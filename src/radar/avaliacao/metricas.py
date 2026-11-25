"""Métricas do classificador contra os rótulos (D14).

Positivo = evento de IA. Erro de saída do LLM (`saida_*`) conta como previsão "não IA" — o
radar não avisaria o evento — e também em `erros`. Erro de API não é medida do classificador:
interrompe a avaliação em `executar` e é recusado aqui (ValueError). O acerto de idioma é
medido só entre os classificados com sucesso. F1 é 0 quando o recall é 0 (mesmo sem
precisão) e só fica sem valor quando o recall não existe.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from radar.avaliacao.dados import RotuloAvaliacao
from radar.classificacao.classificador import ResultadoEvento

CASAS = 3


@dataclass(frozen=True)
class Divergencia:
    id_fonte: str
    titulo: str
    campo: str  # "e_ia", "idioma" ou "erro"
    esperado: str
    obtido: str


@dataclass
class Metricas:
    vp: int = 0
    fp: int = 0
    fn: int = 0
    vn: int = 0
    erros: int = 0
    precisao: float | None = None
    recall: float | None = None
    f1: float | None = None
    acerto_idioma: float | None = None
    matriz_idioma: dict[str, dict[str, int]] = field(default_factory=dict)
    divergencias: list[Divergencia] = field(default_factory=list)


def _razao(numerador: int, denominador: int) -> float | None:
    return None if denominador == 0 else numerador / denominador


def _ia(valor: bool) -> str:
    return "IA" if valor else "não IA"


def calcular_metricas(
    itens: Sequence[tuple[RotuloAvaliacao, str, ResultadoEvento]],
) -> Metricas:
    m = Metricas()
    idioma_certos = 0
    idioma_total = 0
    for rotulo, titulo, resultado in itens:
        if resultado.erro_api is not None:
            raise ValueError(
                f"resultado de {rotulo.id_fonte} tem erro de API "
                f"({resultado.erro_api.categoria}); a avaliação deveria ter sido interrompida"
            )
        saida = resultado.saida
        if saida is None:
            m.erros += 1
            previsto = False
            m.divergencias.append(
                Divergencia(
                    rotulo.id_fonte,
                    titulo,
                    "erro",
                    _ia(rotulo.e_ia),
                    resultado.motivo_erro or "erro",
                )
            )
        else:
            previsto = saida.e_ia
            if previsto != rotulo.e_ia:
                m.divergencias.append(
                    Divergencia(rotulo.id_fonte, titulo, "e_ia", _ia(rotulo.e_ia), _ia(previsto))
                )
            linha = m.matriz_idioma.setdefault(rotulo.idioma, {})
            linha[saida.idioma] = linha.get(saida.idioma, 0) + 1
            idioma_total += 1
            if saida.idioma == rotulo.idioma:
                idioma_certos += 1
            else:
                m.divergencias.append(
                    Divergencia(rotulo.id_fonte, titulo, "idioma", rotulo.idioma, saida.idioma)
                )

        if rotulo.e_ia and previsto:
            m.vp += 1
        elif rotulo.e_ia:
            m.fn += 1
        elif previsto:
            m.fp += 1
        else:
            m.vn += 1

    precisao = _razao(m.vp, m.vp + m.fp)
    recall = _razao(m.vp, m.vp + m.fn)
    if recall == 0:
        m.f1 = 0.0
    elif precisao is not None and recall is not None:
        m.f1 = round(2 * precisao * recall / (precisao + recall), CASAS)
    m.precisao = None if precisao is None else round(precisao, CASAS)
    m.recall = None if recall is None else round(recall, CASAS)
    acerto = _razao(idioma_certos, idioma_total)
    m.acerto_idioma = None if acerto is None else round(acerto, CASAS)
    return m
