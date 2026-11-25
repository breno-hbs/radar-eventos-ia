"""Testes do F1-07 — métricas do classificador contra os rótulos.

Assinaturas fixadas (radar.avaliacao.metricas):
- `calcular_metricas(itens: Sequence[tuple[RotuloAvaliacao, str, ResultadoEvento]])
  -> Metricas`; cada item é (rótulo, título do evento, resultado). O título entra no item
  porque `ResultadoEvento` não o carrega e as divergências o exibem.
- `Metricas`: vp, fp, fn, vn, erros: int; precisao, recall, f1: número com 3 casas ou None
  (divisão por zero); acerto_idioma: número ou None (entre os classificados ok);
  matriz_idioma: dict[esperado, dict[obtido, contagem]] (só classificados ok);
  divergencias: list[Divergencia(id_fonte, titulo, campo, esperado, obtido)].
- e_ia: positivo = IA. Erro de SAÍDA (saida_*) conta como previsão "não IA" e também em
  `erros`; vira divergência com campo "erro" e `obtido` = motivo_erro.
- Resultado com `erro_api` não nulo → ValueError (erro de API interrompe em `executar`).
- F1 = 0.0 quando recall == 0 (vp = 0), mesmo com precisão None; F1 None só se recall None.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest
from classificacao.fabricas import saida_valida

from .fabricas import rotulo


@pytest.fixture
def metricas() -> ModuleType:
    return importlib.import_module("radar.avaliacao.metricas")


def _rot(id_fonte: str, e_ia: bool, idioma: str = "pt") -> Any:
    dados = importlib.import_module("radar.avaliacao.dados")
    return dados.RotuloAvaliacao(**rotulo(id_fonte, e_ia, idioma))


def _ok(e_ia: bool, idioma: str = "pt") -> Any:
    classificador = importlib.import_module("radar.classificacao.classificador")
    modelos = importlib.import_module("radar.classificacao.modelos")
    trecho = "" if idioma == "desconhecido" else "Palestras em português"
    saida = modelos.SaidaClassificador.model_validate(
        saida_valida(e_ia=e_ia, idioma=idioma, trecho_idioma=trecho)
    )
    return classificador.ResultadoEvento(
        saida=saida,
        motivo_erro=None,
        tokens_entrada=1_000,
        tokens_saida=200,
        custo_usd=None,
        erro_api=None,
    )


def _erro(motivo: str = "saida_json_invalido") -> Any:
    classificador = importlib.import_module("radar.classificacao.classificador")
    return classificador.ResultadoEvento(
        saida=None,
        motivo_erro=motivo,
        tokens_entrada=None,
        tokens_saida=None,
        custo_usd=None,
        erro_api=None,
    )


def _num(valor: Any) -> float:
    assert valor is not None
    return float(valor)


def test_todos_certos(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [
            (_rot("evt-A1", True), "A", _ok(True)),
            (_rot("evt-B2", True, "en"), "B", _ok(True, "en")),
            (_rot("evt-C3", False), "C", _ok(False)),
        ]
    )
    assert (m.vp, m.fp, m.fn, m.vn, m.erros) == (2, 0, 0, 1, 0)
    assert _num(m.precisao) == pytest.approx(1.0)
    assert _num(m.recall) == pytest.approx(1.0)
    assert _num(m.f1) == pytest.approx(1.0)
    assert _num(m.acerto_idioma) == pytest.approx(1.0)
    assert m.divergencias == []


def test_um_falso_negativo(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [
            (_rot("evt-A1", True), "A", _ok(True)),
            (_rot("evt-B2", True), "Perdido", _ok(False)),
        ]
    )
    assert (m.vp, m.fp, m.fn, m.vn) == (1, 0, 1, 0)
    assert _num(m.precisao) == pytest.approx(1.0)
    assert _num(m.recall) == pytest.approx(0.5)
    assert _num(m.f1) == pytest.approx(0.667, abs=1e-3)
    [div] = m.divergencias
    assert (div.id_fonte, div.titulo, div.campo) == ("evt-B2", "Perdido", "e_ia")


def test_um_falso_positivo(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [
            (_rot("evt-A1", True), "A", _ok(True)),
            (_rot("evt-B2", False), "Intruso", _ok(True)),
        ]
    )
    assert (m.vp, m.fp, m.fn, m.vn) == (1, 1, 0, 0)
    assert _num(m.precisao) == pytest.approx(0.5)
    assert _num(m.recall) == pytest.approx(1.0)
    assert _num(m.f1) == pytest.approx(0.667, abs=1e-3)
    assert [(d.id_fonte, d.campo) for d in m.divergencias] == [("evt-B2", "e_ia")]


def test_metricas_com_tres_casas(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [
            (_rot("evt-A1", True), "A", _ok(True)),
            (_rot("evt-B2", True), "B", _ok(False)),
            (_rot("evt-C3", True), "C", _ok(False)),
        ]
    )
    assert _num(m.recall) == pytest.approx(0.333, abs=1e-9)


def test_sem_positivos_precisao_none(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [
            (_rot("evt-A1", False), "A", _ok(False)),
            (_rot("evt-B2", False), "B", _ok(False)),
        ]
    )
    assert (m.vp, m.fp, m.fn, m.vn) == (0, 0, 0, 2)
    assert m.precisao is None
    assert m.recall is None
    assert m.f1 is None


def test_erro_conta_como_falso_negativo_quando_e_ia(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [(_rot("evt-A1", True), "Com erro", _erro("saida_json_invalido"))]
    )
    assert (m.vp, m.fp, m.fn, m.vn, m.erros) == (0, 0, 1, 0, 1)
    assert m.precisao is None
    assert _num(m.recall) == pytest.approx(0.0)
    assert _num(m.f1) == pytest.approx(0.0)  # recall 0 → F1 0, mesmo com precisão None
    assert m.acerto_idioma is None
    assert m.matriz_idioma == {}
    erros = [d for d in m.divergencias if d.campo == "erro"]
    assert [(d.id_fonte, d.titulo, d.obtido) for d in erros] == [
        ("evt-A1", "Com erro", "saida_json_invalido")
    ]


def test_erro_conta_como_verdadeiro_negativo_quando_nao_e_ia(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas([(_rot("evt-A1", False), "A", _erro("saida_truncada"))])
    assert (m.vp, m.fp, m.fn, m.vn, m.erros) == (0, 0, 0, 1, 1)


def test_f1_zero_quando_nenhum_positivo_acertado(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [
            (_rot("evt-A1", True), "A", _ok(False)),
            (_rot("evt-B2", False), "B", _ok(True)),
        ]
    )
    assert (m.vp, m.fp, m.fn, m.vn) == (0, 1, 1, 0)
    assert _num(m.precisao) == pytest.approx(0.0)
    assert _num(m.recall) == pytest.approx(0.0)
    assert _num(m.f1) == pytest.approx(0.0)


@pytest.mark.parametrize("categoria", ["servidor", "rate_limit", "outro"])
def test_resultado_com_erro_de_api_e_recusado(metricas: ModuleType, categoria: str) -> None:
    # erro de API interrompe a avaliação em `executar`; nunca chega às métricas
    classificador = importlib.import_module("radar.classificacao.classificador")
    cliente = importlib.import_module("radar.classificacao.cliente")
    resultado = classificador.ResultadoEvento(
        saida=None,
        motivo_erro=f"api_{categoria}",
        tokens_entrada=None,
        tokens_saida=None,
        custo_usd=None,
        erro_api=cliente.ErroAPI(categoria=categoria, status_http=None),
    )
    with pytest.raises(ValueError):
        metricas.calcular_metricas(
            [(_rot("evt-A1", True), "A", _ok(True)), (_rot("evt-B2", True), "B", resultado)]
        )


def test_idioma_bilingue_contra_pt_e_divergencia(metricas: ModuleType) -> None:
    m = metricas.calcular_metricas(
        [
            (_rot("evt-A1", True, "bilingue"), "Evento bilíngue", _ok(True, "pt")),
            (_rot("evt-B2", True, "pt"), "B", _ok(True, "pt")),
            (_rot("evt-C3", True, "en"), "C", _erro()),
        ]
    )
    assert _num(m.acerto_idioma) == pytest.approx(0.5)
    assert m.matriz_idioma == {"bilingue": {"pt": 1}, "pt": {"pt": 1}}
    idioma = [d for d in m.divergencias if d.campo == "idioma"]
    assert [(d.id_fonte, d.titulo, d.esperado, d.obtido) for d in idioma] == [
        ("evt-A1", "Evento bilíngue", "bilingue", "pt")
    ]
