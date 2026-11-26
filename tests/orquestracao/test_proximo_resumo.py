"""Testes do F1-10b — `proximo_resumo_provisorio(agora)`: próxima segunda 00:00 em
America/Sao_Paulo estritamente depois de `agora`; `agora` sem fuso → ValueError."""

from __future__ import annotations

import importlib
from datetime import datetime
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

SP = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")


@pytest.fixture
def executar_mod() -> ModuleType:
    return importlib.import_module("radar.orquestracao.executar")


@pytest.mark.parametrize(
    ("agora", "esperado"),
    [
        (datetime(2026, 11, 24, 15, 0, tzinfo=SP), datetime(2026, 11, 30, 0, 0, tzinfo=SP)),
        (datetime(2026, 11, 29, 23, 59, tzinfo=SP), datetime(2026, 11, 30, 0, 0, tzinfo=SP)),
        (datetime(2026, 11, 30, 7, 40, tzinfo=SP), datetime(2026, 12, 7, 0, 0, tzinfo=SP)),
        (datetime(2026, 11, 30, 0, 0, tzinfo=SP), datetime(2026, 12, 7, 0, 0, tzinfo=SP)),
        # 01:30 UTC de segunda ainda é domingo 22:30 em SP
        (datetime(2026, 11, 30, 1, 30, tzinfo=UTC), datetime(2026, 11, 30, 0, 0, tzinfo=SP)),
    ],
    ids=["terca", "domingo-23h59", "segunda-07h40", "segunda-00h00", "utc-segunda-sp-domingo"],
)
def test_proximo_resumo(executar_mod: ModuleType, agora: datetime, esperado: datetime) -> None:
    resultado = executar_mod.proximo_resumo_provisorio(agora)
    assert resultado == esperado
    assert resultado > agora
    assert resultado.utcoffset() is not None
    local = resultado.astimezone(SP)
    assert (local.weekday(), local.hour, local.minute) == (0, 0, 0)


def test_sem_fuso_e_erro(executar_mod: ModuleType) -> None:
    with pytest.raises(ValueError):
        executar_mod.proximo_resumo_provisorio(datetime(2026, 11, 25, 12, 0))
