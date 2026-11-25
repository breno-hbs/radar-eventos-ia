"""Ambiente do Alembic.

Usa a conexão recebida em `config.attributes["connection"]` (caminho de
`radar.banco.migrar`); na linha de comando, lê `DATABASE_URL` via `carregar_configuracao()`.
A URL nunca fica no `alembic.ini` nem aparece em log.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from radar.banco.modelos import Base
from radar.banco.sessao import criar_engine
from radar.config import carregar_configuracao

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

metadata_alvo = Base.metadata


def _migrar(conexao: Connection) -> None:
    context.configure(connection=conexao, target_metadata=metadata_alvo)
    with context.begin_transaction():
        context.run_migrations()


def executar_online() -> None:
    conexao = config.attributes.get("connection")
    if conexao is not None:
        _migrar(conexao)
        return
    url = carregar_configuracao().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL não configurada")
    engine = criar_engine(url)
    try:
        with engine.connect() as nova_conexao:
            _migrar(nova_conexao)
            nova_conexao.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("modo offline do Alembic não é usado neste projeto")
executar_online()
