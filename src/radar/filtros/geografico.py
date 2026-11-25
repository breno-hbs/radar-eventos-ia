"""Filtro geográfico, aplicado antes do LLM (D6, D11; spec §4.3).

Ordem: coordenadas (`GEO`) na caixa da Grande SP → texto do local → modalidade online.
Sem evidência conclusiva, o evento segue como `indefinida`; só `fora` é descartado.
Só os campos de local (`latitude`/`longitude`, `local_texto`, `local_url`) são evidência:
título, descrição e link de inscrição ficam para o classificador.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, model_validator

from radar.fontes.modelos import EventoNormalizado

SituacaoLocal = Literal["sp", "fora", "online", "indefinida"]
EvidenciaLocal = Literal["geo", "texto", "online_url", "online_texto", "nenhuma"]
MotivoDescarteGeografico = Literal["fora_de_sp"]

_EVIDENCIAS_POR_SITUACAO: dict[str, frozenset[str]] = {
    "sp": frozenset({"geo", "texto"}),
    "fora": frozenset({"geo", "texto"}),
    "online": frozenset({"online_url", "online_texto"}),
    "indefinida": frozenset({"nenhuma"}),
}


class CaixaGeografica(BaseModel):
    """Retângulo de latitude/longitude; as bordas contam como dentro."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contem(self, latitude: float, longitude: float) -> bool:
        return (
            self.lat_min <= latitude <= self.lat_max and self.lon_min <= longitude <= self.lon_max
        )


# Caixa que cobre os 39 municípios da Grande SP (D11). Inclui um pouco de área vizinha; um
# polígono exato exigiria mapa e dependência a mais.
CAIXA_GRANDE_SP = CaixaGeografica(lat_min=-24.10, lat_max=-23.15, lon_min=-47.25, lon_max=-45.65)

# Plataformas de transmissão/reunião: vale o domínio exato ou um subdomínio dele.
DOMINIOS_ONLINE: frozenset[str] = frozenset(
    {
        "zoom.us",
        "meet.google.com",
        "teams.microsoft.com",
        "teams.live.com",
        "youtube.com",
        "youtu.be",
        "twitch.tv",
        "streamyard.com",
        "webex.com",
        "discord.gg",
        "discord.com",
    }
)

_UFS = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"
_PADRAO_CEP = r"\d{5}-?\d{3}(?!\d)"
# UF: sigla em maiúsculas depois de "," ou "-", seguida só de fim do texto, de CEP (depois de
# vírgula ou espaço) ou de ", Brasil"/", Brazil". Qualquer outra coisa ("- AL 3",
# "- PA, Prédio A") não é UF.
_UF = re.compile(
    r"[,\-–]\s*("
    + _UFS
    + r")(?=\s*$|(?:\s*,\s*|\s+)"
    + _PADRAO_CEP
    + r"|\s*,\s*(?i:brasil|brazil)\b)"
)

# País estrangeiro: só como último componente depois de vírgula, por nome completo, com
# inicial maiúscula (como aqui) ou tudo em maiúsculas. Sem siglas de duas letras ("US",
# "UK"). Lista não exaustiva: país desconhecido só deixa o texto inconclusivo.
_PAISES_ESTRANGEIROS = (
    "USA",
    "EUA",
    "United States",
    "United States of America",
    "Estados Unidos",
    "Canada",
    "Mexico",
    "Argentina",
    "Chile",
    "Uruguay",
    "Uruguai",
    "Paraguay",
    "Paraguai",
    "Peru",
    "Colombia",
    "Portugal",
    "Spain",
    "Espanha",
    "Espana",
    "France",
    "Franca",
    "Germany",
    "Alemanha",
    "Deutschland",
    "Italy",
    "Italia",
    "United Kingdom",
    "Reino Unido",
    "England",
    "Ireland",
    "Irlanda",
    "Netherlands",
    "The Netherlands",
    "Holanda",
    "Belgium",
    "Belgica",
    "Switzerland",
    "Suica",
    "Austria",
    "Sweden",
    "Suecia",
    "Norway",
    "Noruega",
    "Denmark",
    "Dinamarca",
    "Finland",
    "Finlandia",
    "Poland",
    "Polonia",
    "Serbia",
    "Czechia",
    "Czech Republic",
    "India",
    "China",
    "Japan",
    "Japao",
    "South Korea",
    "Coreia do Sul",
    "Taiwan",
    "Singapore",
    "Singapura",
    "Malaysia",
    "Malasia",
    "Philippines",
    "Filipinas",
    "Indonesia",
    "Vietnam",
    "Viet Nam",
    "Vietna",
    "Thailand",
    "Tailandia",
    "Pakistan",
    "Paquistao",
    "United Arab Emirates",
    "Emirados Arabes Unidos",
    "Israel",
    "Saudi Arabia",
    "Egypt",
    "Egito",
    "Nigeria",
    "Kenya",
    "Quenia",
    "South Africa",
    "Africa do Sul",
    "Zimbabwe",
    "Australia",
    "New Zealand",
    "Nova Zelandia",
)
_PAISES = frozenset(_PAISES_ESTRANGEIROS) | frozenset(p.upper() for p in _PAISES_ESTRANGEIROS)
_CEP = re.compile(r"(?<!\d)(\d{5})-?(\d{3})(?!\d)")
_CEP_MIN, _CEP_MAX = 1_000_000, 9_999_999  # 01000-000 a 09999-999
_SAO_PAULO = re.compile(r"\bsao paulo\b")
_ONLINE_TEXTO = re.compile(r"\b(?:online|virtual|remoto|remota|remote|livestream|webinar)\b")


class Localizacao(BaseModel):
    """Resultado do filtro: situação e a evidência que a decidiu."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    situacao: SituacaoLocal
    evidencia: EvidenciaLocal

    @model_validator(mode="after")
    def _coerencia(self) -> Self:
        if self.evidencia not in _EVIDENCIAS_POR_SITUACAO[self.situacao]:
            raise ValueError("evidência incompatível com a situação")
        return self


class EventoLocalizado(BaseModel):
    """Evento aprovado no filtro geográfico, com a localização decidida."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evento: EventoNormalizado
    localizacao: Localizacao


class DescartadoGeografico(BaseModel):
    """Evento descartado por estar fora da Grande SP; guarda só a identificação."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fonte: Literal["luma"]
    calendario: str
    id_fonte: str
    motivo: MotivoDescarteGeografico


class ResultadoGeografico(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    aprovados: list[EventoLocalizado]
    descartados: list[DescartadoGeografico]


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _por_texto(texto: str) -> SituacaoLocal | None:
    """`sp`, `fora` ou None (inconclusivo). Precedência: UF → país no fim → CEP → São Paulo.

    Na dúvida, None: o evento segue como `indefinida`, nunca `fora`.
    """
    original = _sem_acento(texto).strip()
    normal = original.casefold()
    cep_sp = any(_CEP_MIN <= int(a + b) <= _CEP_MAX for a, b in _CEP.findall(normal))

    ufs = _UF.findall(original)
    if ufs:
        if ufs[-1] == "SP":
            return "sp"
        # Outra UF com CEP da Grande SP é conflito: nunca descarta.
        return None if cep_sp else "fora"
    if "," in original:
        ultimo = original.rsplit(",", 1)[1].strip().rstrip(".;")
        if ultimo in _PAISES:
            return "fora"
    if cep_sp or _SAO_PAULO.search(normal):
        return "sp"
    return None


def _host_online(url: str) -> bool:
    """Host de plataforma online, exato ou subdomínio. URL com barra invertida, espaço,
    caractere de controle ou userinfo nunca conta."""
    if any(c == "\\" or c.isspace() or unicodedata.category(c) == "Cc" for c in url):
        return False
    try:
        partes = urlsplit(url)
        host = partes.hostname
    except ValueError:
        return False
    if not host or "@" in partes.netloc:
        return False
    host = host.rstrip(".")
    return any(host == d or host.endswith("." + d) for d in DOMINIOS_ONLINE)


def classificar_localizacao(evento: EventoNormalizado) -> Localizacao:
    """Aplica a ordem da D6: GEO → texto → online → indefinida."""
    if evento.latitude is not None and evento.longitude is not None:
        dentro = CAIXA_GRANDE_SP.contem(evento.latitude, evento.longitude)
        return Localizacao(situacao="sp" if dentro else "fora", evidencia="geo")

    if evento.local_texto:
        situacao = _por_texto(evento.local_texto)
        if situacao is not None:
            return Localizacao(situacao=situacao, evidencia="texto")

    # Só o host de plataforma de transmissão conta: a URL externa de um `calev-` (partiful,
    # eventbrite, site próprio) não é evidência de evento online.
    if evento.local_url and _host_online(evento.local_url):
        return Localizacao(situacao="online", evidencia="online_url")
    if evento.local_texto and _ONLINE_TEXTO.search(_sem_acento(evento.local_texto).casefold()):
        return Localizacao(situacao="online", evidencia="online_texto")

    return Localizacao(situacao="indefinida", evidencia="nenhuma")


def filtrar_por_localizacao(eventos: Iterable[EventoNormalizado]) -> ResultadoGeografico:
    """Separa aprovados (sp, online, indefinida) e descartados (fora), mantendo a ordem."""
    aprovados: list[EventoLocalizado] = []
    descartados: list[DescartadoGeografico] = []
    for evento in eventos:
        localizacao = classificar_localizacao(evento)
        if localizacao.situacao == "fora":
            descartados.append(
                DescartadoGeografico(
                    fonte=evento.fonte,
                    calendario=evento.calendario,
                    id_fonte=evento.id_fonte,
                    motivo="fora_de_sp",
                )
            )
        else:
            aprovados.append(EventoLocalizado(evento=evento, localizacao=localizacao))
    return ResultadoGeografico(aprovados=aprovados, descartados=descartados)
