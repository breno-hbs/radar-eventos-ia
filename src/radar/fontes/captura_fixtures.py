"""Captura única das fixtures reais da Luma (ticket F1-02, decisão D9).

Baixa os feeds iCal dos calendários curados e da cidade de São Paulo e no máximo
`MAX_PAGINAS_EVENTO` páginas de evento e grava tudo em `<destino>/luma/`:

- só `https` nos hosts `api.lu.ma` e `luma.com`, sem seguir redirecionamentos;
- robots.txt de cada host lido antes da primeira requisição a ele (falha segura);
- pausa de `PAUSA_SEGUNDOS` antes de cada requisição; nada que falhou é repetido;
- páginas reduzidas ao JSON-LD do Event por lista de permissão; e-mails redigidos e
  menções `@perfil` trocadas por `@perfil-redigido`; qualquer outra arroba descarta o arquivo;
- texto livre que sobra (iCal e campos de texto das páginas) passa por revisão humana.

Uso:
    python -m radar.fontes.captura_fixtures tests/fixtures
    python -m radar.fontes.captura_fixtures --verificar tests/fixtures
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

CALENDARIOS_LUMA: dict[str, str] = {
    "ai-collective": "cal-E74MDlDKBaeAwXK",
    "codex-sp": "cal-sAAnGigXnTQWpw6",
    "vibe-space": "cal-s9CExmUfS7kMu50",
    "womakerscode": "cal-K9IcbN14WTbkzOs",
    "mug-sp": "cal-HtbK5aYvR5B2WAu",
}
CIDADE_LUMA: tuple[str, str] = ("cidade-sp", "discplace-AQZnCu9wl4LmOIp")
PAUSA_SEGUNDOS = 3
MAX_PAGINAS_EVENTO = 2
HOSTS_PERMITIDOS = frozenset({"api.lu.ma", "luma.com"})

# O robots.txt casa o User-agent pelo token de produto, então ele vem primeiro.
USER_AGENT = "radar-eventos-ia/0.1 (+https://github.com/breno-hbs/radar-eventos-ia)"

EMAIL_REDIGIDO = "redigido@exemplo.invalid"
EMAIL_REDIGIDO_URL = "redigido%40exemplo.invalid"
PERFIL_REDIGIDO = "@perfil-redigido"

# Endereço de e-mail, inclusive a forma codificada em URL (`%40`). O começo é amplo de
# propósito (qualquer posição); o escape `\n`/`\N` do iCal é tratado em `_dividir`.
_PADRAO_EMAIL = re.compile(r"[\w.%+-]+(@|%40)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Únicos endereços preservados: os UIDs da Luma, `evt-<id>` e `calev-<id>` (D4).
_UID_LUMA = re.compile(r"(?:evt|calev)-[A-Za-z0-9]+@events\.lu\.ma")
# Escapes do iCal que terminam em letra (`\n`, `\N`, `\t`) e podem colar no endereço.
_ESCAPES_COM_LETRA = "nNt"
# Menção de rede social (`@perfil`, não e-mail): o `@` vem no início, depois de espaço, de um
# escape do iCal (`\n`, `\N`, `\t`, `\\`, `\;`, `\,`; um `\` solto não conta) ou de
# `/ ( [ { " ' > , ; :`; o perfil tem 1 a 30 de `[A-Za-z0-9_.]`, sem começar nem terminar em
# ponto, e não pode continuar com outro caractere de palavra (inclusive acentuado) nem `·`.
# As chaves JSON-LD `"@context"`/`"@type"` não são menções.
_PADRAO_MENCAO = re.compile(
    r"(?:(?<=\\[nNt])|(?<=\\\\)|(?<=[\s/(\[{\"'>,;:])|^)"
    r"@(?!(?:context|type)\")"
    r"[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?"
    # Continuação com `\w` Unicode (letra acentuada etc.) ou `·` impede a redação parcial.
    r"(?![\w@%·-]|\.[\w·])"
)

# Checagem bruta (independente de `_PADRAO_EMAIL`): qualquer forma de arroba é suspeita.
_FORMAS_DE_ARROBA = re.compile(r"%2540|%40|&#64;|&#x40;|\uff20|@", re.IGNORECASE)
# O que a checagem bruta aceita: UIDs da Luma e marcadores de redação, desde que não colados
# a restos de um endereço (exceto depois de um escape do iCal), as chaves do JSON-LD, o
# marcador de perfil solto e o `@` isolado.
_INICIO_ISOLADO = r"(?:(?<=\\[nNt])|(?<![\w.%+-]))"
_EXCECOES_CHECAGEM = re.compile(
    _INICIO_ISOLADO
    + r"(?:(?:evt|calev)-[A-Za-z0-9]+@events\.lu\.ma(?![\w-]|\.\w)"
    + r"|redigido@exemplo\.invalid|redigido%40exemplo\.invalid)"
    + r"|\"@context\"|\"@type\""
    + r"|(?:(?<=\\[nNt;,])|(?<=\\\\)|(?<![\w.%+\\·-]))@perfil-redigido"
    + r"(?![\w·-]|\.[\w·])"
)
# `@` isolado, usado como "em" ("Frontier Tower @ Spaceship"): antes, início, espaço em
# branco ou escape `\n`/`\N`/`\t`; depois, espaço em branco, fim ou esses escapes. É avaliado
# depois de descontar as exceções acima (para `@ redigido@exemplo.invalid` não contar) e só é
# aceito se o que vem depois não parecer domínio:
_ESPACO = r"(?:\s|\\[nNt])"
_ARROBA_ISOLADA = re.compile(
    r"(?:(?<!\S)|(?<=\\[nNt]))@(?=\s|$|\\[nNt])"
    # ...nem `palavra . palavra`, com espaços (inclusive NBSP, tab, quebra) e escapes em volta;
    + rf"(?!{_ESPACO}*\w+{_ESPACO}*\.{_ESPACO}*\w)"
    # ...nem um próximo token (até espaço real ou escape) com `\w.\w` em qualquer posição,
    # mesmo cercado de pontuação: `"gmail.com"`, `(gmail.com)`, `https://gmail.com`.
    + rf"(?!{_ESPACO}*(?:(?!{_ESPACO}).)*?\w\.\w)"
)
# Normalização antes da checagem: dobra de linha da RFC 5545 e formatação invisível (Cf).
_DOBRA_RFC5545 = re.compile(r"\r?\n[ \t]")
_TRECHO = 20
# Link canônico de evento: `https://luma.com/<slug>` sem subcaminho.
_PADRAO_LINK = re.compile(r"(?<![\w./:-])https://luma\.com/[\w-]+(?![\w/.-])")
_CAMINHOS_RESERVADOS = {"event", "user"}
_PADRAO_LD_JSON = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)

# Lista de permissão da página minimizada: só o necessário para data, local e preço (D4, D9).
_TEXTO = "texto"
_PERMITIDO_ENDERECO: dict[str, Any] = dict.fromkeys(
    ("@type", "streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"),
    _TEXTO,
)
_PERMITIDO_LOCAL: dict[str, Any] = {"@type": _TEXTO, "name": _TEXTO, "address": _PERMITIDO_ENDERECO}
_PERMITIDO_OFERTA: dict[str, Any] = dict.fromkeys(
    ("@type", "name", "price", "priceCurrency", "availability", "url"), _TEXTO
)
_PERMITIDO_EVENTO: dict[str, Any] = {
    **dict.fromkeys(
        (
            "@context",
            "@type",
            "name",
            "url",
            "startDate",
            "endDate",
            "eventStatus",
            "eventAttendanceMode",
        ),
        _TEXTO,
    ),
    "location": _PERMITIDO_LOCAL,
    "offers": _PERMITIDO_OFERTA,
}


class RelatorioCaptura(BaseModel):
    """Resultado de uma captura; gravado em `luma/captura.json`."""

    feeds_ok: list[str] = Field(default_factory=list)
    # slug do feed → status HTTP da resposta, ou None se não houve resposta.
    feeds_falha: dict[str, int | None] = Field(default_factory=dict)
    paginas: list[str] = Field(default_factory=list)
    # URLs não requisitadas por causa do robots.txt (ou por ele não ter sido lido).
    bloqueadas_robots: list[str] = Field(default_factory=list)
    # Arquivos não salvos porque a checagem bruta achou arroba suspeita depois da redação.
    descartados_por_dados_pessoais: list[str] = Field(default_factory=list)
    capturado_em: datetime


def url_ics(entidade: str, ident: str) -> str:
    return f"https://api.lu.ma/ics/get?entity={entidade}&id={ident}"


# --- E-mails ------------------------------------------------------------------------


def _dividir(texto: str, achado: re.Match[str]) -> tuple[str, str]:
    """Separa o escape do iCal colado no endereço (`\\n`, `\\N`, `\\t`), que fica."""
    bruto = achado.group(0)
    inicio = achado.start()
    if inicio > 0 and texto[inicio - 1] == "\\" and bruto[0] in _ESCAPES_COM_LETRA:
        return bruto[0], bruto[1:]
    return "", bruto


def redigir_emails(texto: str) -> str:
    """Troca e-mails e menções de rede social por marcadores.

    E-mail vira `EMAIL_REDIGIDO` (ou `EMAIL_REDIGIDO_URL`, se codificado), exceto os UIDs
    `evt-<id>@events.lu.ma` e `calev-<id>@events.lu.ma` da Luma (D4). Depois, menção
    `@perfil` vira `PERFIL_REDIGIDO`. `@` isolado ("Tower @ Spaceship") não é alterado.
    """

    def _trocar(achado: re.Match[str]) -> str:
        escape, email = _dividir(texto, achado)
        if _UID_LUMA.fullmatch(email) or email.startswith(("@", "%40")):
            # UID preservado; `\n@perfil` não é e-mail (o `n` é do escape) e fica para a
            # redação de menções.
            return achado.group(0)
        marcador = EMAIL_REDIGIDO if achado.group(1) == "@" else EMAIL_REDIGIDO_URL
        return escape + marcador

    sem_emails = _PADRAO_EMAIL.sub(_trocar, texto)
    return _PADRAO_MENCAO.sub(PERFIL_REDIGIDO, sem_emails)


def encontrar_emails(texto: str) -> list[str]:
    """Checagem bruta: um trecho por arroba suspeita que sobrou no texto.

    Independente da regex de redação: conta `@`, `%40`, `%2540`, `&#64;`, `&#x40;` e `＠`
    (inclusive menções `@perfil` não redigidas), exceto os UIDs `evt-`/`calev-<id>@events.lu.ma`
    soltos, os marcadores de redação (inclusive `@perfil-redigido` solto), as chaves
    `"@context"`/`"@type"` e o `@` isolado entre espaços ou escapes do iCal que não é seguido
    de algo com cara de domínio. Antes, o texto é desdobrado (RFC 5545) e perde os caracteres
    Unicode de categoria Cf. Os trechos nunca devem ir para log público.
    """
    normalizado = _normalizar_para_checagem(texto)

    def _apagar(achado: re.Match[str]) -> str:
        return " " * len(achado.group(0))

    limpo = _EXCECOES_CHECAGEM.sub(_apagar, normalizado)
    limpo = _ARROBA_ISOLADA.sub(_apagar, limpo)
    return [
        normalizado[max(achado.start() - _TRECHO, 0) : achado.end() + _TRECHO]
        for achado in _FORMAS_DE_ARROBA.finditer(limpo)
    ]


def _normalizar_para_checagem(texto: str) -> str:
    """Desdobra linhas (RFC 5545) e remove caracteres Unicode de categoria Cf."""
    desdobrado = _DOBRA_RFC5545.sub("", texto)
    return "".join(c for c in desdobrado if unicodedata.category(c) != "Cf")


# --- iCal e páginas -------------------------------------------------------------------


def _desdobrar(ics: str) -> list[str]:
    """Desfaz a dobra de linhas da RFC 5545 (quebra seguida de espaço ou tab)."""
    return re.sub(r"\r?\n[ \t]", "", ics).splitlines()


def _descricoes_de_vevent(ics: str) -> list[str]:
    descricoes: list[str] = []
    dentro = False
    for linha in _desdobrar(ics):
        maiuscula = linha.upper()
        if maiuscula == "BEGIN:VEVENT":
            dentro = True
        elif maiuscula == "END:VEVENT":
            dentro = False
        elif dentro and re.match(r"DESCRIPTION[;:]", maiuscula):
            descricoes.append(linha.split(":", 1)[1] if ":" in linha else "")
    return descricoes


def links_de_evento(ics: str) -> list[str]:
    """Links `https://luma.com/<slug>` das DESCRIPTION dos VEVENT, em ordem e sem repetir."""
    links: list[str] = []
    for descricao in _descricoes_de_vevent(ics):
        for link in _PADRAO_LINK.findall(descricao):
            slug = link.rsplit("/", 1)[1]
            if slug not in _CAMINHOS_RESERVADOS and link not in links:
                links.append(link)
    return links


def escolher_paginas(feeds: Mapping[str, str], maximo: int) -> list[str]:
    """Primeiros `maximo` links únicos, seguindo a ordem dos feeds."""
    escolhidos: list[str] = []
    for ics in feeds.values():
        for link in links_de_evento(ics):
            if len(escolhidos) >= maximo:
                return escolhidos
            if link not in escolhidos:
                escolhidos.append(link)
    return escolhidos


def _e_evento(dados: Any) -> bool:
    if not isinstance(dados, dict):
        return False
    tipo = dados.get("@type")
    tipos = tipo if isinstance(tipo, list) else [tipo]
    return any(isinstance(t, str) and t.endswith("Event") for t in tipos)


def _filtrar(valor: Any, permitido: Any) -> Any:
    """Aplica a lista de permissão; devolve None para o que não deve ficar."""
    if permitido == _TEXTO and isinstance(valor, str | int | float | bool):
        return valor
    if isinstance(valor, list):
        itens = [_filtrar(item, permitido) for item in valor]
        return [item for item in itens if item is not None] or None
    if isinstance(valor, str) and (
        permitido is _PERMITIDO_ENDERECO or permitido is _PERMITIDO_LOCAL
    ):
        return valor  # local ou endereço em texto livre
    if isinstance(valor, dict) and isinstance(permitido, dict):
        filtrado = {}
        for chave, sub in permitido.items():
            if chave in valor:
                resultado = _filtrar(valor[chave], sub)
                if resultado is not None:
                    filtrado[chave] = resultado
        return filtrado
    return None


def minimizar_pagina(html: str) -> str:
    """Só o primeiro bloco JSON-LD de Event, reduzido pela lista de permissão.

    Mantém nome, URL, datas, status, modalidade, local (nome e endereço) e ofertas (nome,
    preço, moeda, disponibilidade e URL). Devolve "" se a página não tem esse bloco.
    """
    for bloco in _PADRAO_LD_JSON.findall(html):
        try:
            dados = json.loads(bloco)
        except ValueError:
            continue
        candidatos = dados if isinstance(dados, list) else [dados]
        for candidato in candidatos:
            if _e_evento(candidato):
                evento = _filtrar(candidato, _PERMITIDO_EVENTO)
                corpo = json.dumps(evento, ensure_ascii=False).replace("</", "<\\/")
                return f'<script type="application/ld+json">{corpo}</script>\n'
    return ""


def _e_ical(texto: str) -> bool:
    return texto.lstrip("﻿ \t\r\n").upper().startswith("BEGIN:VCALENDAR")


# --- Rede -----------------------------------------------------------------------------


def _url_permitida(url: str) -> bool:
    try:
        destino = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return destino.scheme == "https" and destino.host in HOSTS_PERMITIDOS


def _baixar(
    cliente: httpx.Client, url: str, dormir: Callable[[float], None]
) -> tuple[int | None, str | None]:
    """Uma única tentativa, precedida da pausa, sem seguir redirecionamento."""
    dormir(PAUSA_SEGUNDOS)
    try:
        resposta = cliente.get(url, follow_redirects=False)
    except httpx.HTTPError:
        return None, None
    return resposta.status_code, resposta.text


class _Robots:
    """robots.txt por host, buscado na primeira vez que o host é usado.

    200 → regras do arquivo; 404/410 → tudo liberado; qualquer outra coisa (3xx, 401,
    403, 5xx, erro de rede) → host bloqueado (falha segura).
    """

    def __init__(self, cliente: httpx.Client, dormir: Callable[[float], None]) -> None:
        self._cliente = cliente
        self._dormir = dormir
        self._por_host: dict[str, RobotFileParser | None] = {}

    def permite(self, url: str) -> bool:
        host = httpx.URL(url).host
        if host not in self._por_host:
            self._por_host[host] = self._ler(host)
        regras = self._por_host[host]
        return regras is not None and regras.can_fetch(USER_AGENT, url)

    def _ler(self, host: str) -> RobotFileParser | None:
        status, corpo = _baixar(self._cliente, f"https://{host}/robots.txt", self._dormir)
        regras = RobotFileParser()
        if status in (404, 410):
            regras.allow_all = True
        elif status == 200 and corpo is not None:
            regras.parse(corpo.splitlines())
        else:
            return None
        regras.modified()
        return regras


def _gravar(caminho: Path, texto: str) -> None:
    # newline="" preserva o CRLF do iCal.
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        arquivo.write(texto)


def capturar(
    destino: Path,
    cliente: httpx.Client,
    dormir: Callable[[float], None] = time.sleep,
) -> RelatorioCaptura:
    """Baixa feeds e páginas, minimiza, redige e-mails e grava tudo em `destino/luma/`.

    Antes de gravar, cada arquivo passa pela checagem bruta (`encontrar_emails`); se sobrar
    qualquer arroba suspeita, o arquivo inteiro é descartado e listado no relatório.
    """
    pasta = Path(destino) / "luma"
    pasta.mkdir(parents=True, exist_ok=True)
    robots = _Robots(cliente, dormir)

    alvos = [(slug, url_ics("calendar", ident)) for slug, ident in CALENDARIOS_LUMA.items()]
    alvos.append((CIDADE_LUMA[0], url_ics("discover", CIDADE_LUMA[1])))

    feeds_ok: list[str] = []
    feeds_falha: dict[str, int | None] = {}
    bloqueadas: list[str] = []
    descartados: list[str] = []
    feeds: dict[str, str] = {}
    for slug, url in alvos:
        if not robots.permite(url):
            bloqueadas.append(url)
            continue
        status, corpo = _baixar(cliente, url, dormir)
        if status == 200 and corpo is not None and _e_ical(corpo):
            texto = redigir_emails(corpo)
            if encontrar_emails(texto):
                descartados.append(f"{slug}.ics")
                continue
            _gravar(pasta / f"{slug}.ics", texto)
            feeds[slug] = texto
            feeds_ok.append(slug)
        else:
            feeds_falha[slug] = status

    paginas: list[str] = []
    for link in escolher_paginas(feeds, MAX_PAGINAS_EVENTO):
        if not _url_permitida(link):
            continue
        if not robots.permite(link):
            bloqueadas.append(link)
            continue
        status, corpo = _baixar(cliente, link, dormir)
        if status != 200 or corpo is None:
            continue
        minima = minimizar_pagina(corpo)
        if not minima:
            continue
        nome = f"pagina_{len(paginas) + 1}.html"
        texto = redigir_emails(minima)
        if encontrar_emails(texto):
            descartados.append(nome)
            continue
        paginas.append(link)
        _gravar(pasta / nome, texto)

    relatorio = RelatorioCaptura(
        feeds_ok=feeds_ok,
        feeds_falha=feeds_falha,
        paginas=paginas,
        bloqueadas_robots=bloqueadas,
        descartados_por_dados_pessoais=descartados,
        capturado_em=datetime.now(UTC),
    )
    _gravar(pasta / "captura.json", relatorio.model_dump_json(indent=2) + "\n")
    return relatorio


# --- Linha de comando -----------------------------------------------------------------


def _verificar(pasta: Path) -> int:
    """Procura e-mails em `<pasta>/luma/`. Imprime só nomes de arquivo (o log é público)."""
    luma = pasta / "luma"
    if not luma.is_dir():
        print(f"Pasta não encontrada: {luma}", file=sys.stderr)
        return 2
    com_email = 0
    for arquivo in sorted(p for p in luma.rglob("*") if p.is_file()):
        achados = encontrar_emails(arquivo.read_text(encoding="utf-8", errors="replace"))
        if achados:
            com_email += 1
            relativo = arquivo.relative_to(pasta)
            mensagem = f"Possível e-mail em {relativo} ({len(achados)} ocorrência(s))"
            print(mensagem, file=sys.stderr)
    if com_email:
        print(f"{com_email} arquivo(s) com e-mail; nada deve ser publicado.", file=sys.stderr)
        return 1
    print("Nenhum e-mail encontrado.")
    return 0


def _imprimir_resumo(relatorio: RelatorioCaptura) -> None:
    falhas = ", ".join(
        f"{slug} ({'sem resposta' if status is None else status})"
        for slug, status in relatorio.feeds_falha.items()
    )
    print(f"Feeds ok ({len(relatorio.feeds_ok)}): {', '.join(relatorio.feeds_ok) or '-'}")
    print(f"Falhas ({len(relatorio.feeds_falha)}): {falhas or '-'}")
    print(f"Páginas ({len(relatorio.paginas)}): {', '.join(relatorio.paginas) or '-'}")
    bloqueadas = ", ".join(relatorio.bloqueadas_robots) or "-"
    print(f"Bloqueadas pelo robots.txt ({len(relatorio.bloqueadas_robots)}): {bloqueadas}")
    descartados = ", ".join(relatorio.descartados_por_dados_pessoais) or "-"
    quantidade = len(relatorio.descartados_por_dados_pessoais)
    print(f"Descartados por dados pessoais ({quantidade}): {descartados}")


def main(argv: Sequence[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if len(argumentos) == 2 and argumentos[0] == "--verificar":
        return _verificar(Path(argumentos[1]))
    if len(argumentos) != 1 or argumentos[0].startswith("-"):
        print(
            "uso: python -m radar.fontes.captura_fixtures [--verificar] <pasta-destino>",
            file=sys.stderr,
        )
        return 2
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=False, timeout=30
    ) as cliente:
        relatorio = capturar(Path(argumentos[0]), cliente)
    _imprimir_resumo(relatorio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
