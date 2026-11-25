"""Custo ESTIMADO por chamada ao LLM (D13): estimativa a partir da tabela, não garantia.

Preços em US$ por milhão de tokens. Acima de `limite_entrada_curto` tokens de entrada na
chamada, entrada e saída passam para a faixa longa.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

MILHAO = Decimal(1_000_000)
CASAS = Decimal("0.000001")


@dataclass(frozen=True)
class PrecoModelo:
    limite_entrada_curto: int
    entrada_curto: Decimal
    saida_curto: Decimal
    entrada_longo: Decimal
    saida_longo: Decimal


TABELA_PRECOS: dict[str, PrecoModelo] = {
    "claude-haiku-5-5": PrecoModelo(
        limite_entrada_curto=100_000,
        entrada_curto=Decimal("0.10"),
        saida_curto=Decimal("0.50"),
        entrada_longo=Decimal("0.50"),
        saida_longo=Decimal("2.50"),
    ),
}


def calcular_custo(modelo: str, tokens_entrada: int, tokens_saida: int) -> Decimal:
    """Custo estimado em US$ com 6 casas (ROUND_HALF_UP). Modelo fora da tabela → ValueError."""
    preco = TABELA_PRECOS.get(modelo)
    if preco is None:
        raise ValueError(f"modelo sem preço na tabela: {modelo!r}")
    if tokens_entrada > preco.limite_entrada_curto:
        taxa_entrada, taxa_saida = preco.entrada_longo, preco.saida_longo
    else:
        taxa_entrada, taxa_saida = preco.entrada_curto, preco.saida_curto
    bruto = (tokens_entrada * taxa_entrada + tokens_saida * taxa_saida) / MILHAO
    return bruto.quantize(CASAS, rounding=ROUND_HALF_UP)
