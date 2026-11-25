"""Testes do ticket F1-02 — captura das fixtures reais da Luma.

A lógica fica em `radar.fontes.captura_fixtures`; o workflow manual
`.github/workflows/capturar-fixtures.yml` só a executa. Nenhum teste acessa a internet:
as respostas HTTP vêm de `httpx.MockTransport` e de arquivos sintéticos em
`tests/fixtures/captura/` (e-mails só em domínios reservados `example.*`).

O detector de e-mail destes testes (`_vazamentos`) é deliberadamente independente da
implementação e mais amplo que ela: qualquer coisa com forma de e-mail, inclusive a forma
codificada `%40`, conta como vazamento, exceto o UID `evt-<id>@events.lu.ma` da Luma (D4).
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import yaml

if TYPE_CHECKING:
    from types import ModuleType

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = RAIZ / "tests" / "fixtures" / "captura"
REDIGIDO = "redigido@exemplo.invalid"
# Forma escolhida para e-mail codificado em URL: o `%40` é mantido.
REDIGIDO_URL = "redigido%40exemplo.invalid"

CALENDARIOS_ESPERADOS = {
    "ai-collective": "cal-E74MDlDKBaeAwXK",
    "codex-sp": "cal-sAAnGigXnTQWpw6",
    "vibe-space": "cal-s9CExmUfS7kMu50",
    "womakerscode": "cal-K9IcbN14WTbkzOs",
    "mug-sp": "cal-HtbK5aYvR5B2WAu",
}
CIDADE_ESPERADA = ("cidade-sp", "discplace-AQZnCu9wl4LmOIp")
TODOS_OS_FEEDS = [*CALENDARIOS_ESPERADOS, CIDADE_ESPERADA[0]]

ROBOTS_API = "https://api.lu.ma/robots.txt"
ROBOTS_LUMA = "https://luma.com/robots.txt"
HOSTS_PERMITIDOS = {"api.lu.ma", "luma.com"}

# Links esperados nas fixtures sintéticas (ordem de aparição).
LINKS_FEED_A = ["https://luma.com/meetup-ia-sp", "https://luma.com/x7k2p9q1"]
LINKS_FEED_B = ["https://luma.com/x7k2p9q1", "https://luma.com/hackathon-genai"]

# Dados pessoais que estão na página sintética e não podem sobreviver à minimização.
NOMES_NA_PAGINA = (
    "Maria Fulana de Tal",
    "Joao Palestrante Exemplo",
    "Joao Patrocinador Exemplo",
    "Ana Participante Exemplo",
)
# Outros rastros que a lista de permissão precisa eliminar.
RASTROS_NA_PAGINA = ("+55 11", "images.example", "GeoCoordinates", "validFrom", "ratingValue")

# Event esperado depois da minimização (lista de permissão) de `pagina_evento.html`.
EVENTO_MINIMIZADO = {
    "@context": "https://schema.org",
    "@type": "Event",
    "name": "Meetup de IA em SP",
    "url": "https://luma.com/meetup-ia-sp",
    "startDate": "2026-12-10T19:00:00-03:00",
    "endDate": "2026-12-10T22:00:00-03:00",
    "eventStatus": "https://schema.org/EventScheduled",
    "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
    "location": {
        "@type": "Place",
        "name": "Espaco Teste",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Av. Paulista 1000",
            "addressLocality": "Sao Paulo",
            "addressRegion": "SP",
            "postalCode": "01310-100",
            "addressCountry": "BR",
        },
    },
    "offers": [
        {
            "@type": "Offer",
            "name": "Ingresso geral",
            "price": "0",
            "priceCurrency": "BRL",
            "availability": "https://schema.org/InStock",
            "url": "https://luma.com/meetup-ia-sp",
        },
        {"@type": "Offer", "name": "Apoiador", "price": "50", "priceCurrency": "BRL"},
    ],
}

HANDLES_FEED_MENCOES = (
    "aicollective_sp",
    "joao.silva",
    "vibe.space",
    "ciclana_dev",
    "fulano",
    "beltrana",
    "perfil_x",
    "autor_medium",
    "radar_ia",
)

FIXTURES_COM_EMAIL = ["feed_a.ics", "feed_b.ics", "pagina_evento.html", "nao_ical.html"]
TODAS_AS_FIXTURES = [
    *FIXTURES_COM_EMAIL,
    "feed_hosts_estranhos.ics",
    "feed_sem_links.ics",
    "feed_mencoes.ics",
]


# --- Detector independente de e-mail ---------------------------------------------

_EMAIL_AMPLO = re.compile(
    r"[A-Za-z0-9._%+-]+(?:@|%40)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
# UIDs da Luma: `evt-<id>@events.lu.ma` e `calev-<id>@events.lu.ma`.
_EXCECAO_UID_LUMA = re.compile(r"(?:evt|calev)-[A-Za-z0-9]+@events\.lu\.ma")
# Mesmo UID, mas só quando não está colado a outro texto (para a checagem bruta).
_UID_LUMA_SOLTO = re.compile(
    r"(?<![\w.%+\-])(?:evt|calev)-[A-Za-z0-9]+@events\.lu\.ma(?![\w\-]|\.\w)"
)
ESCAPES_ICAL = ("\\n", "\\N", "\\t", "\\\\", "\\;", "\\,")
_CARACTERES_DE_LOCAL = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-")


def _vazamentos(texto: str) -> list[str]:
    """Tudo com forma de e-mail que não é o marcador de redação nem o UID da Luma.

    Também acusa marcador "colado" a restos de um endereço (ex.: `fulano.redigido@…`),
    exceto quando o que vem antes é um escape do iCal (`\\n`, `\\t`, `\\\\`...).
    """
    achados: list[str] = []
    for marcador in (REDIGIDO, REDIGIDO_URL):
        inicio = texto.find(marcador)
        while inicio != -1:
            anterior = texto[inicio - 1] if inicio > 0 else ""
            escape = texto[max(inicio - 2, 0) : inicio] in ESCAPES_ICAL
            if anterior in _CARACTERES_DE_LOCAL and not escape:
                achados.append(texto[max(inicio - 20, 0) : inicio + len(marcador)])
            inicio = texto.find(marcador, inicio + 1)
        texto = texto.replace(marcador, " ")
    for achado in _EMAIL_AMPLO.findall(texto):
        if not _EXCECAO_UID_LUMA.fullmatch(achado):
            achados.append(achado)
    return achados


def test_detector_dos_testes_e_amplo() -> None:
    """Sanidade do próprio detector, para que os outros testes signifiquem algo."""
    assert _vazamentos("a fulano@example.com b")
    assert _vazamentos("x?e=fulano%40example.com")
    assert _vazamentos("joao.silva@events.lu.ma")
    assert _vazamentos("fulano.redigido@exemplo.invalid")
    assert not _vazamentos("UID:evt-AAAA1111@events.lu.ma")
    assert not _vazamentos(f"Contato:\\n{REDIGIDO} e {REDIGIDO_URL}")


PERFIL_REDIGIDO = "@perfil-redigido"

# Checagem bruta, independente de qualquer regex de e-mail: qualquer forma de arroba conta,
# exceto o UID da Luma, os marcadores de redação, as chaves JSON-LD `"@context"`/`"@type"`
# (que a página minimizada sempre contém) e o marcador de menção `@perfil-redigido` quando
# não está colado a resto de identificador.
_FORMAS_DE_ARROBA = re.compile(r"@|%40|%2540|&#64;|&#x40;|\uff20", re.IGNORECASE)
_EXCECOES_LITERAIS = (REDIGIDO, REDIGIDO_URL, '"@context"', '"@type"')
_PERFIL_SOLTO = re.compile(
    r"(?:(?<=\\[nNt\\;,])|(?<![\w.%+\-\\·]))@perfil-redigido(?![\w\-·]|\.\w)"
)


# `@` isolado, usado como "em" ("Frontier Tower @ Spaceship"): antes, início/espaço/escape
# `\n` `\N` `\t`; depois, espaço/fim/esses escapes. Só o `@` literal.
_ARROBA_ISOLADA = re.compile(
    r"(?:(?<![^\s])|(?<=\\[nNt]))@(?=\s|$|\\[nNt])"
    # ...desde que o que vem depois não pareça domínio (`palavra . palavra`, com espaços,
    # NBSP e escapes do iCal permitidos em volta do ponto).
    r"(?!(?:\s|\\[nNt])*\w+(?:\s|\\[nNt])*\.(?:\s|\\[nNt])*\w)"
    # ...e desde que o próximo token (até espaço real ou escape do iCal) não tenha `\w.\w`
    # em nenhuma posição, mesmo cercado de pontuação: `"gmail.com"`, `(gmail.com)`, URL.
    r"(?!(?:\s|\\[nNt])*(?:(?!\s|\\[nNt]).)*?\w\.\w)"
)
_DOBRA_RFC5545 = re.compile(r"\r?\n[ \t]")


def _sem_formatacao_invisivel(texto: str) -> str:
    """Desdobra linhas (RFC 5545) e remove caracteres Unicode de categoria Cf."""
    texto = _DOBRA_RFC5545.sub("", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Cf")


def _arrobas_suspeitas(texto: str) -> list[str]:
    limpo = _UID_LUMA_SOLTO.sub(" ", _sem_formatacao_invisivel(texto))
    for excecao in _EXCECOES_LITERAIS:
        limpo = limpo.replace(excecao, " ")
    limpo = _PERFIL_SOLTO.sub(" ", limpo)
    limpo = _ARROBA_ISOLADA.sub(" ", limpo)
    return _FORMAS_DE_ARROBA.findall(limpo)


def test_checagem_bruta_dos_testes() -> None:
    assert _arrobas_suspeitas("@usuario")
    assert _arrobas_suspeitas("joao&#X40;example.com")
    assert _arrobas_suspeitas("joao\uff20example.com")
    assert _arrobas_suspeitas("@perfil-redigidox")
    assert _arrobas_suspeitas("x@perfil-redigido")
    assert _arrobas_suspeitas("@perfil-redigidoé")
    assert _arrobas_suspeitas("@perfil-redigido·x")
    assert _arrobas_suspeitas("a\\@perfil-redigido")
    assert not _arrobas_suspeitas(f'UID:evt-A1@events.lu.ma {REDIGIDO} {{"@type":"Event"}}')
    assert not _arrobas_suspeitas("siga @perfil-redigido. (@perfil-redigido)")
    assert not _arrobas_suspeitas("Hosted by\\n@perfil-redigido https://x.com/@perfil-redigido")
    assert not _arrobas_suspeitas("UID:calev-A1@events.lu.ma")
    assert _arrobas_suspeitas("fulano.calev-1@events.lu.ma")
    assert _arrobas_suspeitas("UID:calevx-1@events.lu.ma")
    assert not _arrobas_suspeitas("Frontier Tower @ Spaceship\\n@ no inicio e fim @")
    assert _arrobas_suspeitas("x ＠ y")
    assert _arrobas_suspeitas("x &#64; y")
    assert _arrobas_suspeitas("x @y")
    assert _arrobas_suspeitas("joao @ example.com")
    assert _arrobas_suspeitas("joao\\n@\\nexample . com")
    assert _arrobas_suspeitas("joao @ gmail.com")
    assert not _arrobas_suspeitas("Demo Night @ Cubo Itaú. Meetup @ FIAP Paulista")


# --- Utilitários ------------------------------------------------------------------


def _modulo() -> ModuleType:
    from radar.fontes import captura_fixtures

    return captura_fixtures


def _fixture(nome: str) -> str:
    # read_bytes preserva o CRLF dos arquivos iCal.
    return (FIXTURES / nome).read_bytes().decode("utf-8")


def _url_calendario(ident: str) -> str:
    return f"https://api.lu.ma/ics/get?entity=calendar&id={ident}"


def _url_cidade() -> str:
    return f"https://api.lu.ma/ics/get?entity=discover&id={CIDADE_ESPERADA[1]}"


def _url_do_feed(slug: str) -> str:
    if slug == CIDADE_ESPERADA[0]:
        return _url_cidade()
    return _url_calendario(CALENDARIOS_ESPERADOS[slug])


_PADRAO_LD_JSON = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)


def _blocos_ld_json(html: str) -> list[Any]:
    return [json.loads(bloco) for bloco in _PADRAO_LD_JSON.findall(html)]


def _evento_original() -> dict[str, Any]:
    blocos = _blocos_ld_json(_fixture("pagina_evento.html"))
    return next(b for b in blocos if b.get("@type") == "Event")


# --- Constantes -------------------------------------------------------------------


def test_calendarios_luma_tem_slugs_e_ids_da_fase_0() -> None:
    assert _modulo().CALENDARIOS_LUMA == CALENDARIOS_ESPERADOS


def test_calendarios_luma_mantem_ordem_declarada() -> None:
    assert list(_modulo().CALENDARIOS_LUMA) == list(CALENDARIOS_ESPERADOS)


def test_cidade_luma_e_sao_paulo() -> None:
    assert tuple(_modulo().CIDADE_LUMA) == CIDADE_ESPERADA


def test_pausa_entre_requisicoes_e_de_3_segundos() -> None:
    assert _modulo().PAUSA_SEGUNDOS == 3


def test_maximo_de_paginas_de_evento_e_2() -> None:
    assert _modulo().MAX_PAGINAS_EVENTO == 2


def test_user_agent_comeca_com_token_do_radar() -> None:
    """O robots.txt casa o User-agent pelo token de produto (`radar-eventos-ia`)."""
    assert _modulo().USER_AGENT.lower().startswith("radar-eventos-ia")


# --- url_ics ----------------------------------------------------------------------


def test_url_ics_de_calendario() -> None:
    url = _modulo().url_ics("calendar", "cal-sAAnGigXnTQWpw6")
    assert url == "https://api.lu.ma/ics/get?entity=calendar&id=cal-sAAnGigXnTQWpw6"


def test_url_ics_da_cidade() -> None:
    url = _modulo().url_ics("discover", "discplace-AQZnCu9wl4LmOIp")
    assert url == "https://api.lu.ma/ics/get?entity=discover&id=discplace-AQZnCu9wl4LmOIp"


# --- redigir_emails ---------------------------------------------------------------


def test_redigir_emails_troca_email_simples() -> None:
    texto = "Fale com fulano@example.com hoje."
    assert _modulo().redigir_emails(texto) == f"Fale com {REDIGIDO} hoje."


def test_redigir_emails_troca_email_em_mailto() -> None:
    texto = "ORGANIZER;CN=Fulana:mailto:fulana.silva@example.com"
    assert _modulo().redigir_emails(texto) == f"ORGANIZER;CN=Fulana:mailto:{REDIGIDO}"


def test_redigir_emails_troca_mailto_maiusculo() -> None:
    texto = "ORGANIZER;CN=Fulana:MAILTO:fulana@example.com"
    assert _modulo().redigir_emails(texto) == f"ORGANIZER;CN=Fulana:MAILTO:{REDIGIDO}"


@pytest.mark.parametrize(
    "email",
    [
        "a.b.c@example.com",
        "nome+tag@sub.example.org",
        "nome_sobrenome@example.com.br",
        "Maiusculas123@Example.NET",
        "com-hifen@meu-dominio.example",
        "percentual%x@example.com",
    ],
)
def test_redigir_emails_cobre_formatos_comuns(email: str) -> None:
    resultado = _modulo().redigir_emails(f"<{email}>")
    assert resultado == f"<{REDIGIDO}>"


@pytest.mark.parametrize(
    ("antes", "depois"),
    [
        ("<a>fulano@example.com</a>", f"<a>{REDIGIDO}</a>"),
        ("<td>\tfulano@example.com</td>", f"<td>\t{REDIGIDO}</td>"),
        ("linha\rfulano@example.com", f"linha\r{REDIGIDO}"),
        ("linha\nfulano@example.com", f"linha\n{REDIGIDO}"),
        ('{"email":"fulano@example.com"}', f'{{"email":"{REDIGIDO}"}}'),
        ("href='mailto:fulano@example.com'", f"href='mailto:{REDIGIDO}'"),
        ("(fulano@example.com)", f"({REDIGIDO})"),
        ("Contato\\;fulano@example.com", f"Contato\\;{REDIGIDO}"),
        ("Contato\\,fulano@example.com", f"Contato\\,{REDIGIDO}"),
    ],
)
def test_redigir_emails_em_qualquer_contexto(antes: str, depois: str) -> None:
    assert _modulo().redigir_emails(antes) == depois


def test_redigir_emails_forma_codificada_em_url() -> None:
    texto = "https://example.com/form?email=fulano%40example.com&x=1"
    resultado = _modulo().redigir_emails(texto)
    assert resultado == f"https://example.com/form?email={REDIGIDO_URL}&x=1"


def test_redigir_emails_forma_codificada_simples() -> None:
    assert _modulo().redigir_emails("x%40y.com") == REDIGIDO_URL


def test_redigir_emails_troca_todos_os_emails_do_texto() -> None:
    texto = "x@example.com, y@example.org\\nz@example.net"
    resultado = _modulo().redigir_emails(texto)
    assert resultado == f"{REDIGIDO}, {REDIGIDO}\\n{REDIGIDO}"


@pytest.mark.parametrize("escape", ESCAPES_ICAL)
def test_redigir_emails_preserva_escape_do_ical_antes_do_email(escape: str) -> None:
    """Um escape do iCal logo antes do e-mail não faz parte do endereço e é preservado."""
    texto = f"Contato:{escape}joao@x.com"
    assert _modulo().redigir_emails(texto) == f"Contato:{escape}{REDIGIDO}"


def test_redigir_emails_preserva_escape_tab_exemplo_da_revisao() -> None:
    assert _modulo().redigir_emails("\\tjoao@x.com") == f"\\t{REDIGIDO}"


@pytest.mark.parametrize(
    "uid",
    ["UID:evt-AAAA1111@events.lu.ma\r\n", "UID:evt-x7K2p9Q1zz@events.lu.ma"],
)
def test_redigir_emails_preserva_uid_da_luma(uid: str) -> None:
    assert _modulo().redigir_emails(uid) == uid


@pytest.mark.parametrize(
    "email",
    ["joao.silva@events.lu.ma", "Fulano@Events.Lu.Ma", "evt@events.lu.ma", "organizer@lu.ma"],
)
def test_redigir_emails_redige_events_lu_ma_que_nao_e_uid(email: str) -> None:
    """A exceção vale só para `evt-<id>@events.lu.ma`; pessoas nesse domínio são redigidas."""
    assert _modulo().redigir_emails(f"mailto:{email}") == f"mailto:{REDIGIDO}"


# --- redigir_emails: menções de rede social ----------------------------------------


@pytest.mark.parametrize(
    ("antes", "depois"),
    [
        ("siga @radar_ia.", "siga @perfil-redigido."),
        ("(@aicollective)", "(@perfil-redigido)"),
        ("Hosted by\\n@joao.silva", "Hosted by\\n@perfil-redigido"),
        ("Hosted by\n@joao.silva", "Hosted by\n@perfil-redigido"),
        ("w/ @fulano, @ciclana", "w/ @perfil-redigido, @perfil-redigido"),
        ("https://x.com/@fulano", "https://x.com/@perfil-redigido"),
        ("https://medium.com/@autor/post", "https://medium.com/@perfil-redigido/post"),
        ("@inicio do texto", "@perfil-redigido do texto"),
        ("fim com reticencias @fulano...", "fim com reticencias @perfil-redigido..."),
        ("\t@tab_real", "\t@perfil-redigido"),
        ("[@colchete]", "[@perfil-redigido]"),
        ("{@chave}", "{@perfil-redigido}"),
        ('"@aspas"', '"@perfil-redigido"'),
        ("'@apostrofo'", "'@perfil-redigido'"),
        ("<b>@negrito</b>", "<b>@perfil-redigido</b>"),
        ("a,@virgula", "a,@perfil-redigido"),
        ("a;@ponto_e_virgula", "a;@perfil-redigido"),
        ("Instagram:@dois_pontos", "Instagram:@perfil-redigido"),
        ("Co-host:\\t@escape_t", "Co-host:\\t@perfil-redigido"),
        ("Co-host:\\N@escape_n", "Co-host:\\N@perfil-redigido"),
        ("barra\\\\@escape_barra", "barra\\\\@perfil-redigido"),
        ("a\\;@escape_ponto_e_virgula", "a\\;@perfil-redigido"),
        ("a\\,@escape_virgula", "a\\,@perfil-redigido"),
        ("@a", "@perfil-redigido"),
        ("@" + "x" * 30, "@perfil-redigido"),
    ],
)
def test_redigir_emails_redige_mencoes(antes: str, depois: str) -> None:
    assert _modulo().redigir_emails(antes) == depois


@pytest.mark.parametrize(
    ("antes", "depois"),
    [
        ("fale com fulano@example.com", f"fale com {REDIGIDO}"),
        ("(fulano@example.com)", f"({REDIGIDO})"),
        ("Hosted by\\nfulano@example.com", f"Hosted by\\n{REDIGIDO}"),
        ("https://x.com/?e=fulano%40example.com", f"https://x.com/?e={REDIGIDO_URL}"),
    ],
)
def test_redigir_emails_email_continua_virando_marcador_de_email(antes: str, depois: str) -> None:
    """Quando o `@` forma um e-mail, vale a redação de e-mail, não a de menção."""
    assert _modulo().redigir_emails(antes) == depois


@pytest.mark.parametrize(
    "texto",
    [
        "UID:evt-AAAA1111@events.lu.ma",
        '{"@context":"https://schema.org","@type":"Event","name":"X"}',
        REDIGIDO,
        REDIGIDO_URL,
        PERFIL_REDIGIDO,
        f"siga {PERFIL_REDIGIDO}. ({PERFIL_REDIGIDO})",
        "https://medium.com/@perfil-redigido/post",
        "e-mail @ empresa",
        "so arroba @",
        "@. nada",
        "joao&#64;example.com",
    ],
)
def test_redigir_emails_nao_altera_o_que_nao_e_mencao(texto: str) -> None:
    assert _modulo().redigir_emails(texto) == texto


@pytest.mark.parametrize(
    "texto",
    [
        "joao @exémplo.com",
        "@josé.silva",
        "@joão_silva",
        "@ana·silva",
        "Hosted by @josé.silva\\n",
        "(@élan)",
    ],
)
def test_redigir_emails_nao_redige_mencao_pela_metade_com_unicode(texto: str) -> None:
    """Se o handle continua com caractere de palavra Unicode (ou `·`), não há redação parcial:
    o texto fica como está e a checagem bruta acusa."""
    modulo = _modulo()
    resultado = modulo.redigir_emails(texto)
    assert resultado == texto
    assert PERFIL_REDIGIDO not in resultado
    assert modulo.encontrar_emails(resultado) != []


def test_redigir_emails_barra_que_nao_e_escape_nao_inicia_mencao() -> None:
    """`\\@` (barra que não é escape do iCal) não conta como início de menção."""
    modulo = _modulo()
    texto = "@joao.silva\\@ex.com"
    resultado = modulo.redigir_emails(texto)
    assert resultado != f"{PERFIL_REDIGIDO}\\{PERFIL_REDIGIDO}"
    assert resultado.endswith("\\@ex.com")
    assert modulo.encontrar_emails(resultado) != []


# --- UID `calev-` e `@` isolado -----------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "UID:calev-LLLL1234@events.lu.ma",
        "UID:calev-x7K2p9Q1zz@events.lu.ma\r\n",
        "UID:evt-AAAA1111@events.lu.ma\r\nUID:calev-BBBB2222@events.lu.ma",
    ],
)
def test_redigir_emails_preserva_uid_calev(texto: str) -> None:
    assert _modulo().redigir_emails(texto) == texto


@pytest.mark.parametrize(
    "email",
    ["calevx-1@events.lu.ma", "evtx-1@events.lu.ma", "calev@events.lu.ma"],
)
def test_redigir_emails_redige_prefixo_parecido_com_uid(email: str) -> None:
    assert _modulo().redigir_emails(f"mailto:{email}") == f"mailto:{REDIGIDO}"


@pytest.mark.parametrize(
    "texto",
    [
        "Frontier Tower @ Spaceship",
        "Demo Night @ Cubo",
        "LOCATION:Frontier Tower @ Spaceship",
        "@ no início",
        "fim @",
        "linha\\n@ depois do escape",
        "antes do escape @\\nlinha",
        "tab\\t@\\tdepois",
        "Demo Night @\tCubo",
    ],
)
def test_redigir_emails_nao_altera_arroba_isolada(texto: str) -> None:
    assert _modulo().redigir_emails(texto) == texto


def test_redigir_emails_arroba_isolada_nao_protege_email_vizinho() -> None:
    assert _modulo().redigir_emails("@ joao@ex.com") == f"@ {REDIGIDO}"


def test_redigir_emails_mencao_colada_continua_redigida() -> None:
    texto = "Hackathon #2 @Stanford"
    assert _modulo().redigir_emails(texto) == f"Hackathon #2 {PERFIL_REDIGIDO}"


def test_redigir_emails_mencoes_e_idempotente() -> None:
    redigir = _modulo().redigir_emails
    texto = "siga @radar_ia. (@vibe.space) https://x.com/@fulano fulano@example.com"
    uma_vez = redigir(texto)
    assert redigir(uma_vez) == uma_vez
    assert uma_vez == (
        f"siga {PERFIL_REDIGIDO}. ({PERFIL_REDIGIDO}) https://x.com/{PERFIL_REDIGIDO} {REDIGIDO}"
    )


def test_redigir_emails_feed_de_mencoes() -> None:
    original = _fixture("feed_mencoes.ics")
    resultado = _modulo().redigir_emails(original)
    for perfil in HANDLES_FEED_MENCOES:
        assert perfil not in resultado, perfil
    assert "UID:evt-JJJJ9999@events.lu.ma" in resultado
    assert f"Hosted by\\n{PERFIL_REDIGIDO}" in resultado
    assert f"https://medium.com/{PERFIL_REDIGIDO}/post-sobre-ia" in resultado
    assert _arrobas_suspeitas(resultado) == []


def test_redigir_emails_nao_altera_texto_sem_email() -> None:
    texto = "DESCRIPTION:Get up-to-date information at: https://luma.com/meetup-ia-sp\r\n"
    assert _modulo().redigir_emails(texto) == texto


def test_redigir_emails_e_idempotente() -> None:
    redigir = _modulo().redigir_emails
    uma_vez = redigir("contato@example.com e x%40example.org")
    assert redigir(uma_vez) == uma_vez == f"{REDIGIDO} e {REDIGIDO_URL}"


@pytest.mark.parametrize("nome", TODAS_AS_FIXTURES)
def test_redigir_emails_nas_fixtures_nao_deixa_nenhum_email(nome: str) -> None:
    resultado = _modulo().redigir_emails(_fixture(nome))
    assert _vazamentos(resultado) == []


def test_redigir_emails_preserva_o_resto_do_ical() -> None:
    original = _fixture("feed_a.ics")
    resultado = _modulo().redigir_emails(original)
    assert resultado.startswith("BEGIN:VCALENDAR")
    assert resultado.count("BEGIN:VEVENT") == original.count("BEGIN:VEVENT")
    assert "UID:evt-AAAA1111@events.lu.ma" in resultado
    assert "https://luma.com/meetup-ia-sp" in resultado
    assert "\r\n" in resultado


# --- encontrar_emails (checagem bruta) ---------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "x fulano@example.com y",
        "?email=fulano%40example.com&x=1",
        "joao.silva@events.lu.ma",
        "joao&#64;example.com",
        "joao&#x40;example.com",
        "joao&#X40;example.com",
        "joao@café.com.br",
        "foo%2540example.com",
        "joao\uff20example.com",
        "joao@ex_ample.com",
        "@usuario",
        "siga @radar_ia no X",
        "Fulano@Events.Lu.Ma",
        "UID:evt-AAAA1111@events.lu.ma\r\nORGANIZER:mailto:a@example.com",
        f"fulano.{REDIGIDO_URL} e x@y",
    ],
)
def test_encontrar_emails_reporta_qualquer_forma_de_arroba(texto: str) -> None:
    assert _modulo().encontrar_emails(texto) != []


@pytest.mark.parametrize(
    "texto",
    [
        "joao&#64;example.com",
        "joao&#x40;example.com",
        "joao@café.com.br",
        "foo%2540example.com",
        "joao\uff20example.com",
        "joao@ex_ample.com",
        "@usuario",
    ],
)
def test_encontrar_emails_um_trecho_por_ocorrencia(texto: str) -> None:
    assert len(_modulo().encontrar_emails(texto)) == 1


def test_encontrar_emails_ignora_uid_ao_lado_de_arroba_suspeita() -> None:
    achados = _modulo().encontrar_emails("UID:evt-AAAA1111@events.lu.ma\r\nX-SOCIAL:@fulano")
    assert len(achados) == 1
    assert "evt-AAAA1111" not in achados[0]


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "texto sem nada",
        "UID:evt-AAAA1111@events.lu.ma",
        "UID:evt-x7K2p9Q1zz@events.lu.ma\r\nUID:evt-BBBB2222@events.lu.ma",
        REDIGIDO,
        REDIGIDO_URL,
        f"Contato:\\n{REDIGIDO}",
        f"mailto:{REDIGIDO}, ?e={REDIGIDO_URL}",
        '{"@context":"https://schema.org","@type":"Event","name":"X"}',
        PERFIL_REDIGIDO,
        f"siga {PERFIL_REDIGIDO}. ({PERFIL_REDIGIDO})",
        f"Hosted by\\n{PERFIL_REDIGIDO}\\, {PERFIL_REDIGIDO}",
        f"https://medium.com/{PERFIL_REDIGIDO}/post",
        f"Instagram:{PERFIL_REDIGIDO}",
    ],
)
def test_encontrar_emails_ignora_uid_marcadores_e_chaves_json_ld(texto: str) -> None:
    assert _modulo().encontrar_emails(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "<a>fulano@example.com</a>",
        "\tfulano@example.com",
        "Contato:\\nfulano@example.com",
        "x%40y.com",
        "joao&#64;example.com",
        "@usuario",
        "nada aqui",
        f"so o marcador {REDIGIDO}",
        "UID:evt-ZZZ@events.lu.ma",
        '{"@type":"Event"}',
    ],
)
def test_encontrar_emails_concorda_com_checagem_bruta(texto: str) -> None:
    assert bool(_modulo().encontrar_emails(texto)) == bool(_arrobas_suspeitas(texto))


@pytest.mark.parametrize(
    "texto",
    [
        "@perfil-redigidox",
        "x@perfil-redigido",
        "@perfil-redigido_extra",
        "@perfil-redigido-extra",
        f"{PERFIL_REDIGIDO} e @outro_perfil",
        "@perfil-redigidoé",
        "@perfil-redigido·silva",
        "@perfil-redigidoão",
        "a\\@perfil-redigido",
    ],
)
def test_encontrar_emails_reporta_marcador_de_perfil_colado(texto: str) -> None:
    assert _modulo().encontrar_emails(texto) != []


@pytest.mark.parametrize(
    "texto",
    [
        "UID:calev-LLLL1234@events.lu.ma",
        "UID:evt-AAAA1111@events.lu.ma\r\nUID:calev-BBBB2222@events.lu.ma",
        "Frontier Tower @ Spaceship",
        "Demo Night @ Cubo",
        "LOCATION:Frontier Tower @ Spaceship",
        "@ no início",
        "fim @",
        "linha\\n@ depois do escape",
        "antes do escape @\\nlinha",
        f"@ {REDIGIDO}",
    ],
)
def test_encontrar_emails_ignora_uid_calev_e_arroba_isolada(texto: str) -> None:
    assert _modulo().encontrar_emails(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "fulano.calev-1@events.lu.ma",
        "xcalev-1@events.lu.ma",
        "calev-1@events.lu.ma.example",
        "calevx-1@events.lu.ma",
        "evtx-1@events.lu.ma",
        "x ＠ y",
        "x &#64; y",
        "x %40 y",
        "Frontier Tower @Spaceship",
        "Frontier Tower@ Spaceship",
        "@ joao@ex.com",
    ],
)
def test_encontrar_emails_reporta_uid_colado_e_arroba_nao_isolada(texto: str) -> None:
    assert _modulo().encontrar_emails(texto) != []


@pytest.mark.parametrize(
    "texto",
    [
        "UID:calev-LLLL1234@events.lu.ma",
        "fulano.calev-1@events.lu.ma",
        "calevx-1@events.lu.ma",
        "Frontier Tower @ Spaceship",
        "x ＠ y",
        "x &#64; y",
        "Frontier Tower@ Spaceship",
    ],
)
def test_encontrar_emails_concorda_com_checagem_bruta_em_calev_e_arroba(texto: str) -> None:
    assert bool(_modulo().encontrar_emails(texto)) == bool(_arrobas_suspeitas(texto))


# E-mails disfarçados com `@` "isolado": o que vem depois parece domínio.
EMAILS_COM_ARROBA_ESPACADA = [
    "joao @ example.com",
    "joao\\n@\\nexample.com",
    "joao\n@\nexample.com",
    "joao @ example . com",
    "contato: joao @ gmail.com",
    "joao.silva @ empresa.com.br",
    "joao\\t@\\tgmail.com",
    "joao\t@\tgmail.com",
    "joao @ gmail.com",
    "joao @ gmail.com",
    "joao @ gmail . com",
    "joao\\N@\\Ngmail.com",
    "joao @\\n gmail \\t.\\n com",
]
ARROBAS_ISOLADAS_ACEITAS = [
    "Frontier Tower @ Spaceship",
    "Demo Night @ Cubo Itaú",
    "Meetup @ FIAP Paulista",
    "@ no início",
    "fim @",
    "Demo Night @ (Cubo)",
    "Meetup @ FIAP - Paulista",
]

# `@` isolado cujo próximo token esconde um domínio (dobra RFC 5545, pontuação em volta,
# URL, caracteres invisíveis de categoria Cf).
EMAILS_COM_TOKEN_DISFARCADO = [
    "joao @ gm\r\n ail.com",
    "joao @ gm\n\tail.com",
    'joao @ "gmail.com"',
    "joao @ (gmail.com)",
    "joao @ <gmail.com>",
    "joao @ [gmail.com]",
    "joao @ https://gmail.com",
    "joao @ ​gmail.com",
    "joao @ g​mail.com",
    "joao @ gmail‌.com",
    "joao @ gmail.‍com",
    "joao @ ⁠gmail.com",
    "joao @ ﻿gmail.com",
    "joao​ @ gmail.com",
]


@pytest.mark.parametrize("texto", EMAILS_COM_ARROBA_ESPACADA)
def test_encontrar_emails_reporta_arroba_isolada_seguida_de_dominio(texto: str) -> None:
    modulo = _modulo()
    assert modulo.encontrar_emails(texto) != []
    # A redação não pode "consertar" o texto a ponto de ele passar na checagem.
    assert modulo.encontrar_emails(modulo.redigir_emails(texto)) != []


@pytest.mark.parametrize("texto", EMAILS_COM_ARROBA_ESPACADA)
def test_checagem_bruta_dos_testes_reporta_arroba_isolada_seguida_de_dominio(texto: str) -> None:
    assert _arrobas_suspeitas(texto) != []


@pytest.mark.parametrize("texto", ARROBAS_ISOLADAS_ACEITAS)
def test_encontrar_emails_aceita_arroba_isolada_sem_dominio(texto: str) -> None:
    modulo = _modulo()
    assert modulo.encontrar_emails(texto) == []
    assert modulo.redigir_emails(texto) == texto


@pytest.mark.parametrize("texto", EMAILS_COM_TOKEN_DISFARCADO)
def test_encontrar_emails_reporta_token_disfarcado_apos_arroba_isolada(texto: str) -> None:
    modulo = _modulo()
    assert modulo.encontrar_emails(texto) != []
    assert modulo.encontrar_emails(modulo.redigir_emails(texto)) != []


@pytest.mark.parametrize("texto", EMAILS_COM_TOKEN_DISFARCADO)
def test_checagem_bruta_dos_testes_reporta_token_disfarcado(texto: str) -> None:
    assert _arrobas_suspeitas(texto) != []


@pytest.mark.parametrize("texto", ARROBAS_ISOLADAS_ACEITAS)
def test_checagem_bruta_dos_testes_aceita_arroba_isolada_sem_dominio(texto: str) -> None:
    assert _arrobas_suspeitas(texto) == []


@pytest.mark.parametrize(
    "nome", ["feed_a.ics", "feed_b.ics", "nao_ical.html", "feed_mencoes.ics", "feed_calev.ics"]
)
def test_encontrar_emails_nas_fixtures_antes_e_depois_da_redacao(nome: str) -> None:
    texto = _fixture(nome)
    assert _modulo().encontrar_emails(texto) != []
    assert _modulo().encontrar_emails(_modulo().redigir_emails(texto)) == []


def test_encontrar_emails_na_pagina_minimizada_e_redigida() -> None:
    modulo = _modulo()
    pagina = _fixture("pagina_evento.html")
    assert modulo.encontrar_emails(pagina) != []
    assert modulo.encontrar_emails(modulo.redigir_emails(modulo.minimizar_pagina(pagina))) == []


@pytest.mark.parametrize("nome", ["feed_email_ofuscado.ics", "pagina_email_ofuscado.html"])
def test_encontrar_emails_acha_ofuscado_que_a_redacao_nao_pega(nome: str) -> None:
    modulo = _modulo()
    assert modulo.encontrar_emails(modulo.redigir_emails(_fixture(nome))) != []


# --- links_de_evento --------------------------------------------------------------


def test_links_de_evento_em_ordem_e_sem_repetir() -> None:
    assert _modulo().links_de_evento(_fixture("feed_a.ics")) == LINKS_FEED_A


def test_links_de_evento_segundo_feed() -> None:
    assert _modulo().links_de_evento(_fixture("feed_b.ics")) == LINKS_FEED_B


def test_links_de_evento_ignora_event_user_e_outros_dominios() -> None:
    links = _modulo().links_de_evento(_fixture("feed_a.ics"))
    assert not any("/event/" in link for link in links)
    assert not any("/user/" in link for link in links)
    assert all(link.startswith("https://luma.com/") for link in links)


def test_links_de_evento_ignora_hosts_parecidos() -> None:
    links = _modulo().links_de_evento(_fixture("feed_hosts_estranhos.ics"))
    assert links == ["https://luma.com/meetup-ia-sp"]


def test_links_de_evento_ignora_links_fora_da_description_do_vevent() -> None:
    links = _modulo().links_de_evento(_fixture("feed_a.ics"))
    assert "https://luma.com/link-fora-da-descricao" not in links  # campo URL
    assert "https://luma.com/calendario-fora-do-evento" not in links  # X-WR-CALDESC


def test_links_de_evento_nao_inclui_escape_de_quebra_de_linha() -> None:
    """Na DESCRIPTION o link vem seguido de `\\n` literal; ele não faz parte do slug."""
    links = _modulo().links_de_evento(_fixture("feed_a.ics"))
    assert all("\\" not in link and "\n" not in link for link in links)


def test_links_de_evento_sem_links_devolve_lista_vazia() -> None:
    assert _modulo().links_de_evento(_fixture("feed_sem_links.ics")) == []


def test_links_de_evento_texto_que_nao_e_ical_devolve_lista_vazia() -> None:
    assert _modulo().links_de_evento(_fixture("nao_ical.html")) == []


def test_links_de_evento_com_linha_dobrada_rfc5545() -> None:
    """Linhas longas do iCal são dobradas (CRLF + espaço); o link deve ser remontado."""
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:evt-FFFF6666@events.lu.ma\r\n"
        "SUMMARY:Evento com descricao dobrada\r\n"
        "DESCRIPTION:Get up-to-date information at: https://luma.com/evento-com-sl\r\n"
        " ug-longo\\n\\nMais detalhes na pagina.\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    assert _modulo().links_de_evento(ics) == ["https://luma.com/evento-com-slug-longo"]


# --- escolher_paginas -------------------------------------------------------------


def test_escolher_paginas_respeita_maximo() -> None:
    feeds = {"a": _fixture("feed_a.ics"), "b": _fixture("feed_b.ics")}
    assert _modulo().escolher_paginas(feeds, 2) == LINKS_FEED_A


def test_escolher_paginas_remove_duplicados_entre_feeds() -> None:
    feeds = {"a": _fixture("feed_a.ics"), "b": _fixture("feed_b.ics")}
    assert _modulo().escolher_paginas(feeds, 10) == [
        "https://luma.com/meetup-ia-sp",
        "https://luma.com/x7k2p9q1",
        "https://luma.com/hackathon-genai",
    ]


def test_escolher_paginas_segue_ordem_das_chaves() -> None:
    feeds = {"b": _fixture("feed_b.ics"), "a": _fixture("feed_a.ics")}
    assert _modulo().escolher_paginas(feeds, 10) == [
        "https://luma.com/x7k2p9q1",
        "https://luma.com/hackathon-genai",
        "https://luma.com/meetup-ia-sp",
    ]


def test_escolher_paginas_sem_links_devolve_vazio() -> None:
    feeds = {"s": _fixture("feed_sem_links.ics"), "h": _fixture("nao_ical.html")}
    assert _modulo().escolher_paginas(feeds, 2) == []


def test_escolher_paginas_sem_feeds_devolve_vazio() -> None:
    assert _modulo().escolher_paginas({}, 2) == []


def test_escolher_paginas_com_maximo_zero_devolve_vazio() -> None:
    assert _modulo().escolher_paginas({"a": _fixture("feed_a.ics")}, 0) == []


# --- minimizar_pagina -------------------------------------------------------------


def test_minimizar_pagina_mantem_so_o_bloco_de_event() -> None:
    minima = _modulo().minimizar_pagina(_fixture("pagina_evento.html"))
    blocos = _blocos_ld_json(minima)
    assert len(blocos) == 1
    assert blocos[0]["@type"] == "Event"
    assert "BreadcrumbList" not in minima


def test_minimizar_pagina_aplica_lista_de_permissao_em_todos_os_niveis() -> None:
    """Igualdade exata: qualquer chave fora da lista de permissão some, em qualquer nível."""
    minima = _modulo().minimizar_pagina(_fixture("pagina_evento.html"))
    (evento,) = _blocos_ld_json(minima)
    assert evento == EVENTO_MINIMIZADO


@pytest.mark.parametrize(
    "chave",
    [
        "organizer",
        "performer",
        "sponsor",
        "attendee",
        "subEvent",
        "image",
        "description",
        "telephone",
        "aggregateRating",
    ],
)
def test_minimizar_pagina_remove_chaves_fora_da_lista_no_event(chave: str) -> None:
    assert chave in _evento_original()  # a fixture tem a chave
    (evento,) = _blocos_ld_json(_modulo().minimizar_pagina(_fixture("pagina_evento.html")))
    assert chave not in evento


def test_minimizar_pagina_reduz_location_e_address() -> None:
    (evento,) = _blocos_ld_json(_modulo().minimizar_pagina(_fixture("pagina_evento.html")))
    assert set(evento["location"]) == {"@type", "name", "address"}
    assert set(evento["location"]["address"]) == {
        "@type",
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    }


def test_minimizar_pagina_reduz_cada_oferta_da_lista() -> None:
    (evento,) = _blocos_ld_json(_modulo().minimizar_pagina(_fixture("pagina_evento.html")))
    permitidas = {"@type", "name", "price", "priceCurrency", "availability", "url"}
    assert isinstance(evento["offers"], list)
    for oferta in evento["offers"]:
        assert set(oferta) <= permitidas
        assert "seller" not in oferta


def _pagina_com_evento(dados: dict[str, Any]) -> str:
    return (
        '<html><body><script type="application/ld+json">'
        f"{json.dumps(dados)}</script><p>Fulana Exemplo</p></body></html>"
    )


def test_minimizar_pagina_reduz_oferta_unica_em_objeto() -> None:
    dados = {
        "@type": "Event",
        "name": "X",
        "offers": {
            "@type": "Offer",
            "price": "10",
            "priceCurrency": "BRL",
            "seller": {"@type": "Person", "name": "Fulana Exemplo"},
            "eligibleRegion": "BR",
        },
    }
    (evento,) = _blocos_ld_json(_modulo().minimizar_pagina(_pagina_com_evento(dados)))
    assert evento["offers"] == {"@type": "Offer", "price": "10", "priceCurrency": "BRL"}


def test_minimizar_pagina_mantem_address_em_texto() -> None:
    dados = {
        "@type": "Event",
        "name": "X",
        "location": {
            "@type": "Place",
            "name": "Auditorio",
            "address": "Rua Teste, 10 - Sao Paulo",
            "telephone": "+55 11 90000-0000",
            "description": "Sala da Fulana Exemplo",
            "image": "https://images.example/sala.jpg",
        },
    }
    (evento,) = _blocos_ld_json(_modulo().minimizar_pagina(_pagina_com_evento(dados)))
    assert evento["location"] == {
        "@type": "Place",
        "name": "Auditorio",
        "address": "Rua Teste, 10 - Sao Paulo",
    }


def test_minimizar_pagina_nao_inventa_chaves_ausentes() -> None:
    dados = {"@type": "Event", "name": "X", "sponsor": {"name": "Fulana Exemplo"}}
    (evento,) = _blocos_ld_json(_modulo().minimizar_pagina(_pagina_com_evento(dados)))
    assert evento == {"@type": "Event", "name": "X"}


def test_minimizar_pagina_descarta_texto_fora_do_json_ld() -> None:
    minima = _modulo().minimizar_pagina(_fixture("pagina_evento.html"))
    for nome in NOMES_NA_PAGINA:
        assert nome not in minima
    for rastro in RASTROS_NA_PAGINA:
        assert rastro not in minima
    assert "__DADOS__" not in minima
    assert "<p" not in minima.lower()
    assert "<h1" not in minima.lower()
    assert "mailto:" not in minima
    assert "Fulana" not in minima


@pytest.mark.parametrize("tipo", ["SocialEvent", ["Event", "SocialEvent"], ["BusinessEvent"]])
def test_minimizar_pagina_aceita_type_que_contem_event(tipo: str | list[str]) -> None:
    dados = {"@type": tipo, "name": "X", "organizer": {"name": "Fulana"}, "offers": {"price": "0"}}
    html = (
        '<html><body><script type="application/ld+json">'
        f"{json.dumps(dados)}</script><p>Fulana</p></body></html>"
    )
    (evento,) = _blocos_ld_json(_modulo().minimizar_pagina(html))
    assert evento["@type"] == tipo
    assert evento["offers"] == {"price": "0"}
    assert "organizer" not in evento


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<html><body><p>Sem JSON-LD</p></body></html>",
        '<script type="application/ld+json">{"@type":"Organization","name":"Luma"}</script>',
        '<script type="application/ld+json">{isto nao e json</script>',
        '<script>{"@type":"Event","name":"script sem tipo ld+json"}</script>',
    ],
)
def test_minimizar_pagina_sem_event_devolve_vazio(html: str) -> None:
    assert _modulo().minimizar_pagina(html) == ""


def test_minimizar_pagina_nao_html_devolve_vazio() -> None:
    assert _modulo().minimizar_pagina(_fixture("nao_ical.html")) == ""


# --- capturar (orquestração com transporte falso) ---------------------------------

Resposta = httpx.Response | Exception
ROBOTS_LIBERADO = "User-agent: *\nAllow: /\n"


class Cenario:
    """Transporte HTTP falso + `dormir` falso que registram tudo num log único."""

    def __init__(self, respostas: dict[str, Resposta]) -> None:
        self.respostas = respostas
        self.log: list[tuple[str, object]] = []

    @property
    def requisicoes(self) -> list[str]:
        return [str(valor) for tipo, valor in self.log if tipo == "requisicao"]

    @property
    def pausas(self) -> list[float]:
        return [float(valor) for tipo, valor in self.log if tipo == "dormir"]  # type: ignore[arg-type]

    def dormir(self, segundos: float) -> None:
        self.log.append(("dormir", segundos))

    def _tratar(self, requisicao: httpx.Request) -> httpx.Response:
        url = str(requisicao.url)
        self.log.append(("requisicao", url))
        resposta = self.respostas.get(url)
        if resposta is None:
            return httpx.Response(404, text="nao encontrado")
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    def cliente(self) -> httpx.Client:
        # follow_redirects=True de propósito: `capturar` precisa recusar redirecionamentos
        # mesmo que o cliente recebido esteja configurado para segui-los.
        return httpx.Client(transport=httpx.MockTransport(self._tratar), follow_redirects=True)


def _ical(nome: str) -> httpx.Response:
    return httpx.Response(
        200, text=_fixture(nome), headers={"content-type": "text/calendar; charset=utf-8"}
    )


def _html(nome: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, text=_fixture(nome), headers={"content-type": "text/html; charset=utf-8"}
    )


def _robots(texto: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=texto, headers={"content-type": "text/plain"})


def _redirecionar(destino: str, status: int = 301) -> httpx.Response:
    return httpx.Response(status, headers={"location": destino})


def _respostas_base() -> dict[str, Resposta]:
    """Robots liberado; todos os feeds OK; só ai-collective e codex-sp têm links."""
    respostas: dict[str, Resposta] = {
        ROBOTS_API: _robots(ROBOTS_LIBERADO),
        ROBOTS_LUMA: _robots(ROBOTS_LIBERADO),
    }
    for slug in TODOS_OS_FEEDS:
        respostas[_url_do_feed(slug)] = _ical("feed_sem_links.ics")
    respostas[_url_do_feed("ai-collective")] = _ical("feed_a.ics")
    respostas[_url_do_feed("codex-sp")] = _ical("feed_b.ics")
    respostas["https://luma.com/meetup-ia-sp"] = _html("pagina_evento.html")
    respostas["https://luma.com/x7k2p9q1"] = _html("pagina_evento.html")
    respostas["https://luma.com/hackathon-genai"] = _html("pagina_evento.html")
    return respostas


def _respostas_sem_links() -> dict[str, Resposta]:
    respostas: dict[str, Resposta] = {ROBOTS_API: _robots(ROBOTS_LIBERADO)}
    for slug in TODOS_OS_FEEDS:
        respostas[_url_do_feed(slug)] = _ical("feed_sem_links.ics")
    return respostas


Executar = Callable[[dict[str, Resposta]], tuple[Any, Cenario]]


@pytest.fixture
def executar(tmp_path: Path) -> Executar:
    def _executar(respostas: dict[str, Resposta]) -> tuple[Any, Cenario]:
        cenario = Cenario(respostas)
        with cenario.cliente() as cliente:
            relatorio = _modulo().capturar(tmp_path, cliente, dormir=cenario.dormir)
        return relatorio, cenario

    return _executar


def _pasta(tmp_path: Path) -> Path:
    return tmp_path / "luma"


def _assert_hosts_permitidos(cenario: Cenario) -> None:
    for url in cenario.requisicoes:
        destino = httpx.URL(url)
        assert destino.scheme == "https", url
        assert destino.host in HOSTS_PERMITIDOS, url


def test_capturar_salva_todos_os_feeds_validos(tmp_path: Path, executar: Executar) -> None:
    executar(_respostas_base())
    for slug in TODOS_OS_FEEDS:
        assert (_pasta(tmp_path) / f"{slug}.ics").is_file(), slug


def test_capturar_faz_so_as_requisicoes_esperadas_na_ordem(
    tmp_path: Path, executar: Executar
) -> None:
    """robots.txt de cada host antes da primeira requisição a ele; nada além disso."""
    _, cenario = executar(_respostas_base())
    assert cenario.requisicoes == [
        ROBOTS_API,
        *(_url_do_feed(slug) for slug in TODOS_OS_FEEDS),
        ROBOTS_LUMA,
        *LINKS_FEED_A,
    ]
    _assert_hosts_permitidos(cenario)


def test_capturar_salva_2_paginas_de_evento(tmp_path: Path, executar: Executar) -> None:
    relatorio, cenario = executar(_respostas_base())
    pasta = _pasta(tmp_path)
    assert (pasta / "pagina_1.html").is_file()
    assert (pasta / "pagina_2.html").is_file()
    assert not (pasta / "pagina_3.html").exists()
    assert relatorio.paginas == LINKS_FEED_A
    assert "https://luma.com/hackathon-genai" not in cenario.requisicoes


def test_capturar_relatorio_feeds_ok(tmp_path: Path, executar: Executar) -> None:
    relatorio, _ = executar(_respostas_base())
    assert sorted(relatorio.feeds_ok) == sorted(TODOS_OS_FEEDS)
    assert relatorio.feeds_falha == {}
    assert relatorio.bloqueadas_robots == []
    # UID, marcadores e chaves "@context"/"@type" não podem disparar descarte.
    assert relatorio.descartados_por_dados_pessoais == []


def test_capturar_relatorio_e_pydantic_com_campos_do_desenho(
    tmp_path: Path, executar: Executar
) -> None:
    from pydantic import BaseModel

    relatorio, _ = executar(_respostas_base())
    assert isinstance(relatorio, _modulo().RelatorioCaptura)
    assert isinstance(relatorio, BaseModel)
    assert set(type(relatorio).model_fields) >= {
        "feeds_ok",
        "feeds_falha",
        "paginas",
        "bloqueadas_robots",
        "descartados_por_dados_pessoais",
        "capturado_em",
    }


def test_capturar_data_em_utc_com_fuso(tmp_path: Path, executar: Executar) -> None:
    antes = datetime.now(UTC)
    relatorio, _ = executar(_respostas_base())
    depois = datetime.now(UTC)
    capturado_em: datetime = relatorio.capturado_em
    assert capturado_em.tzinfo is not None
    assert capturado_em.utcoffset() is not None
    assert capturado_em.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert antes <= capturado_em <= depois


def test_capturar_grava_captura_json_com_o_relatorio(tmp_path: Path, executar: Executar) -> None:
    relatorio, _ = executar(_respostas_base())
    arquivo = _pasta(tmp_path) / "captura.json"
    assert arquivo.is_file()
    lido = _modulo().RelatorioCaptura.model_validate_json(arquivo.read_text(encoding="utf-8"))
    assert lido == relatorio


def test_capturar_salva_conteudo_do_feed(tmp_path: Path, executar: Executar) -> None:
    executar(_respostas_base())
    salvo = (_pasta(tmp_path) / "ai-collective.ics").read_text(encoding="utf-8")
    assert salvo.startswith("BEGIN:VCALENDAR")
    assert "UID:evt-AAAA1111@events.lu.ma" in salvo
    assert "https://luma.com/meetup-ia-sp" in salvo


def test_capturar_redige_emails_no_organizer_e_na_description(
    tmp_path: Path, executar: Executar
) -> None:
    executar(_respostas_base())
    salvo = (_pasta(tmp_path) / "ai-collective.ics").read_text(encoding="utf-8")
    assert "organizadora.teste@example.com" not in salvo  # ORGANIZER
    assert "joao.silva@events.lu.ma" not in salvo  # ORGANIZER em events.lu.ma
    assert "duvidas+ia@sub.example.org" not in salvo  # DESCRIPTION
    assert "rsvp.lista@example.com" not in salvo  # DESCRIPTION após \n
    assert "contato%40example.com" not in salvo  # DESCRIPTION, forma codificada
    assert f"MAILTO:{REDIGIDO}" in salvo
    assert f"RSVP:\\n{REDIGIDO}" in salvo


def test_capturar_nenhum_arquivo_salvo_contem_email(tmp_path: Path, executar: Executar) -> None:
    executar(_respostas_base())
    arquivos = list(_pasta(tmp_path).iterdir())
    assert arquivos
    for arquivo in arquivos:
        texto = arquivo.read_text(encoding="utf-8")
        assert _vazamentos(texto) == [], arquivo.name
        assert _arrobas_suspeitas(texto) == [], arquivo.name


def test_capturar_salva_pagina_minimizada(tmp_path: Path, executar: Executar) -> None:
    executar(_respostas_base())
    salvo = (_pasta(tmp_path) / "pagina_1.html").read_text(encoding="utf-8")
    (evento,) = _blocos_ld_json(salvo)
    assert evento == EVENTO_MINIMIZADO
    for nome in NOMES_NA_PAGINA:
        assert nome not in salvo
    for rastro in RASTROS_NA_PAGINA:
        assert rastro not in salvo
    assert "<p" not in salvo.lower()


def test_capturar_feed_com_email_ofuscado_nao_e_salvo(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("mug-sp")] = _ical("feed_email_ofuscado.ics")
    relatorio, _ = executar(respostas)
    assert not (_pasta(tmp_path) / "mug-sp.ics").exists()
    assert relatorio.descartados_por_dados_pessoais == ["mug-sp.ics"]
    # Os demais feeds seguem normalmente.
    assert (_pasta(tmp_path) / "vibe-space.ics").is_file()
    assert (_pasta(tmp_path) / "ai-collective.ics").is_file()


def test_capturar_pagina_com_email_ofuscado_nao_e_salva_nem_listada(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas["https://luma.com/x7k2p9q1"] = _html("pagina_email_ofuscado.html")
    relatorio, _ = executar(respostas)
    assert relatorio.paginas == ["https://luma.com/meetup-ia-sp"]
    paginas_salvas = list(_pasta(tmp_path).glob("pagina_*.html"))
    assert len(paginas_salvas) == 1
    (descartado,) = relatorio.descartados_por_dados_pessoais
    assert re.fullmatch(r"pagina_\d+\.html", descartado)
    for arquivo in paginas_salvas:
        assert "&#64;" not in arquivo.read_text(encoding="utf-8")


def test_capturar_feed_com_mencoes_e_salvo_redigido(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _ical("feed_mencoes.ics")
    relatorio, _ = executar(respostas)
    arquivo = _pasta(tmp_path) / "vibe-space.ics"
    assert arquivo.is_file()
    assert "vibe-space" in relatorio.feeds_ok
    assert relatorio.descartados_por_dados_pessoais == []
    salvo = arquivo.read_text(encoding="utf-8")
    for perfil in HANDLES_FEED_MENCOES:
        assert perfil not in salvo, perfil
    assert "organizadora.mencao@example.com" not in salvo
    assert PERFIL_REDIGIDO in salvo
    assert "UID:evt-JJJJ9999@events.lu.ma" in salvo
    assert _arrobas_suspeitas(salvo) == []


def test_capturar_mencoes_salvas_e_email_ofuscado_descartado(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _ical("feed_mencoes.ics")
    respostas[_url_do_feed("mug-sp")] = _ical("feed_email_ofuscado.ics")
    relatorio, _ = executar(respostas)
    assert (_pasta(tmp_path) / "vibe-space.ics").is_file()
    assert not (_pasta(tmp_path) / "mug-sp.ics").exists()
    assert relatorio.descartados_por_dados_pessoais == ["mug-sp.ics"]


def test_capturar_feed_com_mencao_unicode_e_descartado(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _ical("feed_mencao_unicode.ics")
    relatorio, _ = executar(respostas)
    assert not (_pasta(tmp_path) / "vibe-space.ics").exists()
    assert relatorio.descartados_por_dados_pessoais == ["vibe-space.ics"]
    for arquivo in _pasta(tmp_path).iterdir():
        assert "josé" not in arquivo.read_text(encoding="utf-8"), arquivo.name


def test_capturar_salva_feed_com_uid_calev_e_arroba_isolada(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _ical("feed_calev.ics")
    relatorio, _ = executar(respostas)
    arquivo = _pasta(tmp_path) / "vibe-space.ics"
    assert arquivo.is_file()
    assert "vibe-space" in relatorio.feeds_ok
    assert relatorio.descartados_por_dados_pessoais == []
    salvo = arquivo.read_text(encoding="utf-8")
    assert "UID:calev-LLLL1234@events.lu.ma" in salvo
    assert "SUMMARY:Demo Night @ Cubo" in salvo
    assert "LOCATION:Frontier Tower @ Spaceship" in salvo
    assert f"Hackathon #2 {PERFIL_REDIGIDO}" in salvo
    assert "Stanford" not in salvo
    assert "organizadora.calev@example.com" not in salvo
    assert _arrobas_suspeitas(salvo) == []


def test_capturar_feed_com_email_de_arroba_espacada_e_descartado(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _ical("feed_arroba_espacada.ics")
    relatorio, _ = executar(respostas)
    assert not (_pasta(tmp_path) / "vibe-space.ics").exists()
    assert relatorio.descartados_por_dados_pessoais == ["vibe-space.ics"]
    for arquivo in _pasta(tmp_path).iterdir():
        assert "gmail" not in arquivo.read_text(encoding="utf-8"), arquivo.name


def test_capturar_feed_com_dominio_dobrado_apos_arroba_isolada_e_descartado(
    tmp_path: Path, executar: Executar
) -> None:
    """Dobra RFC 5545 real no meio do domínio (`gm` + CRLF + espaço + `ail.com`)."""
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _ical("feed_arroba_dobrada.ics")
    relatorio, _ = executar(respostas)
    assert not (_pasta(tmp_path) / "vibe-space.ics").exists()
    assert relatorio.descartados_por_dados_pessoais == ["vibe-space.ics"]


def test_capturar_descarte_aparece_no_captura_json(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("mug-sp")] = _ical("feed_email_ofuscado.ics")
    executar(respostas)
    dados = json.loads((_pasta(tmp_path) / "captura.json").read_text(encoding="utf-8"))
    assert dados["descartados_por_dados_pessoais"] == ["mug-sp.ics"]


def test_capturar_pagina_sem_json_ld_de_event_nao_e_salva_nem_listada(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas["https://luma.com/meetup-ia-sp"] = _html("nao_ical.html")
    relatorio, _ = executar(respostas)
    assert relatorio.paginas == ["https://luma.com/x7k2p9q1"]
    assert len(list(_pasta(tmp_path).glob("pagina_*.html"))) == 1


def test_capturar_pausa_antes_de_cada_requisicao(tmp_path: Path, executar: Executar) -> None:
    _, cenario = executar(_respostas_base())
    assert ROBOTS_API in cenario.requisicoes
    assert ROBOTS_LUMA in cenario.requisicoes
    assert len(cenario.pausas) == len(cenario.requisicoes)
    assert all(segundos == 3 for segundos in cenario.pausas)
    # Cada requisição (inclusive robots.txt) é precedida imediatamente por uma pausa.
    tipos = [tipo for tipo, _ in cenario.log]
    assert tipos == ["dormir", "requisicao"] * len(cenario.requisicoes)


def test_capturar_pausa_uma_vez_por_requisicao_mesmo_com_falhas(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("codex-sp")] = httpx.Response(404, text="nao encontrado")
    respostas[_url_do_feed("vibe-space")] = httpx.ReadTimeout("tempo esgotado")
    respostas[_url_do_feed("mug-sp")] = _redirecionar("https://api.lu.ma/outro")
    respostas["https://luma.com/meetup-ia-sp"] = httpx.Response(500, text="erro")
    _, cenario = executar(respostas)
    tipos = [tipo for tipo, _ in cenario.log]
    assert tipos == ["dormir", "requisicao"] * len(cenario.requisicoes)


def test_capturar_feed_404_vira_falha_sem_arquivo(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("codex-sp")] = httpx.Response(404, text="nao encontrado")
    relatorio, _ = executar(respostas)
    assert relatorio.feeds_falha == {"codex-sp": 404}
    assert "codex-sp" not in relatorio.feeds_ok
    assert not (_pasta(tmp_path) / "codex-sp.ics").exists()
    assert (_pasta(tmp_path) / "vibe-space.ics").is_file()
    assert (_pasta(tmp_path) / "cidade-sp.ics").is_file()


def test_capturar_feed_com_corpo_html_vira_falha_sem_arquivo(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _html("nao_ical.html", status=200)
    relatorio, _ = executar(respostas)
    assert relatorio.feeds_falha == {"vibe-space": 200}
    assert "vibe-space" not in relatorio.feeds_ok
    assert not (_pasta(tmp_path) / "vibe-space.ics").exists()


def test_capturar_timeout_vira_falha_com_status_none(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("womakerscode")] = httpx.ReadTimeout("tempo esgotado")
    relatorio, _ = executar(respostas)
    assert relatorio.feeds_falha == {"womakerscode": None}
    assert not (_pasta(tmp_path) / "womakerscode.ics").exists()
    assert (_pasta(tmp_path) / "mug-sp.ics").is_file()


def test_capturar_erro_de_conexao_nao_levanta(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("cidade-sp")] = httpx.ConnectError("sem rede")
    relatorio, _ = executar(respostas)
    assert relatorio.feeds_falha == {"cidade-sp": None}


def test_capturar_429_registrado_como_falha(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("mug-sp")] = httpx.Response(429, text="devagar")
    relatorio, _ = executar(respostas)
    assert relatorio.feeds_falha == {"mug-sp": 429}


def test_capturar_nunca_repete_requisicao_que_falhou(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("codex-sp")] = httpx.Response(404, text="nao encontrado")
    respostas[_url_do_feed("vibe-space")] = httpx.ReadTimeout("tempo esgotado")
    respostas[_url_do_feed("mug-sp")] = httpx.Response(429, text="devagar")
    respostas[_url_do_feed("womakerscode")] = _html("nao_ical.html")
    respostas["https://luma.com/meetup-ia-sp"] = httpx.Response(500, text="erro")
    _, cenario = executar(respostas)
    for url in set(cenario.requisicoes):
        assert cenario.requisicoes.count(url) == 1, url


# --- Redirecionamentos e hosts ----------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_capturar_feed_redirecionado_vira_falha_e_destino_nao_e_requisitado(
    tmp_path: Path, executar: Executar, status: int
) -> None:
    respostas = _respostas_base()
    destino = "https://api.lu.ma/ics/get?entity=calendar&id=cal-destino"
    respostas[_url_do_feed("codex-sp")] = _redirecionar(destino, status)
    respostas[destino] = _ical("feed_b.ics")
    relatorio, cenario = executar(respostas)
    assert relatorio.feeds_falha == {"codex-sp": status}
    assert destino not in cenario.requisicoes
    assert not (_pasta(tmp_path) / "codex-sp.ics").exists()


def test_capturar_redirecionamento_para_outro_host_nunca_e_seguido(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas[_url_do_feed("vibe-space")] = _redirecionar("https://evil.example/feed.ics")
    respostas["https://evil.example/feed.ics"] = _ical("feed_b.ics")
    relatorio, cenario = executar(respostas)
    assert relatorio.feeds_falha == {"vibe-space": 301}
    assert "https://evil.example/feed.ics" not in cenario.requisicoes
    _assert_hosts_permitidos(cenario)


def test_capturar_pagina_redirecionada_nao_e_salva_nem_seguida(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas["https://luma.com/meetup-ia-sp"] = _redirecionar("https://luma.com/outro", 302)
    respostas["https://luma.com/outro"] = _html("pagina_evento.html")
    relatorio, cenario = executar(respostas)
    assert "https://luma.com/outro" not in cenario.requisicoes
    assert relatorio.paginas == ["https://luma.com/x7k2p9q1"]
    assert len(list(_pasta(tmp_path).glob("pagina_*.html"))) == 1


def test_capturar_feed_com_links_de_hosts_estranhos_so_requisita_luma(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_sem_links()
    respostas[ROBOTS_LUMA] = _robots(ROBOTS_LIBERADO)
    respostas[_url_do_feed("ai-collective")] = _ical("feed_hosts_estranhos.ics")
    respostas["https://luma.com/meetup-ia-sp"] = _html("pagina_evento.html")
    relatorio, cenario = executar(respostas)
    _assert_hosts_permitidos(cenario)
    assert relatorio.paginas == ["https://luma.com/meetup-ia-sp"]


def test_capturar_nunca_requisita_link_de_outro_host(
    tmp_path: Path, executar: Executar, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesmo que a escolha de páginas devolva um link estranho, `capturar` não o segue."""
    estranhos = [
        "https://evil.example/meetup",
        "http://luma.com/sem-https",
        "https://luma.com.evil.example/golpe",
        "https://www.luma.com/com-www",
    ]
    monkeypatch.setattr(
        _modulo(),
        "escolher_paginas",
        lambda feeds, maximo: [*estranhos, "https://luma.com/meetup-ia-sp"][:maximo],
    )
    monkeypatch.setattr(_modulo(), "MAX_PAGINAS_EVENTO", 5)
    relatorio, cenario = executar(_respostas_base())
    _assert_hosts_permitidos(cenario)
    for url in estranhos:
        assert url not in cenario.requisicoes
        assert url not in relatorio.paginas


# --- robots.txt -------------------------------------------------------------------


def test_capturar_robots_txt_de_cada_host_uma_vez(tmp_path: Path, executar: Executar) -> None:
    _, cenario = executar(_respostas_base())
    assert cenario.requisicoes.count(ROBOTS_API) == 1
    assert cenario.requisicoes.count(ROBOTS_LUMA) == 1


def test_capturar_sem_paginas_nao_busca_robots_da_luma(tmp_path: Path, executar: Executar) -> None:
    _, cenario = executar(_respostas_sem_links())
    assert cenario.requisicoes[0] == ROBOTS_API
    assert ROBOTS_LUMA not in cenario.requisicoes


def test_capturar_respeita_robots_que_proibe_os_feeds(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[ROBOTS_API] = _robots("User-agent: *\nDisallow: /ics/\n")
    relatorio, cenario = executar(respostas)
    assert cenario.requisicoes == [ROBOTS_API]
    assert sorted(relatorio.bloqueadas_robots) == sorted(
        _url_do_feed(slug) for slug in TODOS_OS_FEEDS
    )
    assert relatorio.feeds_ok == []
    assert relatorio.paginas == []
    assert list(_pasta(tmp_path).glob("*.ics")) == []


def test_capturar_respeita_robots_especifico_do_radar(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[ROBOTS_LUMA] = _robots(
        "User-agent: radar-eventos-ia\nDisallow: /meetup-ia-sp\n\nUser-agent: *\nAllow: /\n"
    )
    relatorio, cenario = executar(respostas)
    assert "https://luma.com/meetup-ia-sp" not in cenario.requisicoes
    assert "https://luma.com/meetup-ia-sp" in relatorio.bloqueadas_robots
    assert "https://luma.com/meetup-ia-sp" not in relatorio.paginas
    assert "https://luma.com/x7k2p9q1" in relatorio.paginas


def test_capturar_ignora_regra_de_robots_de_outro_robo(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[ROBOTS_API] = _robots("User-agent: OutroRobo\nDisallow: /\n")
    relatorio, _ = executar(respostas)
    assert relatorio.bloqueadas_robots == []
    assert sorted(relatorio.feeds_ok) == sorted(TODOS_OS_FEEDS)


def test_capturar_robots_404_libera_tudo(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas[ROBOTS_API] = _robots("nao encontrado", status=404)
    respostas[ROBOTS_LUMA] = _robots("nao encontrado", status=404)
    relatorio, _ = executar(respostas)
    assert relatorio.bloqueadas_robots == []
    assert sorted(relatorio.feeds_ok) == sorted(TODOS_OS_FEEDS)
    assert relatorio.paginas == LINKS_FEED_A


@pytest.mark.parametrize(
    "falha",
    [
        httpx.Response(500, text="erro"),
        httpx.Response(503, text="indisponivel"),
        httpx.ReadTimeout("tempo esgotado"),
        httpx.ConnectError("sem rede"),
    ],
    ids=["500", "503", "timeout", "conexao"],
)
def test_capturar_robots_com_erro_bloqueia_o_host(
    tmp_path: Path, executar: Executar, falha: Resposta
) -> None:
    respostas = _respostas_base()
    respostas[ROBOTS_API] = falha
    relatorio, cenario = executar(respostas)
    assert cenario.requisicoes == [ROBOTS_API]
    assert sorted(relatorio.bloqueadas_robots) == sorted(
        _url_do_feed(slug) for slug in TODOS_OS_FEEDS
    )
    assert relatorio.feeds_ok == []


def test_capturar_robots_da_luma_com_erro_bloqueia_so_as_paginas(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas[ROBOTS_LUMA] = httpx.Response(500, text="erro")
    relatorio, cenario = executar(respostas)
    assert sorted(relatorio.feeds_ok) == sorted(TODOS_OS_FEEDS)
    assert relatorio.paginas == []
    assert sorted(relatorio.bloqueadas_robots) == sorted(LINKS_FEED_A)
    assert not any(url in cenario.requisicoes for url in LINKS_FEED_A)


# --- Casos de borda gerais --------------------------------------------------------


def test_capturar_todos_os_feeds_falhando_nao_levanta_e_nao_busca_paginas(
    tmp_path: Path, executar: Executar
) -> None:
    respostas: dict[str, Resposta] = {ROBOTS_API: _robots(ROBOTS_LIBERADO)}
    for slug in TODOS_OS_FEEDS:
        respostas[_url_do_feed(slug)] = httpx.ReadTimeout("tempo esgotado")
    relatorio, cenario = executar(respostas)
    assert relatorio.feeds_ok == []
    assert relatorio.feeds_falha == dict.fromkeys(TODOS_OS_FEEDS)
    assert relatorio.paginas == []
    assert cenario.requisicoes == [ROBOTS_API, *(_url_do_feed(s) for s in TODOS_OS_FEEDS)]
    assert (_pasta(tmp_path) / "captura.json").is_file()


def test_capturar_sem_links_nao_salva_paginas(tmp_path: Path, executar: Executar) -> None:
    relatorio, cenario = executar(_respostas_sem_links())
    assert relatorio.paginas == []
    assert list(_pasta(tmp_path).glob("pagina_*.html")) == []
    assert all(httpx.URL(url).host == "api.lu.ma" for url in cenario.requisicoes)


def test_capturar_links_duplicados_entre_feeds_viram_uma_pagina(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_sem_links()
    respostas[ROBOTS_LUMA] = _robots(ROBOTS_LIBERADO)
    respostas[_url_do_feed("ai-collective")] = _ical("feed_b.ics")
    respostas[_url_do_feed("codex-sp")] = _ical("feed_b.ics")
    respostas["https://luma.com/x7k2p9q1"] = _html("pagina_evento.html")
    respostas["https://luma.com/hackathon-genai"] = _html("pagina_evento.html")
    relatorio, cenario = executar(respostas)
    assert relatorio.paginas == LINKS_FEED_B
    assert cenario.requisicoes.count("https://luma.com/x7k2p9q1") == 1


def test_capturar_pagina_com_erro_nao_e_salva_nem_listada(
    tmp_path: Path, executar: Executar
) -> None:
    respostas = _respostas_base()
    respostas["https://luma.com/meetup-ia-sp"] = httpx.Response(500, text="erro")
    relatorio, _ = executar(respostas)
    assert relatorio.paginas == ["https://luma.com/x7k2p9q1"]
    assert len(list(_pasta(tmp_path).glob("pagina_*.html"))) == 1


def test_capturar_pagina_com_timeout_nao_levanta(tmp_path: Path, executar: Executar) -> None:
    respostas = _respostas_base()
    respostas["https://luma.com/x7k2p9q1"] = httpx.ReadTimeout("tempo esgotado")
    relatorio, _ = executar(respostas)
    assert relatorio.paginas == ["https://luma.com/meetup-ia-sp"]
    assert len(list(_pasta(tmp_path).glob("pagina_*.html"))) == 1


def test_capturar_cria_pasta_luma_se_nao_existir(tmp_path: Path) -> None:
    destino = tmp_path / "nao" / "existe"
    cenario = Cenario(_respostas_base())
    with cenario.cliente() as cliente:
        _modulo().capturar(destino, cliente, dormir=cenario.dormir)
    assert (destino / "luma" / "captura.json").is_file()


# --- Linha de comando -------------------------------------------------------------


class _ClienteProibido:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("o modo --verificar não pode abrir cliente HTTP")


def _preparar_verificacao(tmp_path: Path, arquivos: dict[str, str]) -> Path:
    for relativo, texto in arquivos.items():
        caminho = tmp_path / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
    return tmp_path


def test_main_verificar_passa_com_fixtures_limpas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(httpx, "Client", _ClienteProibido)
    pasta = _preparar_verificacao(
        tmp_path,
        {
            "luma/a.ics": f"UID:evt-AAAA1111@events.lu.ma\r\nORGANIZER:mailto:{REDIGIDO}\r\n",
            "luma/pagina_1.html": (
                '<script type="application/ld+json">'
                f'{{"@context":"https://schema.org","@type":"Event","url":"{REDIGIDO_URL}"}}'
                "</script>"
            ),
            "luma/captura.json": "{}",
        },
    )
    assert _modulo().main(["--verificar", str(pasta)]) == 0


@pytest.mark.parametrize(
    "conteudo",
    [
        "ORGANIZER:mailto:fulana@example.com",
        "?email=fulana%40example.com",
        "ORGANIZER:mailto:joao.silva@events.lu.ma",
        "DESCRIPTION:Contato:\\nfulana@example.com",
        "DESCRIPTION:Contato: fulana&#64;example.com",
        "DESCRIPTION:Siga @fulana_perfil",
    ],
)
def test_main_verificar_falha_com_email_e_nao_imprime_o_email(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    conteudo: str,
) -> None:
    monkeypatch.setattr(httpx, "Client", _ClienteProibido)
    pasta = _preparar_verificacao(
        tmp_path, {"luma/limpo.ics": "BEGIN:VCALENDAR\r\n", "luma/vazado.ics": conteudo}
    )
    assert _modulo().main(["--verificar", str(pasta)]) != 0
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert "vazado.ics" in texto
    assert "fulana" not in texto  # o log do CI é público
    assert "joao.silva" not in texto


def test_main_verificar_olha_so_a_pasta_luma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As fixtures sintéticas (ex.: `captura/`) têm e-mails `example.*` de propósito."""
    monkeypatch.setattr(httpx, "Client", _ClienteProibido)
    pasta = _preparar_verificacao(
        tmp_path,
        {"luma/a.ics": "BEGIN:VCALENDAR\r\n", "captura/feed.ics": "mailto:x@example.com"},
    )
    assert _modulo().main(["--verificar", str(pasta)]) == 0


def test_main_cria_cliente_sem_redirecionamento_e_com_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modulo = _modulo()
    vistos: list[httpx.Client] = []

    def capturar_falso(destino: Path, cliente: httpx.Client, *args: object, **kwargs: object):
        vistos.append(cliente)
        return modulo.RelatorioCaptura(
            feeds_ok=[],
            feeds_falha={},
            paginas=[],
            bloqueadas_robots=[],
            descartados_por_dados_pessoais=[],
            capturado_em=datetime.now(UTC),
        )

    monkeypatch.setattr(modulo, "capturar", capturar_falso)
    assert modulo.main([str(tmp_path)]) == 0
    (cliente,) = vistos
    assert cliente.follow_redirects is False
    assert cliente.headers["User-Agent"] == modulo.USER_AGENT


# --- Workflow manual --------------------------------------------------------------

CAMINHO_WORKFLOW = RAIZ / ".github" / "workflows" / "capturar-fixtures.yml"
BRANCH_DADOS = "fase-1/f1-02-fixtures-dados"
# Branch de dados com o número da execução: cada captura vai para um branch novo.
_BRANCH_DADOS_COM_EXECUCAO = re.compile(
    r"fase-1/f1-02-fixtures-dados-\$\{\{\s*github\.run_number\s*\}\}"
)
COMANDO_VERIFICAR = "python -m radar.fontes.captura_fixtures --verificar tests/fixtures"


def _texto_workflow() -> str:
    assert CAMINHO_WORKFLOW.is_file(), f"{CAMINHO_WORKFLOW.relative_to(RAIZ)} não existe"
    return CAMINHO_WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict:
    return yaml.safe_load(_texto_workflow())


def _passos(workflow: dict) -> list[dict]:
    return [passo for job in workflow["jobs"].values() for passo in job.get("steps", [])]


def _comandos_run(workflow: dict) -> list[str]:
    return [passo.get("run", "") for passo in _passos(workflow)]


def test_workflow_captura_usa_branch_com_numero_da_execucao() -> None:
    assert _BRANCH_DADOS_COM_EXECUCAO.search(_texto_workflow())


def test_workflow_captura_nunca_usa_branch_de_dados_sem_numero() -> None:
    """Toda menção ao branch de dados no workflow traz o `github.run_number`."""
    texto = _texto_workflow()
    sem_numero = [
        m.start()
        for m in re.finditer(re.escape(BRANCH_DADOS), texto)
        if not _BRANCH_DADOS_COM_EXECUCAO.match(texto, m.start())
    ]
    assert sem_numero == []


def test_workflow_captura_so_roda_manualmente() -> None:
    workflow = _workflow()
    # PyYAML interpreta a chave `on` como o booleano True.
    gatilhos = workflow.get("on", workflow.get(True))
    if isinstance(gatilhos, str):
        gatilhos = [gatilhos]
    assert set(gatilhos) == {"workflow_dispatch"}


def test_workflow_captura_tem_permissao_de_escrita_no_conteudo() -> None:
    assert _workflow().get("permissions", {}).get("contents") == "write"


def test_workflow_captura_usa_python_312() -> None:
    assert re.search(r"python-version:\s*['\"]?3\.12", _texto_workflow())


def test_workflow_captura_instala_o_pacote() -> None:
    comandos = _comandos_run(_workflow())
    assert any(re.search(r"pip install .*-e \.|pip install .*\s\.(\s|$)", c) for c in comandos)


def test_workflow_captura_executa_o_modulo_em_tests_fixtures() -> None:
    comandos = _comandos_run(_workflow())
    assert any("python -m radar.fontes.captura_fixtures tests/fixtures" in c for c in comandos)


def test_workflow_captura_publica_no_branch_de_dados() -> None:
    texto = _texto_workflow()
    assert BRANCH_DADOS in texto
    comandos = "\n".join(_comandos_run(_workflow()))
    assert re.search(r"git push", comandos)


def test_workflow_captura_nao_faz_push_para_main() -> None:
    texto = _texto_workflow()
    assert not re.search(r"git push[^\n]*\bmain\b", texto)
    assert "HEAD:main" not in texto
    assert "refs/heads/main" not in texto


def test_workflow_captura_nao_forca_push() -> None:
    texto = _texto_workflow()
    assert "--force" not in texto
    assert not re.search(r"git push[^\n]*\s-[a-zA-Z]*f", texto)
    assert not re.search(r"git push[^\n]*\s\+", texto)  # refspec com `+` também força


def test_workflow_captura_verifica_emails_antes_do_commit() -> None:
    passos = _passos(_workflow())
    verificacao = [
        i
        for i, passo in enumerate(passos)
        if re.search(r"e-?mail", passo.get("name", ""), re.IGNORECASE)
        and COMANDO_VERIFICAR in passo.get("run", "")
    ]
    commit = [i for i, passo in enumerate(passos) if "git commit" in passo.get("run", "")]
    assert verificacao, "falta passo com nome de e-mail rodando o modo --verificar"
    assert commit, "falta passo com git commit"
    assert verificacao[0] < commit[0]
    passo = passos[verificacao[0]]
    assert not passo.get("continue-on-error", False)
    assert "|| true" not in passo["run"]


def test_workflow_captura_nao_contem_segredos_literais() -> None:
    texto = _texto_workflow()
    assert not re.search(r"sk-ant-[A-Za-z0-9]", texto)
    assert not re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", texto)


# --- Dependência ------------------------------------------------------------------


def test_pyproject_declara_httpx_fixado() -> None:
    with (RAIZ / "pyproject.toml").open("rb") as arquivo:
        dependencias = tomllib.load(arquivo)["project"]["dependencies"]
    httpx_deps = [d for d in dependencias if re.match(r"httpx\s*==", d)]
    assert httpx_deps, "httpx precisa estar em [project].dependencies com versão fixada (==)"
