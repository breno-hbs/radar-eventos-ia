"""Avaliação do classificador contra conjuntos rotulados à mão (D7, D14).

Uso: `python -m radar.avaliacao --conjunto teste --saida docs/avaliacao/teste-classificador_v1.md`.
Chama a API de verdade: roda só pelo workflow manual `avaliar.yml`.
"""

from radar.avaliacao.execucao import executar

__all__ = ["executar"]
