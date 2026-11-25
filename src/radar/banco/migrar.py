"""Aplica as migrations do Alembic por código, sem depender do diretório atual nem do
`alembic.ini`. A URL nunca é posta na configuração do Alembic: a conexão é passada pronta."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import SecretStr

from radar.banco.sessao import criar_engine

PASTA_MIGRACOES = Path(__file__).resolve().parent / "migracoes"


def _executar(url: SecretStr, alvo: str, subir: bool) -> None:
    configuracao = Config()
    configuracao.set_main_option("script_location", str(PASTA_MIGRACOES))
    engine = criar_engine(url)
    try:
        with engine.begin() as conexao:
            configuracao.attributes["connection"] = conexao
            if subir:
                command.upgrade(configuracao, alvo)
            else:
                command.downgrade(configuracao, alvo)
    finally:
        engine.dispose()


def migrar_para_head(url: SecretStr) -> None:
    _executar(url, "head", subir=True)


def reverter_para_base(url: SecretStr) -> None:
    _executar(url, "base", subir=False)
