"""Testes do ticket F1-04 — filtro de horizonte (spec §2; backlog F1-04).

Antes do LLM o tipo do evento é desconhecido, então o filtro usa 90 dias para todos; o corte
de 30 dias por tipo fica para depois da classificação (fora deste ticket).

"passado" = terminou antes de `agora` (usa `fim`; sem `fim`, usa `inicio`).
"alem_do_horizonte" = `inicio` depois de `agora + 90 dias`. O limite exato é aprovado.

Os módulos são importados dentro de fixtures para que a falta da implementação apareça
teste a teste, e não como erro de coleta do arquivo inteiro.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from radar.fontes.luma import ler_ical
from radar.fontes.modelos import EventoNormalizado

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "fixtures" / "luma"
SP = ZoneInfo("America/Sao_Paulo")
FEEDS = ("ai-collective", "codex-sp", "vibe-space", "womakerscode", "mug-sp", "cidade-sp")
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=SP)


@pytest.fixture
def geo() -> ModuleType:
    return importlib.import_module("radar.filtros.geografico")


@pytest.fixture
def horizonte() -> ModuleType:
    return importlib.import_module("radar.filtros.horizonte")


def _evento(**campos: Any) -> EventoNormalizado:
    inicio = AGORA + timedelta(days=7)
    base: dict[str, Any] = {
        "fonte": "luma",
        "calendario": "sintetico",
        "id_fonte": "evt-Sintetico1",
        "titulo": "Meetup de IA",
        "inicio": inicio,
        "fim": inicio + timedelta(hours=2),
        "dia_inteiro": False,
        "local_texto": None,
        "local_url": None,
        "endereco_oculto": False,
        "latitude": -23.55,
        "longitude": -46.63,
        "status_fonte": "desconhecido",
        "link_inscricao": None,
        "descricao": None,
        "organizador": None,
    }
    base.update(campos)
    return EventoNormalizado(**base)


def _localizado(geo: ModuleType, **campos: Any) -> Any:
    evento = _evento(**campos)
    return geo.EventoLocalizado(
        evento=evento, localizacao=geo.Localizacao(situacao="sp", evidencia="geo")
    )


def _situacao(horizonte: ModuleType, item: Any, agora: datetime = AGORA) -> str:
    """ "aprovado" ou o motivo do descarte."""
    resultado = horizonte.filtrar_por_horizonte([item], agora)
    assert len(resultado.aprovados) + len(resultado.descartados) == 1
    if resultado.aprovados:
        assert resultado.aprovados[0] == item
        return "aprovado"
    descartado = resultado.descartados[0]
    assert descartado.id_fonte == item.evento.id_fonte
    assert descartado.calendario == item.evento.calendario
    return descartado.motivo


def test_horizonte_e_de_90_dias(horizonte: ModuleType) -> None:
    assert horizonte.HORIZONTE_DIAS == 90


def test_evento_futuro_dentro_do_horizonte_e_aprovado(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    assert _situacao(horizonte, _localizado(geo)) == "aprovado"


def test_evento_que_ja_terminou_e_passado(geo: ModuleType, horizonte: ModuleType) -> None:
    item = _localizado(geo, inicio=AGORA - timedelta(hours=3), fim=AGORA - timedelta(hours=1))
    assert _situacao(horizonte, item) == "passado"


def test_evento_em_andamento_e_aprovado(geo: ModuleType, horizonte: ModuleType) -> None:
    item = _localizado(geo, inicio=AGORA - timedelta(hours=1), fim=AGORA + timedelta(hours=1))
    assert _situacao(horizonte, item) == "aprovado"


def test_sem_fim_e_inicio_no_passado_e_passado(geo: ModuleType, horizonte: ModuleType) -> None:
    item = _localizado(geo, inicio=AGORA - timedelta(minutes=1), fim=None)
    assert _situacao(horizonte, item) == "passado"


def test_sem_fim_e_inicio_no_futuro_e_aprovado(geo: ModuleType, horizonte: ModuleType) -> None:
    item = _localizado(geo, inicio=AGORA + timedelta(hours=1), fim=None)
    assert _situacao(horizonte, item) == "aprovado"


def test_inicio_exatamente_no_limite_de_90_dias_e_aprovado(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    inicio = AGORA + timedelta(days=90)
    item = _localizado(geo, inicio=inicio, fim=inicio + timedelta(hours=2))
    assert _situacao(horizonte, item) == "aprovado"


def test_inicio_um_segundo_depois_do_limite_e_alem_do_horizonte(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    inicio = AGORA + timedelta(days=90, seconds=1)
    item = _localizado(geo, inicio=inicio, fim=inicio + timedelta(hours=2))
    assert _situacao(horizonte, item) == "alem_do_horizonte"


def test_sem_fim_e_alem_do_horizonte(geo: ModuleType, horizonte: ModuleType) -> None:
    item = _localizado(geo, inicio=AGORA + timedelta(days=120), fim=None)
    assert _situacao(horizonte, item) == "alem_do_horizonte"


def test_agora_sem_fuso_levanta_value_error(geo: ModuleType, horizonte: ModuleType) -> None:
    with pytest.raises(ValueError):
        horizonte.filtrar_por_horizonte([_localizado(geo)], datetime(2026, 11, 25, 12, 0))


def test_agora_sem_fuso_levanta_value_error_mesmo_sem_eventos(horizonte: ModuleType) -> None:
    with pytest.raises(ValueError):
        horizonte.filtrar_por_horizonte([], datetime(2026, 11, 25, 12, 0))


def test_agora_em_utc_equivale_ao_mesmo_instante_em_sp(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    """15:00 UTC = 12:00 em SP: um evento que acabou às 12:30 SP ainda não é passado."""
    agora_utc = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)
    item = _localizado(geo, inicio=AGORA - timedelta(hours=1), fim=AGORA + timedelta(minutes=30))
    assert _situacao(horizonte, item, agora_utc) == "aprovado"
    passado = _localizado(geo, inicio=AGORA - timedelta(hours=1), fim=AGORA - timedelta(minutes=30))
    assert _situacao(horizonte, passado, agora_utc) == "passado"


def test_dia_inteiro_de_hoje_e_aprovado(geo: ModuleType, horizonte: ModuleType) -> None:
    hoje = datetime(2026, 11, 25, 0, 0, tzinfo=SP)
    item = _localizado(geo, inicio=hoje, fim=hoje + timedelta(days=1), dia_inteiro=True)
    assert _situacao(horizonte, item) == "aprovado"


def test_dia_inteiro_de_ontem_e_passado(geo: ModuleType, horizonte: ModuleType) -> None:
    ontem = datetime(2026, 11, 24, 0, 0, tzinfo=SP)
    item = _localizado(geo, inicio=ontem, fim=ontem + timedelta(days=1), dia_inteiro=True)
    assert _situacao(horizonte, item) == "passado"


def test_dia_inteiro_sem_fim_de_hoje_e_aprovado(geo: ModuleType, horizonte: ModuleType) -> None:
    """Dia inteiro sem `fim` dura até `inicio + 1 dia`: hoje às 12h ainda está acontecendo."""
    hoje = datetime(2026, 11, 25, 0, 0, tzinfo=SP)
    item = _localizado(geo, inicio=hoje, fim=None, dia_inteiro=True)
    assert _situacao(horizonte, item) == "aprovado"
    quase_meia_noite = datetime(2026, 11, 25, 23, 59, tzinfo=SP)
    assert _situacao(horizonte, item, quase_meia_noite) == "aprovado"


def test_dia_inteiro_sem_fim_termina_na_meia_noite_seguinte(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    """O término implícito (`inicio + 1 dia`) segue a regra `fim <= agora` → passado."""
    hoje = datetime(2026, 11, 25, 0, 0, tzinfo=SP)
    item = _localizado(geo, inicio=hoje, fim=None, dia_inteiro=True)
    assert _situacao(horizonte, item, hoje + timedelta(days=1)) == "passado"


def test_dia_inteiro_sem_fim_de_ontem_e_passado(geo: ModuleType, horizonte: ModuleType) -> None:
    ontem = datetime(2026, 11, 24, 0, 0, tzinfo=SP)
    item = _localizado(geo, inicio=ontem, fim=None, dia_inteiro=True)
    assert _situacao(horizonte, item) == "passado"


def test_evento_normal_sem_fim_iniciado_hoje_cedo_continua_passado(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    """Só o dia inteiro ganha a duração implícita; evento com hora usa o próprio início."""
    item = _localizado(geo, inicio=AGORA - timedelta(hours=3), fim=None, dia_inteiro=False)
    assert _situacao(horizonte, item) == "passado"


def test_filtro_mantem_a_ordem_e_separa_motivos(geo: ModuleType, horizonte: ModuleType) -> None:
    itens = [
        _localizado(geo, id_fonte="evt-Futuro1"),
        _localizado(
            geo,
            id_fonte="evt-Passado1",
            inicio=AGORA - timedelta(days=2),
            fim=AGORA - timedelta(days=1),
        ),
        _localizado(geo, id_fonte="evt-Longe1", inicio=AGORA + timedelta(days=200), fim=None),
        _localizado(geo, id_fonte="evt-Futuro2", inicio=AGORA + timedelta(days=30), fim=None),
    ]
    resultado = horizonte.filtrar_por_horizonte(itens, AGORA)
    assert [a.evento.id_fonte for a in resultado.aprovados] == ["evt-Futuro1", "evt-Futuro2"]
    assert [(d.id_fonte, d.motivo) for d in resultado.descartados] == [
        ("evt-Passado1", "passado"),
        ("evt-Longe1", "alem_do_horizonte"),
    ]


def test_lista_vazia(horizonte: ModuleType) -> None:
    resultado = horizonte.filtrar_por_horizonte([], AGORA)
    assert resultado.aprovados == []
    assert resultado.descartados == []


# --- Fixtures reais ------------------------------------------------------------------


def _localizados_do_feed(geo: ModuleType, slug: str) -> list[Any]:
    resultado = ler_ical((FIXTURES / f"{slug}.ics").read_text(encoding="utf-8"), slug)
    return geo.filtrar_por_localizacao(resultado.eventos).aprovados


@pytest.mark.parametrize("slug", FEEDS)
def test_fixtures_aprovados_mais_descartados_e_o_total(
    geo: ModuleType, horizonte: ModuleType, slug: str
) -> None:
    itens = _localizados_do_feed(geo, slug)
    resultado = horizonte.filtrar_por_horizonte(itens, AGORA)
    assert len(resultado.aprovados) + len(resultado.descartados) == len(itens)
    assert all(d.motivo in {"passado", "alem_do_horizonte"} for d in resultado.descartados)


def test_fixture_cidade_sp_inteira_dentro_do_horizonte(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    """Os 12 eventos vão de 25/11 a 09/12/2026 e nenhum terminou antes das 12h de 25/11."""
    itens = _localizados_do_feed(geo, "cidade-sp")
    resultado = horizonte.filtrar_por_horizonte(itens, AGORA)
    assert resultado.descartados == []
    assert len(resultado.aprovados) == 12


def test_fixture_mug_em_andamento_e_aprovado(geo: ModuleType, horizonte: ModuleType) -> None:
    """MUG São Paulo: 25/11/2026 das 18h às 21h; às 20h está em andamento."""
    itens = _localizados_do_feed(geo, "mug-sp")
    resultado = horizonte.filtrar_por_horizonte(itens, datetime(2026, 11, 25, 20, 0, tzinfo=SP))
    assert [a.evento.id_fonte for a in resultado.aprovados] == ["evt-AXkUVWCxURYLxoT"]
    depois = horizonte.filtrar_por_horizonte(itens, datetime(2026, 11, 25, 21, 1, tzinfo=SP))
    assert [(d.id_fonte, d.motivo) for d in depois.descartados] == [
        ("evt-AXkUVWCxURYLxoT", "passado")
    ]


def test_fixture_codex_de_maio_e_passado(geo: ModuleType, horizonte: ModuleType) -> None:
    itens = _localizados_do_feed(geo, "codex-sp")
    resultado = horizonte.filtrar_por_horizonte(itens, AGORA)
    assert resultado.aprovados == []
    assert [(d.id_fonte, d.motivo) for d in resultado.descartados] == [
        ("evt-PPnuy0tzwk8MVct", "passado")
    ]


def test_fim_exatamente_igual_a_agora_e_passado(geo: ModuleType, horizonte: ModuleType) -> None:
    item = _localizado(geo, inicio=AGORA - timedelta(hours=2), fim=AGORA)
    assert _situacao(horizonte, item) == "passado"


def test_sem_fim_e_inicio_exatamente_igual_a_agora_e_passado(
    geo: ModuleType, horizonte: ModuleType
) -> None:
    item = _localizado(geo, inicio=AGORA, fim=None)
    assert _situacao(horizonte, item) == "passado"
