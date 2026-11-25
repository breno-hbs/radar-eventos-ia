"""Linha de comando: `python -m radar.avaliacao --conjunto teste|dev --saida <arquivo.md>`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from radar.avaliacao.execucao import CONJUNTOS, FUSO, executar
from radar.config import carregar_configuracao


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m radar.avaliacao", description="Avalia o classificador (D14)."
    )
    parser.add_argument("--conjunto", required=True, choices=CONJUNTOS)
    parser.add_argument("--saida", required=True, type=Path, help="relatório Markdown")
    parser.add_argument("--dir-avaliacao", type=Path, default=Path("avaliacao"))
    parser.add_argument("--dir-fixtures", type=Path, default=Path("tests/fixtures/luma"))
    parser.add_argument(
        "--fontes", type=Path, default=Path("config/fontes.yaml"), help="calendários (D15)"
    )
    parser.add_argument(
        "--diagnostico",
        type=Path,
        default=None,
        help="diretório para a resposta bruta dos erros de saída (só artifact, D15)",
    )
    args = parser.parse_args(argv)

    try:
        metricas = executar(
            args.conjunto,
            args.saida,
            config=carregar_configuracao(),
            cliente=None,
            dir_avaliacao=args.dir_avaliacao,
            dir_fixtures=args.dir_fixtures,
            arquivo_fontes=args.fontes,
            dir_diagnostico=args.diagnostico,
            agora=datetime.now(ZoneInfo(FUSO)),
        )
    except (ValueError, RuntimeError, OSError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    print(
        f"Relatório gravado em {args.saida}: recall={metricas.recall} "
        f"precisão={metricas.precisao} f1={metricas.f1} erros={metricas.erros}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
