#!/usr/bin/env bash
# Critério de "pronto" de todo ticket: lint, formatação e testes.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check"
python -m ruff check .

echo "==> ruff format --check"
python -m ruff format --check .

echo "==> pytest"
python -m pytest
