"""Testes do F1-08 — combinação do preço do LLM com o da página (decisão do autor).

Assinatura fixada (radar.enriquecimento.preco):
`estado_preco_efetivo(estado_llm: EstadoPreco | None, enriquecimento: Enriquecimento | None)
-> EstadoPreco`: página conclusiva (≠ preco_desconhecido) prevalece, EXCETO página
gratuito_confirmado com LLM pago_com_valor/pago_sem_valor (vale o LLM); página desconhecida ou
ausente → LLM; nenhum → preco_desconhecido.
"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest

ESTADOS = [
    "gratuito_confirmado",
    "pago_com_valor",
    "pago_sem_valor",
    "preco_variavel",
    "preco_desconhecido",
]


@pytest.fixture
def preco() -> ModuleType:
    return importlib.import_module("radar.enriquecimento.preco")


def _pagina(estado: str) -> object:
    from radar.banco.modelos import Enriquecimento

    pago = estado == "pago_com_valor"
    return Enriquecimento(
        estado_preco=estado,
        valor_centavos=5000 if pago else None,
        moeda="BRL" if pago else None,
        motivo="sem_offers" if estado == "preco_desconhecido" else None,
    )


@pytest.mark.parametrize(
    ("llm", "pagina", "esperado"),
    [
        ("preco_desconhecido", "pago_com_valor", "pago_com_valor"),
        ("gratuito_confirmado", "pago_com_valor", "pago_com_valor"),
        ("pago_sem_valor", "pago_com_valor", "pago_com_valor"),
        ("pago_com_valor", "preco_variavel", "preco_variavel"),
        (None, "pago_sem_valor", "pago_sem_valor"),
        ("preco_variavel", "gratuito_confirmado", "gratuito_confirmado"),
        ("preco_desconhecido", "gratuito_confirmado", "gratuito_confirmado"),
        (None, "gratuito_confirmado", "gratuito_confirmado"),
        # exceção: página grátis, LLM viu preço → vale o LLM
        ("pago_com_valor", "gratuito_confirmado", "pago_com_valor"),
        ("pago_sem_valor", "gratuito_confirmado", "pago_sem_valor"),
    ],
)
def test_pagina_conclusiva(preco: ModuleType, llm: str | None, pagina: str, esperado: str) -> None:
    assert preco.estado_preco_efetivo(llm, _pagina(pagina)) == esperado


@pytest.mark.parametrize("llm", ESTADOS)
def test_pagina_desconhecida_usa_o_llm(preco: ModuleType, llm: str) -> None:
    assert preco.estado_preco_efetivo(llm, _pagina("preco_desconhecido")) == llm


@pytest.mark.parametrize("llm", ESTADOS)
def test_sem_pagina_usa_o_llm(preco: ModuleType, llm: str) -> None:
    assert preco.estado_preco_efetivo(llm, None) == llm


def test_nada_e_desconhecido(preco: ModuleType) -> None:
    assert preco.estado_preco_efetivo(None, None) == "preco_desconhecido"
    assert preco.estado_preco_efetivo(None, _pagina("preco_desconhecido")) == "preco_desconhecido"
