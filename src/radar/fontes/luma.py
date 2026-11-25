"""Coletor da Luma: feed iCal → `EventoNormalizado` (D3, D4, D10).

Regras de acesso: só `https://api.lu.ma`, robots.txt lido uma vez por execução (falha
segura), uma requisição por calendário, sem seguir redirecionamento, sem repetição e sem
pausa. Erros ficam isolados por calendário. Nenhuma mensagem de exceção vai para o
resultado: o parser do iCal cita a linha do feed, então `detalhe` leva só o nome da exceção.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any

import httpx
from icalendar import Calendar, Component
from icalendar.prop import vCalAddress, vDDDTypes, vGeo

from radar.fontes.calendarios import CalendarioLuma
from radar.fontes.modelos import (
    FUSO_SP,
    EventoInvalido,
    EventoNormalizado,
    FalhaExtracao,
    MotivoEventoInvalido,
    MotivoFalha,
    ResultadoCalendario,
    Situacao,
    StatusFonte,
)
from radar.fontes.rede import RobotsTxt

ORIGEM = "https://api.lu.ma"

_UID = re.compile(r"((?:evt|calev)-[A-Za-z0-9]+)@events\.lu\.ma")
_ENDERECO_OCULTO = re.compile(r"https://luma\.com/event/evt-[A-Za-z0-9]+")
_URL_HTTPS = re.compile(r"https://\S+")
_PARECE_URL = re.compile(r"https?://", re.IGNORECASE)
# Link da página do evento: luma.com/<slug> sem caminho nem domínio colado depois
# (exclui /event/…, /user/… e `luma.com/home.evil`); pontuação de frase depois é aceita.
_LINK = re.compile(r"https://luma\.com/([A-Za-z0-9_-]+)(?![A-Za-z0-9_/-])(?!\.\S)")
_CAMINHOS_QUE_NAO_SAO_EVENTO = {"event", "user"}
# TENTATIVE é o padrão da Luma em todos os eventos e por isso não é sinal (D10).
_STATUS: dict[str, StatusFonte] = {"CONFIRMED": "confirmado", "CANCELLED": "cancelado"}


class _Invalido(Exception):
    def __init__(self, motivo: MotivoEventoInvalido) -> None:
        super().__init__(motivo)
        self.motivo: MotivoEventoInvalido = motivo


def url_ical(calendario: CalendarioLuma) -> str:
    """URL do feed iCal; `entidade` e `id` já foram validados por `CalendarioLuma`."""
    return f"{ORIGEM}/ics/get?entity={calendario.entidade}&id={calendario.id}"


# --- Leitura do iCal -----------------------------------------------------------------


def _falha(
    slug: str, motivo: MotivoFalha, status: int | None = None, detalhe: str | None = None
) -> ResultadoCalendario:
    return ResultadoCalendario(
        fonte="luma",
        calendario=slug,
        situacao="falha",
        eventos=[],
        eventos_invalidos=[],
        falha=FalhaExtracao(motivo=motivo, status_http=status, detalhe=detalhe),
    )


def _limpar(texto: str, manter: str = "") -> str | None:
    """Tira caracteres de controle (Cc), exceto `manter`; quebras viram espaço se não mantidas.

    Devolve None se não sobrar texto.
    """
    saida = []
    for caractere in texto:
        if caractere in manter or unicodedata.category(caractere) != "Cc":
            saida.append(caractere)
        elif caractere in "\r\n\t":
            saida.append(" ")
    limpo = "".join(saida)
    return limpo if limpo.strip() else None


def _propriedade(evento: Component, nome: str) -> Any:
    """Valor único da propriedade; repetida conta como ausente (ambígua)."""
    valor = evento.get(nome)
    return None if isinstance(valor, list) else valor


def _texto(evento: Component, nome: str, manter: str = "") -> str | None:
    valor = _propriedade(evento, nome)
    return None if valor is None else _limpar(str(valor), manter)


def _horario(valor: Any) -> tuple[datetime, bool]:
    """Converte DTSTART/DTEND em horário de SP; devolve também se é de dia inteiro."""
    if not isinstance(valor, vDDDTypes):
        raise _Invalido("horario_invalido")
    bruto = valor.dt
    if isinstance(bruto, datetime):
        if bruto.tzinfo is None or bruto.utcoffset() is None:
            raise _Invalido("horario_sem_fuso")
        return bruto.astimezone(FUSO_SP), False
    if isinstance(bruto, date):
        # Dia inteiro: 00:00 em São Paulo; o DTEND de dia inteiro é exclusivo (RFC 5545).
        return datetime.combine(bruto, time(0, 0), tzinfo=FUSO_SP), True
    raise _Invalido("horario_invalido")


def _fim(evento: Component, inicio: datetime, dia_inteiro: bool) -> datetime | None:
    """DTEND é opcional: malformado, sem fuso ou incoerente com o início vira None."""
    dtend = _propriedade(evento, "DTEND")
    if dtend is None:
        return None
    try:
        fim, fim_dia_inteiro = _horario(dtend)
    except _Invalido:
        return None
    if fim_dia_inteiro != dia_inteiro or fim <= inicio:
        return None
    return fim


def _local(evento: Component) -> tuple[str | None, str | None, bool]:
    """(local_texto, local_url, endereco_oculto) a partir do LOCATION."""
    local = _texto(evento, "LOCATION")
    if local is None:
        return None, None, False
    local = local.strip()
    if _ENDERECO_OCULTO.fullmatch(local):
        return None, None, True
    if _PARECE_URL.match(local):
        # Só https vira local_url; http ou URL com espaço não viram nem texto nem URL.
        return None, (local if _URL_HTTPS.fullmatch(local) else None), False
    return local, None, False


def _coordenadas(evento: Component) -> tuple[float | None, float | None]:
    geo = _propriedade(evento, "GEO")
    if not isinstance(geo, vGeo):
        return None, None
    latitude, longitude = float(geo.latitude), float(geo.longitude)
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return latitude, longitude
    return None, None


def _link(descricao: str | None) -> str | None:
    if descricao is None:
        return None
    for achado in _LINK.finditer(descricao):
        if achado.group(1) not in _CAMINHOS_QUE_NAO_SAO_EVENTO:
            return achado.group(0)
    return None


def _organizador(evento: Component) -> str | None:
    """Só o CN do ORGANIZER; o endereço (MAILTO) nunca é lido."""
    organizador = _propriedade(evento, "ORGANIZER")
    if not isinstance(organizador, vCalAddress):
        return None
    nome = organizador.params.get("CN")
    if not isinstance(nome, str):
        return None
    nome = _limpar(nome)
    if nome is None or "@" in nome:
        return None
    return nome.strip()


def _status(evento: Component) -> StatusFonte:
    status = _texto(evento, "STATUS")
    return _STATUS.get(status.strip().upper(), "desconhecido") if status else "desconhecido"


def _id_fonte(evento: Component) -> str | None:
    uid = _texto(evento, "UID")
    achado = _UID.fullmatch(uid.strip()) if uid else None
    return achado.group(1) if achado else None


def _normalizar(evento: Component, slug: str, id_fonte: str) -> EventoNormalizado:
    titulo = _texto(evento, "SUMMARY")
    if titulo is None:
        raise _Invalido("titulo_ausente")
    dtstart = _propriedade(evento, "DTSTART")
    if dtstart is None:
        raise _Invalido("inicio_ausente")
    inicio, dia_inteiro = _horario(dtstart)
    local_texto, local_url, endereco_oculto = _local(evento)
    latitude, longitude = _coordenadas(evento)
    descricao = _texto(evento, "DESCRIPTION", manter="\n\t")
    # calev- não tem página própria: o link da DESCRIPTION é a página do calendário.
    link = _link(descricao) if id_fonte.startswith("evt-") else local_url

    return EventoNormalizado(
        fonte="luma",
        calendario=slug,
        id_fonte=id_fonte,
        titulo=titulo.strip(),
        inicio=inicio,
        fim=_fim(evento, inicio, dia_inteiro),
        dia_inteiro=dia_inteiro,
        local_texto=local_texto,
        local_url=local_url,
        endereco_oculto=endereco_oculto,
        latitude=latitude,
        longitude=longitude,
        status_fonte=_status(evento),
        link_inscricao=link,
        descricao=descricao,
        organizador=_organizador(evento),
    )


def ler_ical(texto: str, slug: str) -> ResultadoCalendario:
    """Lê um feed iCal; evento inválido é pulado e registrado sem derrubar o feed."""
    texto = texto.removeprefix("\ufeff")
    if not texto.lstrip(" \t\r\n").startswith("BEGIN:VCALENDAR"):
        return _falha(slug, "formato_invalido", detalhe="resposta não é iCal")
    try:
        calendario = Calendar.from_ical(texto)
    except Exception as erro:
        return _falha(slug, "formato_invalido", detalhe=type(erro).__name__)
    if not isinstance(calendario, Calendar) or calendario.name != "VCALENDAR":
        return _falha(slug, "formato_invalido", detalhe="resposta não é iCal")

    eventos: list[EventoNormalizado] = []
    invalidos: list[EventoInvalido] = []
    for componente in calendario.walk("VEVENT"):
        id_fonte = _id_fonte(componente)
        try:
            if id_fonte is None:
                raise _Invalido("uid_ausente_ou_fora_do_padrao")
            eventos.append(_normalizar(componente, slug, id_fonte))
        except _Invalido as erro:
            invalidos.append(EventoInvalido(id_fonte=id_fonte, motivo=erro.motivo))
        except Exception:
            invalidos.append(EventoInvalido(id_fonte=id_fonte, motivo="formato_invalido"))

    situacao: Situacao = "ok" if eventos else "vazio"
    if not eventos and invalidos:
        return ResultadoCalendario(
            fonte="luma",
            calendario=slug,
            situacao="falha",
            eventos=[],
            eventos_invalidos=invalidos,
            falha=FalhaExtracao(
                motivo="formato_invalido",
                status_http=None,
                detalhe=f"todos os {len(invalidos)} eventos são inválidos",
            ),
        )
    return ResultadoCalendario(
        fonte="luma",
        calendario=slug,
        situacao=situacao,
        eventos=eventos,
        eventos_invalidos=invalidos,
        falha=None,
    )


# --- Coleta --------------------------------------------------------------------------


def _coletar_um(
    cliente: httpx.Client, calendario: CalendarioLuma, robots: RobotsTxt
) -> ResultadoCalendario:
    url = url_ical(calendario)
    if not robots.permite(url):
        return _falha(calendario.slug, "bloqueado_robots")
    try:
        resposta = cliente.get(url, follow_redirects=False)
    except httpx.HTTPError as erro:
        return _falha(calendario.slug, "rede", detalhe=type(erro).__name__)
    if 300 <= resposta.status_code < 400:
        return _falha(calendario.slug, "redirecionamento", status=resposta.status_code)
    if resposta.status_code != 200:
        return _falha(calendario.slug, "status_http", status=resposta.status_code)
    return ler_ical(resposta.text, calendario.slug)


def coletar_luma(
    cliente: httpx.Client, calendarios: Sequence[CalendarioLuma]
) -> list[ResultadoCalendario]:
    """Coleta cada calendário isoladamente; um feed quebrado não derruba os outros."""
    if not calendarios:
        return []
    robots = RobotsTxt.ler(cliente, ORIGEM)
    resultados: list[ResultadoCalendario] = []
    for calendario in calendarios:
        try:
            resultado = _coletar_um(cliente, calendario, robots)
        except Exception as erro:
            resultado = _falha(calendario.slug, "formato_invalido", detalhe=type(erro).__name__)
        resultados.append(resultado)
    return resultados
