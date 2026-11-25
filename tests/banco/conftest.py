"""Fixtures de banco do F1-05.

Os testes de banco usam um Postgres de verdade, apontado por `TEST_DATABASE_URL`. As travas
(variável obrigatória no CI, nome terminado em `_teste`, banco diferente do de
`DATABASE_URL`) estão em `seguranca.py` e rodam antes de qualquer operação destrutiva.

Os módulos de `radar.banco` são importados dentro das fixtures para que a falta da
implementação apareça teste a teste, e não como erro de coleta.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .seguranca import preparar_banco, resolver_url_teste


@pytest.fixture(scope="session")
def url_teste() -> SecretStr:
    return SecretStr(resolver_url_teste(os.environ))


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine do banco de teste, com o schema recriado do zero e migrado para head."""
    resolver_url_teste(os.environ)  # pula/falha antes de importar a implementação
    from radar.banco.migrar import migrar_para_head
    from radar.banco.sessao import criar_engine

    motor = preparar_banco(os.environ, criar_engine, migrar_para_head)
    yield motor
    motor.dispose()


@pytest.fixture
def sessao(engine: Engine) -> Iterator[Session]:
    """Sessão dentro de uma transação externa que é sempre revertida no fim do teste.

    `join_transaction_mode="create_savepoint"`: `commit()`/`rollback()` do código testado
    agem num savepoint, e nada sobrevive ao teste.
    """
    conexao = engine.connect()
    transacao = conexao.begin()
    sessao_teste = Session(bind=conexao, join_transaction_mode="create_savepoint")
    try:
        yield sessao_teste
    finally:
        sessao_teste.close()
        transacao.rollback()
        conexao.close()


def linhas(sessao: Session, sql: str, **parametros: Any) -> list[dict[str, Any]]:
    """Executa SQL cru e devolve as linhas como dicts (independe dos nomes das classes ORM)."""
    sessao.flush()
    return [dict(linha) for linha in sessao.execute(text(sql), parametros).mappings()]
