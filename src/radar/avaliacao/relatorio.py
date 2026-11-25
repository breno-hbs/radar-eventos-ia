"""Relatório Markdown da avaliação (D14).

Do evento só entram id e título; nada da descrição. Células de tabela são escapadas ("|" e
quebras de linha) para que um título não quebre a tabela.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from radar.avaliacao.metricas import Metricas

_QUEBRAS = re.compile(r"[\r\n  ]+")


def _celula(texto: str) -> str:
    return _QUEBRAS.sub(" ", texto).replace("|", "\\|").strip()


def _num(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:.3f}"


def _tabela(cabecalho: list[str], linhas: list[list[str]]) -> list[str]:
    saida = [
        "| " + " | ".join(cabecalho) + " |",
        "|" + "|".join("---" for _ in cabecalho) + "|",
    ]
    saida += ["| " + " | ".join(_celula(c) for c in linha) + " |" for linha in linhas]
    return saida


def gerar_relatorio(
    metricas: Metricas,
    *,
    conjunto: str,
    versao_prompt: str,
    modelo: str,
    versao_schema: str,
    custo_total: Decimal,
    data: date,
) -> str:
    m = metricas
    total = m.vp + m.fp + m.fn + m.vn
    partes: list[str] = [
        f"# Avaliação do classificador — conjunto {conjunto}",
        "",
        f"- Conjunto: {conjunto} ({total} eventos)",
        f"- Prompt: {versao_prompt}",
        f"- Modelo: {modelo}",
        f"- Versão do schema: {versao_schema}",
        f"- Data: {data.isoformat()}",
        f"- Custo estimado: US$ {custo_total:.6f}",
        "",
        'Positivo = evento de IA. Erro de saída do LLM conta como "não IA" e também na '
        "linha de erros; erro de API interrompe a avaliação (D14). O recall é a métrica "
        "prioritária.",
        "",
        "## Métricas",
        "",
        *_tabela(
            ["Métrica", "Valor"],
            [
                ["Precisão", _num(m.precisao)],
                ["Recall", _num(m.recall)],
                ["F1", _num(m.f1)],
                ["Verdadeiros positivos", str(m.vp)],
                ["Falsos positivos", str(m.fp)],
                ["Falsos negativos", str(m.fn)],
                ["Verdadeiros negativos", str(m.vn)],
                ["Erros de saída do LLM", str(m.erros)],
                ["Acerto de idioma (classificados)", _num(m.acerto_idioma)],
            ],
        ),
        "",
        "## Matriz de idioma",
        "",
        "Linhas: idioma rotulado; colunas: idioma obtido (só eventos classificados).",
        "",
    ]
    if m.matriz_idioma:
        obtidos = sorted({o for linha in m.matriz_idioma.values() for o in linha})
        linhas = [
            [esperado, *(str(m.matriz_idioma[esperado].get(o, 0)) for o in obtidos)]
            for esperado in sorted(m.matriz_idioma)
        ]
        partes += _tabela(["Rotulado \\ obtido", *obtidos], linhas)
    else:
        partes.append("Nenhum evento classificado.")
    partes += ["", "## Divergências", ""]
    if m.divergencias:
        partes += _tabela(
            ["Evento", "Título", "Campo", "Esperado", "Obtido"],
            [[d.id_fonte, d.titulo, d.campo, d.esperado, d.obtido] for d in m.divergencias],
        )
    else:
        partes.append("Nenhuma divergência.")
    partes.append("")
    return "\n".join(partes)
