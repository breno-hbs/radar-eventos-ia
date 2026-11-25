"""Engine e fábrica de sessões. A URL vem como `SecretStr` e nunca é registrada em log."""

from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

DRIVER = "postgresql+psycopg"
_SEM_DRIVER = frozenset({"postgres", "postgresql"})


def criar_engine(url: SecretStr) -> Engine:
    """Cria o engine (sem conectar). `postgres://` e `postgresql://` passam a usar psycopg 3.

    `echo=False` e `hide_parameters=True`: nem SQL com valores nem URL vão para o log.
    """
    endereco = make_url(url.get_secret_value())
    if endereco.drivername in _SEM_DRIVER:
        endereco = endereco.set(drivername=DRIVER)
    return create_engine(endereco, echo=False, hide_parameters=True, pool_pre_ping=True)


def criar_fabrica_sessoes(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
