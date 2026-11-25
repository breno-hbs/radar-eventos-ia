"""Testes do F1-07 — relatório Markdown da avaliação.

Assinatura fixada (radar.avaliacao.relatorio):
`gerar_relatorio(metricas: Metricas, *, conjunto: str, versao_prompt: str, modelo: str,
versao_schema: str, custo_total: Decimal, data: date) -> str`, em português: cabeçalho com
conjunto, versões e data (ISO), tabela de métricas, matriz de idioma, divergências e custo.
Números com 3 casas (ponto ou vírgula decimal); "|" e quebras de linha em títulos são
escapados para não quebrar tabelas.
"""

from __future__ import annotations

import importlib
import re
from datetime import date
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest

from .test_metricas import _erro, _ok, _rot

TITULO_PERIGOSO = "Meetup A | B\nlinha nova"


@pytest.fixture
def relatorio() -> ModuleType:
    return importlib.import_module("radar.avaliacao.relatorio")


def _metricas() -> Any:
    metricas = importlib.import_module("radar.avaliacao.metricas")
    return metricas.calcular_metricas(
        [
            (_rot("evt-A1", True), "Acertou", _ok(True)),
            (_rot("evt-B2", True), "Perdido no e_ia", _ok(False)),
            (_rot("evt-C3", False), "Intruso", _ok(True)),
            (_rot("evt-D4", False), "Certo não IA", _ok(False)),
            (_rot("evt-E5", True, "bilingue"), TITULO_PERIGOSO, _ok(True, "pt")),
            (_rot("evt-F6", True), "Com erro", _erro("saida_json_invalido")),
        ]
    )


def _gerar(relatorio: ModuleType) -> str:
    return relatorio.gerar_relatorio(
        _metricas(),
        conjunto="teste",
        versao_prompt="classificador_v1",
        modelo="claude-haiku-5-5",
        versao_schema="1",
        custo_total=Decimal("0.001234"),
        data=date(2026, 11, 25),
    )


def _tem_numero(texto: str, valor: str) -> bool:
    return valor in texto or valor.replace(".", ",") in texto


def test_cabecalho(relatorio: ModuleType) -> None:
    texto = _gerar(relatorio)
    assert isinstance(texto, str)
    assert texto.lstrip().startswith("#")
    for esperado in ("teste", "classificador_v1", "claude-haiku-5-5", "2026-11-25"):
        assert esperado in texto
    assert re.search(r"schema\W+1\b", texto, re.IGNORECASE)


def test_numeros_das_metricas(relatorio: ModuleType) -> None:
    texto = _gerar(relatorio)
    m = _metricas()
    # vp=2 (A1, E5), fp=1, fn=2 (B2 e o erro F6), vn=1
    assert (m.vp, m.fp, m.fn, m.vn, m.erros) == (2, 1, 2, 1, 1)
    for valor in ("0.667", "0.500", "0.571"):  # precisão, recall, F1
        assert _tem_numero(texto, valor), valor
    assert _tem_numero(texto, "0.001234")


def test_matriz_de_idioma(relatorio: ModuleType) -> None:
    texto = _gerar(relatorio)
    assert "bilingue" in texto
    assert re.search(r"idioma", texto, re.IGNORECASE)


def test_cada_divergencia_aparece(relatorio: ModuleType) -> None:
    texto = _gerar(relatorio)
    for id_fonte in ("evt-B2", "evt-C3", "evt-E5", "evt-F6"):
        assert id_fonte in texto
    for titulo in ("Perdido no e_ia", "Intruso", "Com erro"):
        assert titulo in texto
    assert "saida_json_invalido" in texto
    assert "evt-A1" not in texto  # sem divergência
    assert "evt-D4" not in texto


def _barras(linha: str) -> int:
    return len(re.findall(r"(?<!\\)\|", linha))


def test_titulo_com_barra_e_quebra_nao_quebra_a_tabela(relatorio: ModuleType) -> None:
    linhas = _gerar(relatorio).splitlines()
    assert "linha nova" not in [linha.strip() for linha in linhas]
    [indice] = [i for i, linha in enumerate(linhas) if "evt-E5" in linha]
    assert linhas[indice].lstrip().startswith("|")
    assert "linha nova" in linhas[indice]
    inicio = indice
    while inicio > 0 and linhas[inicio - 1].lstrip().startswith("|"):
        inicio -= 1
    fim = indice
    while fim + 1 < len(linhas) and linhas[fim + 1].lstrip().startswith("|"):
        fim += 1
    contagens = {_barras(linha) for linha in linhas[inicio : fim + 1]}
    assert len(contagens) == 1
