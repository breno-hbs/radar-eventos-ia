"""Testes do F1-06 — custo por chamada.

Assinaturas fixadas (radar.classificacao.precos):
- `TABELA_PRECOS`: dict por modelo; claude-haiku-5-5 custa US$ 0,10/MTok de entrada e
  US$ 0,50/MTok de saída até 100.000 tokens de entrada na chamada, e 0,50/2,50 acima disso;
- `calcular_custo(modelo, tokens_entrada, tokens_saida) -> Decimal` com 6 casas
  (ROUND_HALF_UP); modelo desconhecido → ValueError.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from types import ModuleType

import pytest

HAIKU = "claude-haiku-5-5"


@pytest.fixture
def precos() -> ModuleType:
    return importlib.import_module("radar.classificacao.precos")


def test_modelo_padrao_esta_na_tabela(precos: ModuleType) -> None:
    modelos = importlib.import_module("radar.classificacao.modelos")
    assert modelos.MODELO in precos.TABELA_PRECOS
    assert HAIKU in precos.TABELA_PRECOS


def test_preco_curto_por_milhao(precos: ModuleType) -> None:
    # 0,10/MTok de entrada e 0,50/MTok de saída, proporcional, até 100.000 de entrada.
    # (1M de entrada numa chamada só passaria do limite; por isso a taxa é conferida em 100k.)
    assert precos.calcular_custo(HAIKU, 100_000, 0) == Decimal("0.010000")
    assert precos.calcular_custo(HAIKU, 10_000, 0) * 100 == Decimal("0.100000")
    assert precos.calcular_custo(HAIKU, 0, 1_000_000) == Decimal("0.500000")
    assert precos.calcular_custo(HAIKU, 1_000, 200) == Decimal("0.000200")


def test_entrada_acima_de_100_mil_usa_preco_longo(precos: ModuleType) -> None:
    # 100.001 x 0,50 / 1M = 0,0500005 -> ROUND_HALF_UP -> 0,050001
    assert precos.calcular_custo(HAIKU, 100_001, 0) == Decimal("0.050001")
    # saída também passa para 2,50/MTok
    assert precos.calcular_custo(HAIKU, 100_001, 1_000_000) == Decimal("2.550001")
    assert precos.calcular_custo(HAIKU, 1_000_000, 0) == Decimal("0.500000")


def test_resultado_tem_6_casas(precos: ModuleType) -> None:
    custo = precos.calcular_custo(HAIKU, 1, 1)
    assert isinstance(custo, Decimal)
    assert custo.as_tuple().exponent == -6
    assert custo == Decimal("0.000001")  # 0,0000006 -> arredonda para cima
    assert precos.calcular_custo(HAIKU, 0, 0) == Decimal("0.000000")


def test_modelo_desconhecido_e_erro(precos: ModuleType) -> None:
    with pytest.raises(ValueError):
        precos.calcular_custo("modelo-que-nao-existe", 10, 10)
