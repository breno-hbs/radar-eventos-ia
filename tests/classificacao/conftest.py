"""Fixtures do F1-06.

Os testes do classificador que gravam no banco reutilizam as fixtures `engine` e `sessao`
de `tests/banco/conftest.py` (Postgres de teste, transação sempre revertida; as travas de
segurança do F1-05 valem aqui também).
"""

from __future__ import annotations

from banco.conftest import engine, sessao, url_teste

__all__ = ["engine", "sessao", "url_teste"]
