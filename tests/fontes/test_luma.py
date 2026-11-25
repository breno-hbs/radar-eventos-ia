"""Testes do ticket F1-03 — coletor Luma (iCal → `EventoNormalizado`).

Cobrem `radar.fontes.modelos` e `radar.fontes.luma` sobre as fixtures reais de
`tests/fixtures/luma/` (capturadas no F1-02). Variações (cancelado, campo ausente, UID fora
do padrão etc.) são montadas em memória a partir dessas fixtures; nenhuma fixture nova é
criada e nenhum teste acessa a rede (respostas HTTP vêm de `httpx.MockTransport`).

Os módulos são importados dentro de fixtures para que a falta da implementação apareça
teste a teste, e não como erro de coleta do arquivo inteiro.
"""

from __future__ import annotations

import importlib
import json
import re
import tomllib
import unicodedata
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "fixtures" / "luma"
SP = ZoneInfo("America/Sao_Paulo")
USER_AGENT = "radar-eventos-ia/0.1 (+https://github.com/breno-hbs/radar-eventos-ia)"

FEEDS = ("ai-collective", "codex-sp", "vibe-space", "womakerscode", "mug-sp", "cidade-sp")
CALENDARIOS_REAIS: tuple[tuple[str, str, str], ...] = (
    ("ai-collective", "calendar", "cal-E74MDlDKBaeAwXK"),
    ("codex-sp", "calendar", "cal-sAAnGigXnTQWpw6"),
    ("vibe-space", "calendar", "cal-s9CExmUfS7kMu50"),
    ("womakerscode", "calendar", "cal-K9IcbN14WTbkzOs"),
    ("mug-sp", "calendar", "cal-HtbK5aYvR5B2WAu"),
    ("cidade-sp", "discover", "discplace-AQZnCu9wl4LmOIp"),
)
# TENTATIVE é o padrão da Luma em 100% dos eventos e não informa nada: vira "desconhecido".
STATUS_VALIDOS = {"confirmado", "cancelado", "desconhecido"}
PADRAO_ID_FONTE = re.compile(r"^(evt|calev)-[A-Za-z0-9]+$")
PADRAO_LINK = re.compile(r"^https://luma\.com/[A-Za-z0-9_-]+$")
ROBOTS_LIBERADO = "User-agent: *\nAllow: /\n"
# Marcador que só existe no corpo das respostas; nunca pode aparecer no resultado.
MARCADOR = "CONTEUDO-DO-FEED-7f3a9c"


# --- Módulos (import tardio) ---------------------------------------------------------


@pytest.fixture
def modelos() -> ModuleType:
    return importlib.import_module("radar.fontes.modelos")


@pytest.fixture
def luma() -> ModuleType:
    return importlib.import_module("radar.fontes.luma")


@pytest.fixture
def calendarios_mod() -> ModuleType:
    return importlib.import_module("radar.fontes.calendarios")


# --- Utilitários de iCal em memória ---------------------------------------------------


def _ler_fixture(slug: str) -> str:
    return (FIXTURES / f"{slug}.ics").read_text(encoding="utf-8")


def _nome_propriedade(linha: str) -> str:
    return re.split(r"[;:]", linha, maxsplit=1)[0]


def _editar_evento(texto: str, uid: str | None, editar: Callable[[list[str]], list[str]]) -> str:
    """Aplica `editar` às linhas do VEVENT com esse UID (ou do primeiro, se `uid` é None)."""
    linhas = texto.split("\n")
    saida: list[str] = []
    feito = False
    i = 0
    while i < len(linhas):
        if linhas[i] == "BEGIN:VEVENT":
            fim = linhas.index("END:VEVENT", i)
            bloco = linhas[i : fim + 1]
            if not feito and (uid is None or f"UID:{uid}@events.lu.ma" in bloco):
                bloco = editar(bloco)
                feito = True
            saida.extend(bloco)
            i = fim + 1
        else:
            saida.append(linhas[i])
            i += 1
    assert feito, f"evento {uid} não encontrado na fixture"
    return "\n".join(saida)


def _sem(nome: str) -> Callable[[list[str]], list[str]]:
    """Remove a propriedade `nome` (e as linhas de continuação da dobra)."""

    def editar(bloco: list[str]) -> list[str]:
        saida: list[str] = []
        pulando = False
        for linha in bloco:
            if linha.startswith(" ") and pulando:
                continue
            pulando = not linha.startswith(" ") and _nome_propriedade(linha) == nome
            if not pulando:
                saida.append(linha)
        return saida

    return editar


def _com(nome: str, nova_linha: str) -> Callable[[list[str]], list[str]]:
    """Substitui a propriedade `nome` por `nova_linha` (linha completa, já no formato iCal)."""

    def editar(bloco: list[str]) -> list[str]:
        sem = _sem(nome)(bloco)
        return [*sem[:-1], nova_linha, sem[-1]]

    return editar


def _encadear(*edicoes: Callable[[list[str]], list[str]]) -> Callable[[list[str]], list[str]]:
    def editar(bloco: list[str]) -> list[str]:
        for edicao in edicoes:
            bloco = edicao(bloco)
        return bloco

    return editar


def _sem_eventos(texto: str) -> str:
    linhas = texto.split("\n")
    saida: list[str] = []
    dentro = False
    for linha in linhas:
        if linha == "BEGIN:VEVENT":
            dentro = True
        if not dentro:
            saida.append(linha)
        if linha == "END:VEVENT":
            dentro = False
    return "\n".join(saida)


def _evento(resultado: Any, id_fonte: str) -> Any:
    achados = [e for e in resultado.eventos if e.id_fonte == id_fonte]
    assert len(achados) == 1, f"esperava 1 evento {id_fonte}, achei {len(achados)}"
    return achados[0]


def _ler(luma: ModuleType, slug: str, texto: str | None = None) -> Any:
    return luma.ler_ical(_ler_fixture(slug) if texto is None else texto, slug)


def _e_horario_de_sp(valor: datetime) -> bool:
    return getattr(valor.tzinfo, "key", None) == "America/Sao_Paulo"


# --- Utilitários de HTTP falso -----------------------------------------------------------


def _calendarios(calendarios_mod: ModuleType) -> list[Any]:
    return [
        calendarios_mod.CalendarioLuma(slug=slug, nome=slug, entidade=entidade, id=ident)
        for slug, entidade, ident in CALENDARIOS_REAIS
    ]


def _resposta_ics(texto: str) -> httpx.Response:
    return httpx.Response(200, text=texto, headers={"content-type": "text/calendar; charset=utf-8"})


class _Servidor:
    """Transporte falso: responde por URL e registra cada requisição."""

    def __init__(self, rotas: dict[str, httpx.Response | Exception]) -> None:
        self.rotas = {"https://api.lu.ma/robots.txt": httpx.Response(200, text=ROBOTS_LIBERADO)}
        self.rotas.update(rotas)
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

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), headers={"User-Agent": USER_AGENT})


def _url(luma: ModuleType, calendarios: list[Any], slug: str) -> str:
    return luma.url_ical(next(c for c in calendarios if c.slug == slug))


# =====================================================================================
# Critério de aceite: evento com endereço
# =====================================================================================


def test_evento_com_endereco_mug_sp_tem_todos_os_campos(luma: ModuleType) -> None:
    resultado = _ler(luma, "mug-sp")

    assert resultado.situacao == "ok"
    assert resultado.falha is None
    assert resultado.eventos_invalidos == []
    assert len(resultado.eventos) == 1
    evento = resultado.eventos[0]

    assert evento.fonte == "luma"
    assert evento.calendario == "mug-sp"
    assert evento.id_fonte == "evt-AXkUVWCxURYLxoT"
    assert evento.titulo == "MUG São Paulo NOV 2026 - Vector Search na Prática & mais"
    assert evento.dia_inteiro is False
    assert evento.endereco_oculto is False
    assert evento.local_texto == (
        "FIAP - Paulista, Av. Paulista, 1106 - 7º andar - Bela Vista, "
        "São Paulo - SP, 01311-000, Brazil"
    )
    assert evento.latitude == pytest.approx(-23.564084299999998)
    assert evento.longitude == pytest.approx(-46.6523865)
    assert evento.local_url is None
    assert evento.status_fonte == "desconhecido"
    assert evento.link_inscricao == "https://luma.com/afm6b12v"
    assert evento.descricao == (
        "Get up-to-date information at: https://luma.com/afm6b12v\n\n"
        "Address:\nFIAP - Paulista\nSão Paulo, SP\nBrazil\n\n"
        "Hosted by MongoDB Community & 3 others"
    )
    assert evento.organizador == "MongoDB Community"


def test_horario_utc_vira_horario_de_sao_paulo(luma: ModuleType) -> None:
    evento = _ler(luma, "mug-sp").eventos[0]

    # DTSTART:20261125T210000Z → 18:00 -03:00; DTEND:20261126T000000Z → 21:00 -03:00.
    assert evento.inicio == datetime(2026, 11, 25, 18, 0, tzinfo=SP)
    assert evento.inicio.hour == 18
    assert evento.inicio.utcoffset() == timedelta(hours=-3)
    assert _e_horario_de_sp(evento.inicio)
    assert evento.fim == datetime(2026, 11, 25, 21, 0, tzinfo=SP)
    assert evento.fim.hour == 21
    assert _e_horario_de_sp(evento.fim)


def test_local_texto_vem_desdobrado_sem_quebra_da_dobra(luma: ModuleType) -> None:
    # Na fixture o LOCATION é dobrado no meio de "São" ("Sã" + "\n o Paulo").
    evento = _ler(luma, "mug-sp").eventos[0]

    assert "\n" not in evento.local_texto
    assert "São Paulo" in evento.local_texto


def test_organizador_e_so_o_cn_sem_mailto(luma: ModuleType) -> None:
    evento = _ler(luma, "mug-sp").eventos[0]

    assert evento.organizador == "MongoDB Community"
    assert "MAILTO" not in evento.organizador.upper()
    assert "redigido" not in evento.organizador
    assert "@" not in evento.organizador


def test_organizador_sem_cn_fica_none_e_email_nunca_e_lido(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("mug-sp"),
        None,
        _com("ORGANIZER", "ORGANIZER:MAILTO:pessoa.organizadora@exemplo.invalid"),
    )
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.eventos[0].organizador is None
    assert "pessoa.organizadora" not in resultado.model_dump_json()


def test_email_do_organizador_nao_aparece_em_nenhum_campo(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("mug-sp"),
        None,
        _com("ORGANIZER", 'ORGANIZER;CN="Comunidade X":MAILTO:pessoa.organizadora@exemplo.invalid'),
    )
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.eventos[0].organizador == "Comunidade X"
    assert "pessoa.organizadora" not in resultado.model_dump_json()


# =====================================================================================
# Critério de aceite: endereço oculto (LOCATION = https://luma.com/event/evt-…)
# =====================================================================================


def test_endereco_oculto_cidade_sp_devin_meetup(luma: ModuleType) -> None:
    evento = _evento(_ler(luma, "cidade-sp"), "evt-YEkDcmJq3cWyjK5")

    assert evento.titulo == "Devin Meetup São Paulo"
    assert evento.endereco_oculto is True
    assert evento.local_texto is None
    # O GEO aproximado continua valendo mesmo com endereço oculto.
    assert evento.latitude == pytest.approx(-23.599598324175613)
    assert evento.longitude == pytest.approx(-46.678163236002376)
    assert evento.link_inscricao == "https://luma.com/devin-sp-4"


def test_endereco_oculto_womakerscode_tech_talks(luma: ModuleType) -> None:
    evento = _evento(_ler(luma, "womakerscode"), "evt-zw9QXKGtbTeDlev")

    assert evento.titulo == "TECH TALKS - WoMakersCode"
    assert evento.endereco_oculto is True
    assert evento.local_texto is None
    assert evento.latitude == pytest.approx(-23.558579263837412)
    assert evento.longitude == pytest.approx(-46.657180326392734)
    assert evento.link_inscricao == "https://luma.com/m37dv3hg"


def test_location_com_url_de_evento_de_outro_formato_nao_e_endereco_oculto(
    luma: ModuleType,
) -> None:
    # Só ^https://luma\.com/event/evt-[A-Za-z0-9]+$ é endereço oculto; outra URL vai para
    # `local_url`.
    texto = _editar_evento(
        _ler_fixture("mug-sp"),
        None,
        _com("LOCATION", "LOCATION:https://luma.com/event/evt-AXkUVWCxURYLxoT/extra"),
    )
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.endereco_oculto is False
    assert evento.local_texto is None
    assert evento.local_url == "https://luma.com/event/evt-AXkUVWCxURYLxoT/extra"


@pytest.mark.parametrize("linha", ["LOCATION:", None], ids=["vazio", "ausente"])
def test_location_vazio_ou_ausente_vira_none(luma: ModuleType, linha: str | None) -> None:
    edicao = _sem("LOCATION") if linha is None else _com("LOCATION", linha)
    texto = _editar_evento(_ler_fixture("mug-sp"), None, edicao)
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.local_texto is None
    assert evento.local_url is None
    assert evento.endereco_oculto is False


# =====================================================================================
# Critério de aceite: sem GEO
# =====================================================================================


def test_sem_geo_womakerscode_capture_the_flag(luma: ModuleType) -> None:
    evento = _evento(_ler(luma, "womakerscode"), "evt-LYJDk4jOEadCy2q")

    assert evento.titulo == "[WoMakers + FIAP] - Capture The Flag! ♥"
    assert evento.latitude is None
    assert evento.longitude is None
    assert evento.local_texto == "FIAP - Paulista - 1° andar"
    assert evento.endereco_oculto is False
    assert evento.link_inscricao == "https://luma.com/zn0zld55"


def test_sem_geo_e_endereco_oculto_ai_collective(luma: ModuleType) -> None:
    evento = _evento(_ler(luma, "ai-collective"), "evt-YDkpPkYuPBXIpCH")

    assert evento.latitude is None
    assert evento.longitude is None
    assert evento.endereco_oculto is True
    assert evento.local_texto is None
    assert evento.link_inscricao == "https://luma.com/11-21"


@pytest.mark.parametrize(
    "linha_geo",
    [
        "GEO:abc",
        "GEO:-23.5",
        "GEO:95.0;-46.6",
        "GEO:-23.5;-200.0",
    ],
    ids=["texto", "so-latitude", "latitude-fora-da-faixa", "longitude-fora-da-faixa"],
)
def test_geo_malformado_ou_fora_da_faixa_vira_none_sem_invalidar_evento(
    luma: ModuleType, linha_geo: str
) -> None:
    texto = _editar_evento(_ler_fixture("mug-sp"), None, _com("GEO", linha_geo))
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "ok"
    assert resultado.eventos_invalidos == []
    evento = resultado.eventos[0]
    assert evento.latitude is None
    assert evento.longitude is None


# =====================================================================================
# Critério de aceite: cancelado
# =====================================================================================


def test_evento_cancelado(luma: ModuleType) -> None:
    # Nas fixtures reais não há nenhum STATUS:CANCELLED: `grep -rn CANCELLED tests/fixtures/`
    # não acha nada e os 550 VEVENT têm STATUS:TENTATIVE (conferido ao escrever o teste).
    # Por isso o caso é montado em memória trocando o STATUS do evento do mug-sp.
    original = _ler_fixture("mug-sp")
    assert "STATUS:TENTATIVE" in original
    texto = original.replace("STATUS:TENTATIVE", "STATUS:CANCELLED")
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "ok"
    evento = resultado.eventos[0]
    assert evento.status_fonte == "cancelado"
    assert evento.id_fonte == "evt-AXkUVWCxURYLxoT"


@pytest.mark.parametrize(
    ("edicao", "esperado"),
    [
        (_com("STATUS", "STATUS:CONFIRMED"), "confirmado"),
        (_com("STATUS", "STATUS:TENTATIVE"), "desconhecido"),
        (_com("STATUS", "STATUS:CANCELLED"), "cancelado"),
        (_sem("STATUS"), "desconhecido"),
        (_com("STATUS", "STATUS:ADIADO"), "desconhecido"),
    ],
    ids=["confirmed", "tentative", "cancelled", "ausente", "valor-estranho"],
)
def test_mapeamento_de_status(
    luma: ModuleType, edicao: Callable[[list[str]], list[str]], esperado: str
) -> None:
    texto = _editar_evento(_ler_fixture("mug-sp"), None, edicao)
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.status_fonte == esperado


# =====================================================================================
# calev-, dia inteiro, link dobrado, descrição
# =====================================================================================


def test_evento_calev_womakerscode(luma: ModuleType) -> None:
    evento = _evento(_ler(luma, "womakerscode"), "calev-BlWaHsESv15VkJo")

    assert evento.titulo == "Encontro de Mulheres e Carreira em Tecnologia"
    # O link da DESCRIPTION aponta para a página do calendário; a inscrição é a URL do
    # LOCATION, que vai para `local_url`.
    assert evento.link_inscricao == "https://www.womakerscode.org/encontro"
    assert evento.local_url == "https://www.womakerscode.org/encontro"
    assert evento.local_texto is None
    assert evento.endereco_oculto is False
    assert evento.latitude == pytest.approx(-23.5572348)
    assert evento.longitude == pytest.approx(-46.6369578)


def test_evento_de_dia_inteiro_vibe_space(luma: ModuleType) -> None:
    # DTSTART;VALUE=DATE:20241007 e DTEND;VALUE=DATE:20241012 (fim exclusivo).
    evento = _evento(_ler(luma, "vibe-space"), "evt-pG3Y6C6KDBcjfaA")

    assert evento.titulo == "SF AI2Web3 Bootcamp"
    assert evento.dia_inteiro is True
    assert isinstance(evento.inicio, datetime)
    assert evento.inicio == datetime(2024, 10, 7, 0, 0, tzinfo=SP)
    assert _e_horario_de_sp(evento.inicio)
    assert evento.fim == datetime(2024, 10, 12, 0, 0, tzinfo=SP)
    assert _e_horario_de_sp(evento.fim)


def test_so_eventos_value_date_sao_dia_inteiro(luma: ModuleType) -> None:
    # vibe-space.ics tem exatamente 5 VEVENT com DTSTART;VALUE=DATE.
    resultado = _ler(luma, "vibe-space")

    assert sum(e.dia_inteiro for e in resultado.eventos) == 5
    assert _evento(resultado, "evt-AOI6VlcZHu1TqSn").dia_inteiro is True


def test_link_dobrado_sai_inteiro_gaico_park_hang(luma: ModuleType) -> None:
    # Na fixture a URL é dobrada: "https://luma.com/gaico-park-han" + "\n g".
    evento = _evento(_ler(luma, "ai-collective"), "evt-SchiWMpl0UoJ3V2")

    assert evento.titulo == "GAICo Park Hang"
    assert evento.link_inscricao == "https://luma.com/gaico-park-hang"


def test_link_ignora_urls_de_event_e_user(luma: ModuleType) -> None:
    descricao = (
        "DESCRIPTION:Organizado por https://luma.com/user/usr-abc123\\n"
        "Endereço em https://luma.com/event/evt-AXkUVWCxURYLxoT\\n"
        "Inscrição: https://luma.com/meu-evento-sp\\nOutro: https://luma.com/segundo"
    )
    texto = _editar_evento(_ler_fixture("mug-sp"), None, _com("DESCRIPTION", descricao))
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.link_inscricao == "https://luma.com/meu-evento-sp"


def test_descricao_sem_link_da_luma_deixa_link_none(luma: ModuleType) -> None:
    descricao = "DESCRIPTION:Mais em https://exemplo.invalid/evento\\nSem link da Luma"
    texto = _editar_evento(_ler_fixture("mug-sp"), None, _com("DESCRIPTION", descricao))
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.link_inscricao is None
    assert evento.descricao == "Mais em https://exemplo.invalid/evento\nSem link da Luma"


def test_sem_description_descricao_e_link_ficam_none(luma: ModuleType) -> None:
    texto = _editar_evento(_ler_fixture("mug-sp"), None, _sem("DESCRIPTION"))
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "ok"
    evento = resultado.eventos[0]
    assert evento.descricao is None
    assert evento.link_inscricao is None


def test_descricao_sem_escapes_do_ical(luma: ModuleType) -> None:
    descricao = "DESCRIPTION:Linha 1\\nLinha 2\\, com vírgula\\; e ponto e vírgula"
    texto = _editar_evento(_ler_fixture("mug-sp"), None, _com("DESCRIPTION", descricao))
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.descricao == "Linha 1\nLinha 2, com vírgula; e ponto e vírgula"


# =====================================================================================
# Fixtures inteiras: contagem e invariantes
# =====================================================================================


def _aprovados_da_captura() -> dict[str, int]:
    captura = json.loads((FIXTURES / "captura.json").read_text(encoding="utf-8"))
    return {slug: d["aprovados"] for slug, d in captura["feeds_detalhe"].items()}


@pytest.mark.parametrize("slug", FEEDS)
def test_contagem_bate_com_aprovados_da_captura(luma: ModuleType, slug: str) -> None:
    resultado = _ler(luma, slug)

    assert resultado.situacao == "ok"
    assert resultado.falha is None
    assert resultado.calendario == slug
    assert len(resultado.eventos) > 0
    total = len(resultado.eventos) + len(resultado.eventos_invalidos)
    assert total == _aprovados_da_captura()[slug]


@pytest.mark.parametrize("slug", FEEDS)
def test_invariantes_em_todos_os_eventos_das_fixtures(luma: ModuleType, slug: str) -> None:
    resultado = _ler(luma, slug)

    for evento in resultado.eventos:
        rotulo = f"{slug}/{evento.id_fonte}"
        assert evento.fonte == "luma", rotulo
        assert evento.calendario == slug, rotulo
        assert PADRAO_ID_FONTE.match(evento.id_fonte), rotulo
        assert evento.titulo.strip(), rotulo
        assert _e_horario_de_sp(evento.inicio), rotulo
        if evento.fim is not None:
            assert _e_horario_de_sp(evento.fim), rotulo
            assert evento.fim >= evento.inicio, rotulo
        assert evento.status_fonte in STATUS_VALIDOS, rotulo
        assert (evento.latitude is None) == (evento.longitude is None), rotulo
        if evento.endereco_oculto:
            assert evento.local_texto is None, rotulo
        if evento.endereco_oculto:
            assert evento.local_url is None, rotulo
        if evento.local_texto is not None:
            assert evento.local_texto.strip(), rotulo
            assert "\n" not in evento.local_texto, rotulo
            assert not re.match(r"^https?://", evento.local_texto), rotulo
        if evento.local_url is not None:
            assert evento.local_url.startswith("https://"), rotulo
        assert evento.link_inscricao is not None, rotulo
        if evento.id_fonte.startswith("evt-"):
            # Todas as DESCRIPTION das fixtures têm exatamente um link luma.com/<slug>.
            assert PADRAO_LINK.match(evento.link_inscricao), rotulo
        else:
            assert evento.link_inscricao == evento.local_url, rotulo
        if evento.descricao is not None:
            assert "\\n" not in evento.descricao, rotulo
            assert "\\," not in evento.descricao, rotulo
        if evento.organizador is not None:
            assert "MAILTO" not in evento.organizador.upper(), rotulo
            assert "@" not in evento.organizador, rotulo
            assert "redigido" not in evento.organizador, rotulo


@pytest.mark.parametrize("slug", FEEDS)
def test_nenhum_email_do_organizador_vaza_no_resultado(luma: ModuleType, slug: str) -> None:
    resultado = _ler(luma, slug)

    assert "redigido@exemplo.invalid" not in resultado.model_dump_json()


# =====================================================================================
# Zero eventos e eventos inválidos
# =====================================================================================


def test_vcalendar_sem_vevent_e_vazio_e_nao_falha(luma: ModuleType) -> None:
    texto = _sem_eventos(_ler_fixture("mug-sp"))
    assert "BEGIN:VCALENDAR" in texto
    assert "VEVENT" not in texto
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "vazio"
    assert resultado.eventos == []
    assert resultado.eventos_invalidos == []
    assert resultado.falha is None
    assert resultado.calendario == "mug-sp"


def test_evento_sem_dtstart_e_invalido_e_os_outros_seguem(luma: ModuleType) -> None:
    texto = _editar_evento(_ler_fixture("cidade-sp"), "evt-oJnGMDJ7aiuvsu9", _sem("DTSTART"))
    resultado = _ler(luma, "cidade-sp", texto)

    assert resultado.situacao == "ok"
    assert resultado.falha is None
    assert len(resultado.eventos) == 11
    assert len(resultado.eventos_invalidos) == 1
    invalido = resultado.eventos_invalidos[0]
    assert invalido.id_fonte == "evt-oJnGMDJ7aiuvsu9"
    assert invalido.motivo == "inicio_ausente"
    assert "evt-oJnGMDJ7aiuvsu9" not in {e.id_fonte for e in resultado.eventos}


def test_todos_os_eventos_invalidos_vira_falha_formato_invalido(luma: ModuleType) -> None:
    texto = _editar_evento(_ler_fixture("mug-sp"), None, _sem("DTSTART"))
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "falha"
    assert resultado.eventos == []
    assert resultado.falha is not None
    assert resultado.falha.motivo == "formato_invalido"


@pytest.mark.parametrize(
    "edicao",
    [
        _com("UID", "UID:outro-AXkUVWCxURYLxoT@events.lu.ma"),
        _com("UID", "UID:evt-AXkUVWCxURYLxoT@exemplo.invalid"),
        _com("UID", "UID:evt-AXk_UVW@events.lu.ma"),
        _sem("UID"),
    ],
    ids=["prefixo-desconhecido", "dominio-errado", "caractere-invalido", "ausente"],
)
def test_uid_fora_do_padrao_ou_ausente_e_invalido(
    luma: ModuleType, edicao: Callable[[list[str]], list[str]]
) -> None:
    texto = _editar_evento(_ler_fixture("cidade-sp"), "evt-AXkUVWCxURYLxoT", edicao)
    resultado = _ler(luma, "cidade-sp", texto)

    assert resultado.situacao == "ok"
    assert len(resultado.eventos) == 11
    assert [i.motivo for i in resultado.eventos_invalidos] == ["uid_ausente_ou_fora_do_padrao"]
    assert "evt-AXkUVWCxURYLxoT" not in {e.id_fonte for e in resultado.eventos}


@pytest.mark.parametrize(
    "edicao", [_sem("SUMMARY"), _com("SUMMARY", "SUMMARY:   ")], ids=["ausente", "em-branco"]
)
def test_titulo_ausente_ou_vazio_e_invalido(
    luma: ModuleType, edicao: Callable[[list[str]], list[str]]
) -> None:
    texto = _editar_evento(_ler_fixture("cidade-sp"), "evt-AXkUVWCxURYLxoT", edicao)
    resultado = _ler(luma, "cidade-sp", texto)

    assert len(resultado.eventos) == 11
    assert len(resultado.eventos_invalidos) == 1
    assert resultado.eventos_invalidos[0].id_fonte == "evt-AXkUVWCxURYLxoT"
    assert resultado.eventos_invalidos[0].motivo == "titulo_ausente"


def test_dtstart_flutuante_sem_fuso_e_invalido(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("cidade-sp"),
        "evt-AXkUVWCxURYLxoT",
        _encadear(_com("DTSTART", "DTSTART:20261125T210000"), _sem("DTEND")),
    )
    resultado = _ler(luma, "cidade-sp", texto)

    assert len(resultado.eventos) == 11
    assert len(resultado.eventos_invalidos) == 1
    assert resultado.eventos_invalidos[0].id_fonte == "evt-AXkUVWCxURYLxoT"
    assert resultado.eventos_invalidos[0].motivo == "horario_sem_fuso"


def test_evento_invalido_nao_guarda_conteudo_do_feed(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("mug-sp"),
        None,
        _encadear(_sem("DTSTART"), _com("SUMMARY", f"SUMMARY:{MARCADOR}")),
    )
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "falha"
    assert MARCADOR not in resultado.model_dump_json()


# =====================================================================================
# url_ical
# =====================================================================================


def test_url_ical_de_calendario_e_de_cidade(luma: ModuleType, calendarios_mod: ModuleType) -> None:
    calendarios = _calendarios(calendarios_mod)

    assert (
        _url(luma, calendarios, "mug-sp")
        == "https://api.lu.ma/ics/get?entity=calendar&id=cal-HtbK5aYvR5B2WAu"
    )
    assert (
        _url(luma, calendarios, "cidade-sp")
        == "https://api.lu.ma/ics/get?entity=discover&id=discplace-AQZnCu9wl4LmOIp"
    )


# =====================================================================================
# coletar_luma: isolamento de erros por calendário
# =====================================================================================


def _servidor_misto(luma: ModuleType, calendarios: list[Any]) -> _Servidor:
    def url(slug: str) -> str:
        return _url(luma, calendarios, slug)

    return _Servidor(
        {
            url("ai-collective"): httpx.Response(500, text=f"erro interno {MARCADOR}"),
            url("codex-sp"): httpx.Response(
                302, headers={"location": "https://luma.com/login"}, text=MARCADOR
            ),
            url("vibe-space"): httpx.ConnectTimeout(f"timeout {MARCADOR}"),
            url("womakerscode"): httpx.Response(
                200,
                text=f"<html><body><h1>{MARCADOR}</h1></body></html>",
                headers={"content-type": "text/html"},
            ),
            url("mug-sp"): _resposta_ics(_ler_fixture("mug-sp")),
            url("cidade-sp"): _resposta_ics(_ler_fixture("cidade-sp")),
        }
    )


def test_um_feed_quebrado_nao_derruba_os_outros(
    luma: ModuleType, calendarios_mod: ModuleType
) -> None:
    calendarios = _calendarios(calendarios_mod)
    servidor = _servidor_misto(luma, calendarios)
    with servidor.cliente() as cliente:
        resultados = luma.coletar_luma(cliente, calendarios)

    assert [r.calendario for r in resultados] == [c.slug for c in calendarios]
    por_slug = {r.calendario: r for r in resultados}

    for slug in ("ai-collective", "codex-sp", "vibe-space", "womakerscode"):
        assert por_slug[slug].situacao == "falha", slug
        assert por_slug[slug].eventos == [], slug
        assert por_slug[slug].falha is not None, slug

    assert por_slug["ai-collective"].falha.motivo == "status_http"
    assert por_slug["ai-collective"].falha.status_http == 500
    assert por_slug["codex-sp"].falha.motivo == "redirecionamento"
    assert por_slug["codex-sp"].falha.status_http == 302
    assert por_slug["vibe-space"].falha.motivo == "rede"
    assert por_slug["vibe-space"].falha.status_http is None
    assert por_slug["womakerscode"].falha.motivo == "formato_invalido"

    assert por_slug["mug-sp"].situacao == "ok"
    assert por_slug["mug-sp"].falha is None
    assert len(por_slug["mug-sp"].eventos) == 1
    assert por_slug["cidade-sp"].situacao == "ok"
    assert len(por_slug["cidade-sp"].eventos) == 12


def test_excecao_inesperada_vira_formato_invalido_so_daquele_calendario(
    luma: ModuleType, calendarios_mod: ModuleType
) -> None:
    calendarios = _calendarios(calendarios_mod)
    rotas: dict[str, httpx.Response | Exception] = {
        luma.url_ical(c): _resposta_ics(_ler_fixture(c.slug)) for c in calendarios
    }
    rotas[_url(luma, calendarios, "codex-sp")] = RuntimeError(f"inesperado {MARCADOR}")
    servidor = _Servidor(rotas)
    with servidor.cliente() as cliente:
        resultados = luma.coletar_luma(cliente, calendarios)

    por_slug = {r.calendario: r for r in resultados}
    assert por_slug["codex-sp"].situacao == "falha"
    assert por_slug["codex-sp"].falha.motivo == "formato_invalido"
    assert MARCADOR not in por_slug["codex-sp"].model_dump_json()
    for slug in FEEDS:
        if slug != "codex-sp":
            assert por_slug[slug].situacao == "ok", slug


def test_feed_sem_eventos_e_vazio_e_nao_falha_na_coleta(
    luma: ModuleType, calendarios_mod: ModuleType
) -> None:
    calendarios = [c for c in _calendarios(calendarios_mod) if c.slug in ("mug-sp", "codex-sp")]
    servidor = _Servidor(
        {
            _url(luma, calendarios, "mug-sp"): _resposta_ics(_sem_eventos(_ler_fixture("mug-sp"))),
            _url(luma, calendarios, "codex-sp"): httpx.Response(500),
        }
    )
    with servidor.cliente() as cliente:
        resultados = luma.coletar_luma(cliente, calendarios)

    por_slug = {r.calendario: r for r in resultados}
    assert por_slug["mug-sp"].situacao == "vazio"
    assert por_slug["mug-sp"].falha is None
    assert por_slug["codex-sp"].situacao == "falha"


# =====================================================================================
# Privacidade: FalhaExtracao nunca carrega conteúdo recebido
# =====================================================================================


def test_detalhe_da_falha_nunca_contem_trecho_do_corpo(
    luma: ModuleType, calendarios_mod: ModuleType
) -> None:
    calendarios = _calendarios(calendarios_mod)
    servidor = _servidor_misto(luma, calendarios)
    with servidor.cliente() as cliente:
        resultados = luma.coletar_luma(cliente, calendarios)

    falhas = [r for r in resultados if r.situacao == "falha"]
    assert len(falhas) == 4
    for resultado in falhas:
        detalhe = resultado.falha.detalhe or ""
        assert MARCADOR not in detalhe, resultado.calendario
        assert "<html" not in detalhe.lower(), resultado.calendario
        assert MARCADOR not in resultado.model_dump_json(), resultado.calendario


def test_detalhe_da_falha_de_ical_quebrado_nao_contem_o_feed(
    luma: ModuleType, calendarios_mod: ModuleType
) -> None:
    # O parser do iCal costuma citar a linha problemática na mensagem de erro.
    calendarios = [c for c in _calendarios(calendarios_mod) if c.slug == "mug-sp"]
    quebrado = f"BEGIN:VCALENDAR\nVERSION:2.0\n<<{MARCADOR}>>\nEND:VCALENDAR\n"
    servidor = _Servidor({_url(luma, calendarios, "mug-sp"): _resposta_ics(quebrado)})
    with servidor.cliente() as cliente:
        (resultado,) = luma.coletar_luma(cliente, calendarios)

    assert resultado.situacao == "falha"
    assert resultado.falha.motivo == "formato_invalido"
    assert MARCADOR not in resultado.model_dump_json()


# =====================================================================================
# Modelos
# =====================================================================================


def _dados_evento(**mudancas: Any) -> dict[str, Any]:
    dados: dict[str, Any] = {
        "fonte": "luma",
        "calendario": "mug-sp",
        "id_fonte": "evt-AXkUVWCxURYLxoT",
        "titulo": "Meetup",
        "inicio": datetime(2026, 11, 25, 18, 0, tzinfo=SP),
        "fim": datetime(2026, 11, 25, 21, 0, tzinfo=SP),
        "dia_inteiro": False,
        "local_texto": "FIAP - Paulista",
        "endereco_oculto": False,
        "latitude": -23.56,
        "longitude": -46.65,
        "local_url": None,
        "status_fonte": "desconhecido",
        "link_inscricao": "https://luma.com/afm6b12v",
        "descricao": "Descrição",
        "organizador": "MongoDB Community",
    }
    dados.update(mudancas)
    return dados


def _base_valida(modelos: ModuleType) -> None:
    """Controle: o evento base é aceito, então a rejeição vem só da mudança testada."""
    modelos.EventoNormalizado(**_dados_evento())


def test_evento_normalizado_valido(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(**_dados_evento())

    assert evento.id_fonte == "evt-AXkUVWCxURYLxoT"
    assert _e_horario_de_sp(evento.inicio)


def test_evento_normalizado_aceita_calev_e_campos_opcionais_none(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(
        **_dados_evento(
            id_fonte="calev-BlWaHsESv15VkJo",
            fim=None,
            local_texto=None,
            latitude=None,
            longitude=None,
            link_inscricao=None,
            descricao=None,
            organizador=None,
            status_fonte="desconhecido",
        )
    )

    assert evento.id_fonte == "calev-BlWaHsESv15VkJo"


def test_evento_normalizado_e_imutavel(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(**_dados_evento())

    with pytest.raises(ValidationError):
        evento.titulo = "Outro"


def test_evento_normalizado_rejeita_campo_extra(modelos: ModuleType) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(preco="gratuito"))


@pytest.mark.parametrize(
    "id_fonte",
    [
        "evt-AXkUVWCxURYLxoT@events.lu.ma",
        "cal-HtbK5aYvR5B2WAu",
        "evt-",
        "evt-AXk_UVW",
        "AXkUVWCxURYLxoT",
        "",
    ],
)
def test_evento_normalizado_rejeita_id_fonte_fora_do_padrao(
    modelos: ModuleType, id_fonte: str
) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(id_fonte=id_fonte))


@pytest.mark.parametrize("titulo", ["", "   "])
def test_evento_normalizado_rejeita_titulo_vazio(modelos: ModuleType, titulo: str) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(titulo=titulo))


@pytest.mark.parametrize("campo", ["inicio", "fim"])
def test_evento_normalizado_exige_fuso(modelos: ModuleType, campo: str) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(**{campo: datetime(2026, 11, 25, 18, 0)}))


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(-23.56, None), (None, -46.65), (91.0, -46.65), (-91.0, -46.65), (-23.56, 181.0)],
    ids=["so-latitude", "so-longitude", "lat>90", "lat<-90", "lon>180"],
)
def test_evento_normalizado_coordenadas_em_par_e_na_faixa(
    modelos: ModuleType, latitude: float | None, longitude: float | None
) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(latitude=latitude, longitude=longitude))


def test_evento_normalizado_aceita_coordenadas_no_limite(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(**_dados_evento(latitude=-90.0, longitude=180.0))

    assert (evento.latitude, evento.longitude) == (-90.0, 180.0)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("status_fonte", "TENTATIVE"),
        ("status_fonte", "provisorio"),
        ("status_fonte", "ativo"),
        ("fonte", "meetup"),
    ],
)
def test_evento_normalizado_rejeita_literal_desconhecido(
    modelos: ModuleType, campo: str, valor: str
) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(**{campo: valor}))


def test_falha_extracao_rejeita_motivo_desconhecido(modelos: ModuleType) -> None:
    falha = modelos.FalhaExtracao(motivo="status_http", status_http=503, detalhe=None)
    assert falha.status_http == 503

    with pytest.raises(ValidationError):
        modelos.FalhaExtracao(motivo="timeout", status_http=None, detalhe=None)


def test_evento_invalido_rejeita_motivo_desconhecido(modelos: ModuleType) -> None:
    invalido = modelos.EventoInvalido(id_fonte=None, motivo="uid_ausente_ou_fora_do_padrao")
    assert invalido.id_fonte is None

    with pytest.raises(ValidationError):
        modelos.EventoInvalido(id_fonte="evt-abc", motivo="qualquer")


def _resultado(modelos: ModuleType, **campos: Any) -> Any:
    dados: dict[str, Any] = {
        "fonte": "luma",
        "calendario": "mug-sp",
        "situacao": "ok",
        "eventos": [],
        "eventos_invalidos": [],
        "falha": None,
    }
    dados.update(campos)
    return modelos.ResultadoCalendario(**dados)


def test_resultado_calendario_combinacoes_coerentes(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(**_dados_evento())
    falha = modelos.FalhaExtracao(motivo="rede", status_http=None, detalhe=None)

    assert _resultado(modelos, situacao="ok", eventos=[evento]).situacao == "ok"
    assert _resultado(modelos, situacao="vazio").situacao == "vazio"
    assert _resultado(modelos, situacao="falha", falha=falha).falha == falha


@pytest.mark.parametrize(
    "caso",
    [
        "falha-sem-falha",
        "ok-com-falha",
        "falha-com-eventos",
        "vazio-com-eventos",
        "vazio-com-falha",
    ],
)
def test_resultado_calendario_rejeita_combinacoes_incoerentes(
    modelos: ModuleType, caso: str
) -> None:
    evento = modelos.EventoNormalizado(**_dados_evento())
    falha = modelos.FalhaExtracao(motivo="rede", status_http=None, detalhe=None)
    campos: dict[str, Any] = {
        "falha-sem-falha": {"situacao": "falha", "falha": None},
        "ok-com-falha": {"situacao": "ok", "eventos": [evento], "falha": falha},
        "falha-com-eventos": {"situacao": "falha", "eventos": [evento], "falha": falha},
        "vazio-com-eventos": {"situacao": "vazio", "eventos": [evento]},
        "vazio-com-falha": {"situacao": "vazio", "falha": falha},
    }[caso]

    with pytest.raises(ValidationError):
        _resultado(modelos, **campos)


def test_resultado_calendario_rejeita_fonte_diferente_de_luma(modelos: ModuleType) -> None:
    with pytest.raises(ValidationError):
        _resultado(modelos, fonte="meetup", situacao="vazio")


# =====================================================================================
# Dependências
# =====================================================================================


def test_pyproject_declara_icalendar_e_pyyaml_como_dependencias_de_execucao() -> None:
    with (RAIZ / "pyproject.toml").open("rb") as arquivo:
        dependencias = tomllib.load(arquivo)["project"]["dependencies"]
    por_nome = {re.split(r"[<>=!~;\[\s]", d, maxsplit=1)[0].lower(): d for d in dependencias}

    assert "icalendar" in por_nome
    assert "==" in por_nome["icalendar"]
    assert "pyyaml" in por_nome
    assert "==" in por_nome["pyyaml"]


# =====================================================================================
# Revisão do F1-03: local_url e link de eventos calev-
# =====================================================================================

# (feed, id_fonte, URL do LOCATION, link da DESCRIPTION que aponta para o calendário)
CALEV_REAIS = (
    (
        "womakerscode",
        "calev-BlWaHsESv15VkJo",
        "https://www.womakerscode.org/encontro",
        "https://luma.com/womakerscode",
    ),
    (
        "ai-collective",
        "calev-5WU1y0W8U0qrCmC",
        "https://partiful.com/e/fduy3TJ8t7xQYw06HEPU",
        "https://luma.com/genai-collective",
    ),
    (
        "ai-collective",
        "calev-DhDqCFGGhwE3WJZ",
        "https://www.eventbrite.com/e/ai-investors-networking-tickets-707083233987"
        "?aff=ebdsoporgprofile",
        "https://luma.com/genai-collective",
    ),
    (
        "vibe-space",
        "calev-Hm7MX91gGxxbboZ",
        "https://partiful.com/e/56oqJx0q85eKvpqr7TTu",
        "https://luma.com/ventureminer",
    ),
)


@pytest.mark.parametrize(
    ("slug", "id_fonte", "url_local", "link_do_calendario"),
    CALEV_REAIS,
    ids=[c[1] for c in CALEV_REAIS],
)
def test_calev_real_usa_url_do_location_como_inscricao(
    luma: ModuleType, slug: str, id_fonte: str, url_local: str, link_do_calendario: str
) -> None:
    evento = _evento(_ler(luma, slug), id_fonte)

    assert evento.local_url == url_local
    assert evento.local_texto is None
    assert evento.endereco_oculto is False
    assert evento.link_inscricao == url_local
    assert evento.link_inscricao != link_do_calendario


def test_nenhum_calev_das_fixtures_aponta_para_a_pagina_do_calendario(luma: ModuleType) -> None:
    calev = [
        evento
        for slug in FEEDS
        for evento in _ler(luma, slug).eventos
        if evento.id_fonte.startswith("calev-")
    ]

    # 12 no ai-collective, 2 no vibe-space e 1 no womakerscode.
    assert len(calev) == 15
    for evento in calev:
        assert evento.link_inscricao is not None, evento.id_fonte
        assert not evento.link_inscricao.startswith("https://luma.com/"), evento.id_fonte
        assert evento.link_inscricao == evento.local_url, evento.id_fonte
        assert evento.local_texto is None, evento.id_fonte


def test_evt_com_url_externa_no_location_mantem_link_da_description(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("mug-sp"), None, _com("LOCATION", "LOCATION:https://meet.exemplo.invalid/sala")
    )
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.local_url == "https://meet.exemplo.invalid/sala"
    assert evento.local_texto is None
    assert evento.endereco_oculto is False
    assert evento.link_inscricao == "https://luma.com/afm6b12v"


def test_location_http_sem_tls_nao_vira_local_url_nem_texto(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("mug-sp"), None, _com("LOCATION", "LOCATION:http://exemplo.invalid/sala")
    )
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.local_url is None
    assert evento.local_texto is None
    assert evento.link_inscricao == "https://luma.com/afm6b12v"


@pytest.mark.parametrize(
    ("linha", "local_texto"),
    [
        ("LOCATION:Rua Exemplo\\, 100", "Rua Exemplo, 100"),
        ("LOCATION:http://www.womakerscode.org/encontro", None),
        (None, None),
    ],
    ids=["texto", "http", "ausente"],
)
def test_calev_sem_local_url_fica_sem_link(
    luma: ModuleType, linha: str | None, local_texto: str | None
) -> None:
    edicao = _sem("LOCATION") if linha is None else _com("LOCATION", linha)
    texto = _editar_evento(_ler_fixture("womakerscode"), "calev-BlWaHsESv15VkJo", edicao)
    evento = _evento(_ler(luma, "womakerscode", texto), "calev-BlWaHsESv15VkJo")

    assert evento.local_url is None
    assert evento.local_texto == local_texto
    # O link da DESCRIPTION (página do calendário) nunca é usado para calev-.
    assert evento.link_inscricao is None


# =====================================================================================
# Revisão do F1-03: DTEND opcional e DTSTART malformado
# =====================================================================================


@pytest.mark.parametrize(
    "linha",
    [
        "DTEND:20261125T210000Z",
        "DTEND:20261125T200000Z",
        "DTEND;VALUE=DATE:20261126",
        "DTEND:abc",
        "DTEND:20261126T000000",
    ],
    ids=["igual-ao-inicio", "antes-do-inicio", "data-com-inicio-data-hora", "texto", "flutuante"],
)
def test_dtend_malformado_ou_incoerente_vira_fim_none(luma: ModuleType, linha: str) -> None:
    texto = _editar_evento(_ler_fixture("mug-sp"), None, _com("DTEND", linha))
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "ok"
    assert resultado.eventos_invalidos == []
    evento = resultado.eventos[0]
    assert evento.fim is None
    assert evento.inicio == datetime(2026, 11, 25, 18, 0, tzinfo=SP)


def test_dtend_data_hora_em_evento_de_dia_inteiro_vira_fim_none(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("vibe-space"), "evt-pG3Y6C6KDBcjfaA", _com("DTEND", "DTEND:20241012T120000Z")
    )
    resultado = _ler(luma, "vibe-space", texto)

    evento = _evento(resultado, "evt-pG3Y6C6KDBcjfaA")
    assert evento.dia_inteiro is True
    assert evento.inicio == datetime(2024, 10, 7, 0, 0, tzinfo=SP)
    assert evento.fim is None
    assert "evt-pG3Y6C6KDBcjfaA" not in {i.id_fonte for i in resultado.eventos_invalidos}


def test_dtstart_malformado_e_horario_invalido(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("cidade-sp"), "evt-AXkUVWCxURYLxoT", _com("DTSTART", "DTSTART:abc")
    )
    resultado = _ler(luma, "cidade-sp", texto)

    assert len(resultado.eventos) == 11
    assert [(i.id_fonte, i.motivo) for i in resultado.eventos_invalidos] == [
        ("evt-AXkUVWCxURYLxoT", "horario_invalido")
    ]


# =====================================================================================
# Revisão do F1-03: status TENTATIVE
# =====================================================================================


@pytest.mark.parametrize("slug", FEEDS)
def test_tentative_das_fixtures_vira_desconhecido(luma: ModuleType, slug: str) -> None:
    # Todos os 550 VEVENT das fixtures têm STATUS:TENTATIVE (padrão da Luma).
    resultado = _ler(luma, slug)

    assert {e.status_fonte for e in resultado.eventos} == {"desconhecido"}


# =====================================================================================
# Revisão do F1-03: caracteres de controle
# =====================================================================================


def _tem_controle(texto: str, permitidos: str = "") -> bool:
    return any(unicodedata.category(c) == "Cc" and c not in permitidos for c in texto)


def test_caracteres_de_controle_saem_do_titulo(luma: ModuleType) -> None:
    texto = _ler_fixture("mug-sp").replace("SUMMARY:MUG", "SUMMARY:\x00\x1bMUG")
    assert "\x00\x1bMUG" in texto
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "ok"
    evento = resultado.eventos[0]
    assert evento.titulo == "MUG São Paulo NOV 2026 - Vector Search na Prática & mais"


def test_titulo_so_com_caracteres_de_controle_e_titulo_ausente(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("cidade-sp"),
        "evt-AXkUVWCxURYLxoT",
        _com("SUMMARY", "SUMMARY:\x00\x1b\x07\x7f"),
    )
    resultado = _ler(luma, "cidade-sp", texto)

    assert len(resultado.eventos) == 11
    assert [(i.id_fonte, i.motivo) for i in resultado.eventos_invalidos] == [
        ("evt-AXkUVWCxURYLxoT", "titulo_ausente")
    ]


def test_caracteres_de_controle_saem_de_local_e_descricao(luma: ModuleType) -> None:
    texto = _editar_evento(
        _ler_fixture("mug-sp"),
        None,
        _encadear(
            _com("LOCATION", "LOCATION:FIAP\x1b - Paulista\x7f"),
            _com("DESCRIPTION", "DESCRIPTION:Linha\x07 1\\n\tLinha\x00 2 https://luma.com/abc"),
        ),
    )
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.local_texto == "FIAP - Paulista"
    # Na descrição só \n e \t sobrevivem.
    assert evento.descricao == "Linha 1\n\tLinha 2 https://luma.com/abc"


def test_organizador_com_caractere_de_controle_nao_vaza(luma: ModuleType) -> None:
    # O icalendar 7.3.2 descarta a linha ORGANIZER inteira quando o CN tem caractere de
    # controle, então a remoção no CN não é alcançável pelo iCal; garante-se que o evento
    # segue válido e que nenhum caractere de controle chega ao organizador.
    texto = _editar_evento(
        _ler_fixture("mug-sp"),
        None,
        _com("ORGANIZER", 'ORGANIZER;CN="Comuni\x1bdade":MAILTO:redigido@exemplo.invalid'),
    )
    resultado = _ler(luma, "mug-sp", texto)

    assert resultado.situacao == "ok"
    organizador = resultado.eventos[0].organizador
    assert organizador in (None, "Comunidade")


@pytest.mark.parametrize("slug", FEEDS)
def test_fixtures_nao_produzem_caracteres_de_controle(luma: ModuleType, slug: str) -> None:
    for evento in _ler(luma, slug).eventos:
        assert not _tem_controle(evento.titulo), evento.id_fonte
        assert not _tem_controle(evento.local_texto or ""), evento.id_fonte
        assert not _tem_controle(evento.organizador or ""), evento.id_fonte
        assert not _tem_controle(evento.descricao or "", "\n\t"), evento.id_fonte


# =====================================================================================
# Revisão do F1-03: BOM no início do feed
# =====================================================================================


@pytest.mark.parametrize("slug", ["mug-sp", "cidade-sp"])
def test_bom_no_inicio_do_feed_e_ignorado(luma: ModuleType, slug: str) -> None:
    sem_bom = _ler(luma, slug)
    com_bom = _ler(luma, slug, "﻿" + _ler_fixture(slug))

    assert com_bom.situacao == "ok"
    assert com_bom.falha is None
    assert com_bom.eventos == sem_bom.eventos


def test_bom_em_bytes_na_resposta_http_e_ignorado(
    luma: ModuleType, calendarios_mod: ModuleType
) -> None:
    calendarios = [c for c in _calendarios(calendarios_mod) if c.slug == "cidade-sp"]
    corpo = b"\xef\xbb\xbf" + _ler_fixture("cidade-sp").encode("utf-8")
    servidor = _Servidor(
        {
            _url(luma, calendarios, "cidade-sp"): httpx.Response(
                200, content=corpo, headers={"content-type": "text/calendar; charset=utf-8"}
            )
        }
    )
    with servidor.cliente() as cliente:
        (resultado,) = luma.coletar_luma(cliente, calendarios)

    assert resultado.situacao == "ok"
    assert len(resultado.eventos) == 12


# =====================================================================================
# Revisão do F1-03: fronteira do link de inscrição
# =====================================================================================


@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        ("Veja https://luma.com/home.evil", None),
        ("Veja https://luma.com/home.evil e https://luma.com/certo", "https://luma.com/certo"),
        ("https://luma.com/home.evil.com/x https://luma.com/certo", "https://luma.com/certo"),
        ("https://luma.com/abc/xyz https://luma.com/certo", "https://luma.com/certo"),
        ("Inscreva-se em https://luma.com/abc.", "https://luma.com/abc"),
        ("Inscreva-se em https://luma.com/abc. Até lá", "https://luma.com/abc"),
        ("Página (https://luma.com/abc) do evento", "https://luma.com/abc"),
        ("https://luma.com/abc\\, https://luma.com/outro", "https://luma.com/abc"),
        ("https://luma.com/abc\\nMais texto", "https://luma.com/abc"),
        ("https://luma.com/abc", "https://luma.com/abc"),
    ],
    ids=[
        "dominio-falso-sozinho",
        "dominio-falso-e-depois-valido",
        "dominio-falso-com-caminho",
        "caminho-extra",
        "ponto-final",
        "ponto-e-espaco",
        "parenteses",
        "virgula-escapada",
        "quebra-escapada",
        "fim-do-texto",
    ],
)
def test_fronteira_do_link_de_inscricao(
    luma: ModuleType, descricao: str, esperado: str | None
) -> None:
    texto = _editar_evento(
        _ler_fixture("mug-sp"), None, _com("DESCRIPTION", f"DESCRIPTION:{descricao}")
    )
    evento = _ler(luma, "mug-sp", texto).eventos[0]

    assert evento.link_inscricao == esperado


# =====================================================================================
# Revisão do F1-03: coerência nos modelos
# =====================================================================================


@pytest.mark.parametrize(
    "mudancas",
    [
        {"endereco_oculto": True, "local_texto": "FIAP - Paulista", "local_url": None},
        {"endereco_oculto": True, "local_texto": None, "local_url": "https://exemplo.invalid"},
    ],
    ids=["oculto-com-texto", "oculto-com-url"],
)
def test_evento_normalizado_rejeita_endereco_oculto_com_local(
    modelos: ModuleType, mudancas: dict[str, Any]
) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(**mudancas))


def test_evento_normalizado_aceita_endereco_oculto_sem_local(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(
        **_dados_evento(endereco_oculto=True, local_texto=None, local_url=None)
    )

    assert evento.endereco_oculto is True


def test_evento_normalizado_aceita_local_url(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(
        **_dados_evento(local_texto=None, local_url="https://www.womakerscode.org/encontro")
    )

    assert evento.local_url == "https://www.womakerscode.org/encontro"


@pytest.mark.parametrize(
    ("inicio", "fim"),
    [
        (datetime(2026, 11, 25, 18, 0, tzinfo=SP), datetime(2026, 11, 26, 0, 0, tzinfo=SP)),
        (datetime(2026, 11, 25, 0, 0, tzinfo=SP), datetime(2026, 11, 26, 12, 0, tzinfo=SP)),
        (datetime(2026, 11, 25, 18, 0, tzinfo=SP), None),
    ],
    ids=["inicio-fora-da-meia-noite", "fim-fora-da-meia-noite", "inicio-fora-sem-fim"],
)
def test_evento_normalizado_dia_inteiro_exige_meia_noite(
    modelos: ModuleType, inicio: datetime, fim: datetime | None
) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(**_dados_evento(dia_inteiro=True, inicio=inicio, fim=fim))


def test_evento_normalizado_dia_inteiro_valido(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(
        **_dados_evento(
            dia_inteiro=True,
            inicio=datetime(2024, 10, 7, 0, 0, tzinfo=SP),
            fim=datetime(2024, 10, 12, 0, 0, tzinfo=SP),
        )
    )

    assert evento.dia_inteiro is True


@pytest.mark.parametrize(
    "fim",
    [datetime(2026, 11, 25, 18, 0, tzinfo=SP), datetime(2026, 11, 25, 17, 0, tzinfo=SP)],
    ids=["igual", "antes"],
)
def test_evento_normalizado_rejeita_fim_nao_posterior_ao_inicio(
    modelos: ModuleType, fim: datetime
) -> None:
    _base_valida(modelos)
    with pytest.raises(ValidationError):
        modelos.EventoNormalizado(
            **_dados_evento(inicio=datetime(2026, 11, 25, 18, 0, tzinfo=SP), fim=fim)
        )


def test_resultado_calendario_rejeita_evento_de_outro_calendario(modelos: ModuleType) -> None:
    evento = modelos.EventoNormalizado(**_dados_evento(calendario="mug-sp"))

    with pytest.raises(ValidationError):
        _resultado(modelos, calendario="cidade-sp", situacao="ok", eventos=[evento])


@pytest.mark.parametrize("motivo", ["rede", "status_http", "redirecionamento", "bloqueado_robots"])
def test_resultado_calendario_rejeita_invalidos_com_falha_que_nao_e_de_formato(
    modelos: ModuleType, motivo: str
) -> None:
    invalido = modelos.EventoInvalido(id_fonte="evt-abc", motivo="inicio_ausente")
    falha = modelos.FalhaExtracao(motivo=motivo, status_http=None, detalhe=None)

    with pytest.raises(ValidationError):
        _resultado(modelos, situacao="falha", eventos_invalidos=[invalido], falha=falha)


def test_resultado_calendario_aceita_invalidos_com_falha_de_formato(modelos: ModuleType) -> None:
    invalido = modelos.EventoInvalido(id_fonte="evt-abc", motivo="inicio_ausente")
    falha = modelos.FalhaExtracao(motivo="formato_invalido", status_http=None, detalhe=None)

    resultado = _resultado(modelos, situacao="falha", eventos_invalidos=[invalido], falha=falha)

    assert resultado.eventos_invalidos == [invalido]
