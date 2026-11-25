"""Testes do F1-05 — criação do engine e da fábrica de sessões (sem conectar ao banco).

Assinaturas fixadas (radar.banco.sessao):
- `criar_engine(url: SecretStr) -> sqlalchemy.Engine`: `postgres://` e `postgresql://` viram
  `postgresql+psycopg://`; `postgresql+psycopg://` fica como está. A senha nunca aparece em
  `repr`/`str` do engine nem da URL renderizada sem `hide_password=False`.
- `criar_fabrica_sessoes(engine: Engine) -> sessionmaker[Session]`.
"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

SENHA = "senha-falsa-sessao-123"


@pytest.fixture
def sessao_mod() -> ModuleType:
    return importlib.import_module("radar.banco.sessao")


@pytest.mark.parametrize(
    "url",
    [
        f"postgres://usuario:{SENHA}@localhost:5432/radar",
        f"postgresql://usuario:{SENHA}@localhost:5432/radar",
        f"postgresql+psycopg://usuario:{SENHA}@localhost:5432/radar",
    ],
)
def test_url_vira_driver_psycopg(sessao_mod: ModuleType, url: str) -> None:
    engine = sessao_mod.criar_engine(SecretStr(url))
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.username == "usuario"
        assert engine.url.password == SENHA
        assert engine.url.host == "localhost"
        assert engine.url.port == 5432
        assert engine.url.database == "radar"
    finally:
        engine.dispose()


def test_url_com_parametros_preserva_query(sessao_mod: ModuleType) -> None:
    engine = sessao_mod.criar_engine(
        SecretStr(f"postgres://usuario:{SENHA}@db.exemplo.invalid/radar?sslmode=require")
    )
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.query.get("sslmode") == "require"
    finally:
        engine.dispose()


def test_senha_fora_do_repr(sessao_mod: ModuleType) -> None:
    engine = sessao_mod.criar_engine(SecretStr(f"postgres://usuario:{SENHA}@localhost/radar"))
    try:
        assert SENHA not in repr(engine)
        assert SENHA not in str(engine)
        assert SENHA not in repr(engine.url)
        assert SENHA not in str(engine.url)
    finally:
        engine.dispose()


def test_criar_engine_nao_conecta(sessao_mod: ModuleType) -> None:
    """Criar o engine não abre conexão: host inexistente não dá erro aqui."""
    engine = sessao_mod.criar_engine(SecretStr("postgresql://u@host-inexistente.invalid/radar"))
    engine.dispose()


def test_fabrica_de_sessoes_usa_o_engine(sessao_mod: ModuleType) -> None:
    engine = sessao_mod.criar_engine(SecretStr("postgresql://u@localhost/radar"))
    try:
        fabrica = sessao_mod.criar_fabrica_sessoes(engine)
        with fabrica() as sessao:
            assert isinstance(sessao, Session)
            assert sessao.get_bind() is engine
    finally:
        engine.dispose()
