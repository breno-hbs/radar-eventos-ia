"""Testes do ticket F1-03 — acesso à rede do coletor Luma.

Cobrem `radar.fontes.rede` (User-Agent, cliente HTTP, robots.txt) e as regras de
requisição de `radar.fontes.luma.coletar_luma`: robots.txt lido uma vez, uma requisição por
calendário, só `https://api.lu.ma`, sem seguir redirecionamento, sem repetição e sem pausa.
Nenhuma requisição sai da máquina: tudo passa por `httpx.MockTransport`.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "fixtures" / "luma"
USER_AGENT = "radar-eventos-ia/0.1 (+https://github.com/breno-hbs/radar-eventos-ia)"
ROBOTS = "https://api.lu.ma/robots.txt"
ROBOTS_LIBERADO = "User-agent: *\nAllow: /\n"

CALENDARIOS_REAIS: tuple[tuple[str, str, str], ...] = (
    ("ai-collective", "calendar", "cal-E74MDlDKBaeAwXK"),
    ("codex-sp", "calendar", "cal-sAAnGigXnTQWpw6"),
    ("vibe-space", "calendar", "cal-s9CExmUfS7kMu50"),
    ("womakerscode", "calendar", "cal-K9IcbN14WTbkzOs"),
    ("mug-sp", "calendar", "cal-HtbK5aYvR5B2WAu"),
    ("cidade-sp", "discover", "discplace-AQZnCu9wl4LmOIp"),
)


@pytest.fixture
def rede() -> ModuleType:
    return importlib.import_module("radar.fontes.rede")


@pytest.fixture
def luma() -> ModuleType:
    return importlib.import_module("radar.fontes.luma")


@pytest.fixture
def calendarios() -> list[Any]:
    modulo = importlib.import_module("radar.fontes.calendarios")
    return [
        modulo.CalendarioLuma(slug=slug, nome=slug, entidade=entidade, id=ident)
        for slug, entidade, ident in CALENDARIOS_REAIS
    ]


def _ics(slug: str) -> httpx.Response:
    return httpx.Response(
        200,
        text=(FIXTURES / f"{slug}.ics").read_text(encoding="utf-8"),
        headers={"content-type": "text/calendar; charset=utf-8"},
    )


class _Servidor:
    """Transporte falso: robots.txt configurável, feeds das fixtures e registro de pedidos."""

    def __init__(
        self,
        luma: ModuleType,
        calendarios: list[Any],
        robots: httpx.Response | Exception | None = None,
        extras: dict[str, httpx.Response] | None = None,
    ) -> None:
        self.rotas: dict[str, httpx.Response | Exception] = {
            ROBOTS: robots if robots is not None else httpx.Response(200, text=ROBOTS_LIBERADO)
        }
        for calendario in calendarios:
            self.rotas[luma.url_ical(calendario)] = _ics(calendario.slug)
        self.rotas.update(extras or {})
        self.pedidos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        resposta = self.rotas.get(str(request.url), httpx.Response(404))
        if isinstance(resposta, httpx.HTTPError):
            raise type(resposta)(str(resposta), request=request)
        if isinstance(resposta, Exception):
            raise resposta
        # Cópia nova a cada pedido: a mesma resposta pode servir a vários calendários.
        return httpx.Response(
            resposta.status_code, headers=resposta.headers, content=resposta.content
        )

    def cliente(self, **opcoes: Any) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self), headers={"User-Agent": USER_AGENT}, **opcoes
        )

    def urls(self) -> list[str]:
        return [str(p.url) for p in self.pedidos]

    def feeds_pedidos(self) -> list[str]:
        return [u for u in self.urls() if u != ROBOTS]


def _coletar(luma: ModuleType, servidor: _Servidor, calendarios: list[Any]) -> list[Any]:
    with servidor.cliente() as cliente:
        return luma.coletar_luma(cliente, calendarios)


# =====================================================================================
# rede: User-Agent e cliente
# =====================================================================================


def test_user_agent_identifica_o_projeto(rede: ModuleType) -> None:
    assert rede.USER_AGENT == USER_AGENT


def test_criar_cliente_configura_user_agent_timeout_e_sem_redirecionamento(
    rede: ModuleType,
) -> None:
    cliente = rede.criar_cliente()
    try:
        assert isinstance(cliente, httpx.Client)
        assert cliente.headers["User-Agent"] == USER_AGENT
        assert cliente.follow_redirects is False
        assert cliente.timeout == httpx.Timeout(30)
    finally:
        cliente.close()


def test_rede_expoe_robots_txt(rede: ModuleType) -> None:
    assert isinstance(rede.RobotsTxt, type)


# =====================================================================================
# robots.txt
# =====================================================================================


def test_robots_lido_uma_vez_antes_dos_feeds(luma: ModuleType, calendarios: list[Any]) -> None:
    servidor = _Servidor(luma, calendarios)
    resultados = _coletar(luma, servidor, calendarios)

    assert servidor.urls().count(ROBOTS) == 1
    assert servidor.urls()[0] == ROBOTS
    assert all(r.situacao == "ok" for r in resultados)


@pytest.mark.parametrize(
    "corpo",
    [
        "User-agent: *\nDisallow: /ics\n",
        "User-agent: *\nDisallow: /\n",
        "User-agent: *\nDisallow:\n\nUser-agent: radar-eventos-ia\nDisallow: /ics\n",
    ],
    ids=["disallow-ics", "disallow-tudo", "regra-para-o-nosso-agente"],
)
def test_robots_que_proibe_bloqueia_todos_sem_pedir_feeds(
    luma: ModuleType, calendarios: list[Any], corpo: str
) -> None:
    servidor = _Servidor(luma, calendarios, robots=httpx.Response(200, text=corpo))
    resultados = _coletar(luma, servidor, calendarios)

    assert servidor.feeds_pedidos() == []
    assert servidor.urls() == [ROBOTS]
    assert [r.calendario for r in resultados] == [c.slug for c in calendarios]
    for resultado in resultados:
        assert resultado.situacao == "falha", resultado.calendario
        assert resultado.eventos == [], resultado.calendario
        assert resultado.falha.motivo == "bloqueado_robots", resultado.calendario


@pytest.mark.parametrize("status", [404, 410])
def test_robots_ausente_libera(luma: ModuleType, calendarios: list[Any], status: int) -> None:
    servidor = _Servidor(luma, calendarios, robots=httpx.Response(status))
    resultados = _coletar(luma, servidor, calendarios)

    assert len(servidor.feeds_pedidos()) == len(calendarios)
    assert all(r.situacao == "ok" for r in resultados)


def test_robots_que_permite_outro_caminho_libera(luma: ModuleType, calendarios: list[Any]) -> None:
    corpo = "User-agent: *\nDisallow: /admin\n"
    servidor = _Servidor(luma, calendarios, robots=httpx.Response(200, text=corpo))
    resultados = _coletar(luma, servidor, calendarios)

    assert len(servidor.feeds_pedidos()) == len(calendarios)
    assert all(r.situacao == "ok" for r in resultados)


@pytest.mark.parametrize(
    "robots",
    [
        httpx.Response(503),
        httpx.Response(500),
        httpx.Response(403),
        httpx.Response(401),
        httpx.Response(301, headers={"location": "https://luma.com/robots.txt"}),
        httpx.ConnectError("sem rede"),
        httpx.ConnectTimeout("timeout"),
    ],
    ids=["503", "500", "403", "401", "301", "erro-de-conexao", "timeout"],
)
def test_robots_indisponivel_bloqueia_por_seguranca(
    luma: ModuleType, calendarios: list[Any], robots: httpx.Response | Exception
) -> None:
    servidor = _Servidor(luma, calendarios, robots=robots)
    resultados = _coletar(luma, servidor, calendarios)

    assert servidor.feeds_pedidos() == []
    assert servidor.urls().count(ROBOTS) == 1
    for resultado in resultados:
        assert resultado.situacao == "falha", resultado.calendario
        assert resultado.falha.motivo == "bloqueado_robots", resultado.calendario


# =====================================================================================
# Regras de requisição
# =====================================================================================


def test_uma_requisicao_por_calendario_com_url_montada_do_yaml(
    luma: ModuleType, calendarios: list[Any]
) -> None:
    servidor = _Servidor(luma, calendarios)
    _coletar(luma, servidor, calendarios)

    feeds = servidor.feeds_pedidos()
    assert len(feeds) == len(calendarios)
    assert sorted(feeds) == sorted(
        f"https://api.lu.ma/ics/get?entity={entidade}&id={ident}"
        for _, entidade, ident in CALENDARIOS_REAIS
    )


def test_todas_as_requisicoes_usam_user_agent_e_so_api_lu_ma_em_https(
    luma: ModuleType, calendarios: list[Any]
) -> None:
    servidor = _Servidor(luma, calendarios)
    _coletar(luma, servidor, calendarios)

    assert servidor.pedidos
    for pedido in servidor.pedidos:
        assert pedido.url.scheme == "https", str(pedido.url)
        assert pedido.url.host == "api.lu.ma", str(pedido.url)
        assert pedido.method == "GET", str(pedido.url)
        assert pedido.headers["User-Agent"] == USER_AGENT, str(pedido.url)


@pytest.mark.parametrize(
    "falha",
    [
        httpx.Response(500),
        httpx.Response(503),
        httpx.Response(429, headers={"retry-after": "1"}),
        httpx.ConnectTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
    ],
    ids=["500", "503", "429", "connect-timeout", "read-timeout"],
)
def test_sem_repeticao_depois_de_erro(
    luma: ModuleType, calendarios: list[Any], falha: httpx.Response | Exception
) -> None:
    servidor = _Servidor(luma, calendarios)
    for calendario in calendarios:
        servidor.rotas[luma.url_ical(calendario)] = falha
    resultados = _coletar(luma, servidor, calendarios)

    feeds = servidor.feeds_pedidos()
    assert len(feeds) == len(calendarios)
    assert len(set(feeds)) == len(feeds)
    assert all(r.situacao == "falha" for r in resultados)


def test_429_e_falha_de_status_http(luma: ModuleType, calendarios: list[Any]) -> None:
    escolhido = calendarios[0]
    servidor = _Servidor(luma, calendarios, extras={luma.url_ical(escolhido): httpx.Response(429)})
    resultados = _coletar(luma, servidor, calendarios)

    resultado = next(r for r in resultados if r.calendario == escolhido.slug)
    assert resultado.falha.motivo == "status_http"
    assert resultado.falha.status_http == 429


def test_nao_segue_redirecionamento_mesmo_com_cliente_que_seguiria(
    luma: ModuleType, calendarios: list[Any]
) -> None:
    mug = next(c for c in calendarios if c.slug == "mug-sp")
    destino = "https://api.lu.ma/ics/get?entity=calendar&id=cal-OutroDestino1"
    servidor = _Servidor(
        luma,
        [mug],
        extras={
            luma.url_ical(mug): httpx.Response(302, headers={"location": destino}),
            destino: _ics("mug-sp"),
        },
    )
    with servidor.cliente(follow_redirects=True) as cliente:
        (resultado,) = luma.coletar_luma(cliente, [mug])

    assert destino not in servidor.urls()
    assert resultado.situacao == "falha"
    assert resultado.falha.motivo == "redirecionamento"
    assert resultado.falha.status_http == 302


def test_robots_tambem_nao_segue_redirecionamento(luma: ModuleType, calendarios: list[Any]) -> None:
    destino = "https://api.lu.ma/outro-robots.txt"
    servidor = _Servidor(
        luma,
        calendarios,
        robots=httpx.Response(302, headers={"location": destino}),
        extras={destino: httpx.Response(200, text=ROBOTS_LIBERADO)},
    )
    with servidor.cliente(follow_redirects=True) as cliente:
        resultados = luma.coletar_luma(cliente, calendarios)

    assert destino not in servidor.urls()
    assert servidor.feeds_pedidos() == []
    assert all(r.falha.motivo == "bloqueado_robots" for r in resultados)


def test_coleta_nao_faz_pausa(
    luma: ModuleType,
    rede: ModuleType,
    calendarios: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def proibido(_segundos: float) -> None:
        raise AssertionError("coletar_luma não deve pausar entre requisições")

    monkeypatch.setattr(time, "sleep", proibido)
    for modulo in (luma, rede):
        if hasattr(modulo, "sleep"):
            monkeypatch.setattr(modulo, "sleep", proibido)

    servidor = _Servidor(luma, calendarios)
    resultados = _coletar(luma, servidor, calendarios)

    assert all(r.situacao == "ok" for r in resultados)


@pytest.mark.parametrize(
    "erro",
    [
        httpx.DecodingError("conteúdo comprimido inválido"),
        httpx.TooManyRedirects("redirecionamentos demais"),
        httpx.RemoteProtocolError("protocolo"),
        httpx.ReadError("leitura"),
    ],
    ids=["decoding", "too-many-redirects", "remote-protocol", "read"],
)
def test_qualquer_httpx_http_error_vira_falha_de_rede(
    luma: ModuleType, calendarios: list[Any], erro: Exception
) -> None:
    escolhido = calendarios[0]
    servidor = _Servidor(luma, calendarios)
    servidor.rotas[luma.url_ical(escolhido)] = erro
    resultados = _coletar(luma, servidor, calendarios)

    por_slug = {r.calendario: r for r in resultados}
    falha = por_slug[escolhido.slug].falha
    assert por_slug[escolhido.slug].situacao == "falha"
    assert falha.motivo == "rede"
    assert falha.status_http is None
    assert servidor.feeds_pedidos().count(luma.url_ical(escolhido)) == 1
    for calendario in calendarios[1:]:
        assert por_slug[calendario.slug].situacao == "ok", calendario.slug


# --- RobotsTxt.ilegivel (F1-08) -------------------------------------------------------


def _robots(rede: ModuleType, resposta: Any) -> Any:
    def tratador(requisicao: httpx.Request) -> httpx.Response:
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    with httpx.Client(transport=httpx.MockTransport(tratador)) as cliente:
        return rede.RobotsTxt.ler(cliente, "https://luma.com")


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        (httpx.Response(200, text="User-agent: *\nDisallow: /x\n"), False),
        (httpx.Response(404), False),
        (httpx.Response(410), False),
        (httpx.Response(500), True),
        (httpx.Response(403), True),
        (httpx.ConnectError("sem rede"), True),
    ],
    ids=["200", "404", "410", "500", "403", "erro"],
)
def test_robots_ilegivel(rede: ModuleType, resposta: Any, esperado: bool) -> None:
    assert _robots(rede, resposta).ilegivel is esperado
