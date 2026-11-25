"""Fixtures do F1-08: reusa o Postgres de teste de `tests/banco/conftest.py`."""

from __future__ import annotations

from banco.conftest import engine, sessao, url_teste

__all__ = ["engine", "sessao", "url_teste"]
