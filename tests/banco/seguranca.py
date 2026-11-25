"""Travas do banco de teste do F1-05, separadas do conftest para poderem ser testadas.

A fixture `engine` apaga o schema `public` inteiro. Antes de qualquer operação destrutiva,
`resolver_url_teste` aplica, nesta ordem:
1. só lê `TEST_DATABASE_URL` (nunca `DATABASE_URL` como alternativa); sem ela, pula os
   testes de banco — exceto no CI (`GITHUB_ACTIONS=true`), onde é erro;
2. o nome do banco precisa terminar em `_teste`;
3. se `DATABASE_URL` também estiver definida e apontar para o mesmo banco (host, porta e
   nome), aborta.
Nenhuma mensagem de erro mostra a URL (pode ter senha).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError

VARIAVEL_URL = "TEST_DATABASE_URL"
SUFIXO_BANCO = "_teste"
_HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1", ""}


def _identidade_do_banco(url: str) -> tuple[str, int, str]:
    """(host, porta, nome) com host local normalizado e porta padrão 5432."""
    try:
        partes = make_url(url)
    except ArgumentError:
        pytest.fail("URL de banco inválida (valor omitido)")
    host = (partes.host or "").lower()
    host = "localhost" if host in _HOSTS_LOCAIS else host
    return host, partes.port or 5432, partes.database or ""


def resolver_url_teste(ambiente: Mapping[str, str]) -> str:
    url = ambiente.get(VARIAVEL_URL, "").strip()
    if not url:
        if ambiente.get("GITHUB_ACTIONS", "").strip().lower() == "true":
            pytest.fail(f"{VARIAVEL_URL} é obrigatória no CI (container de serviço Postgres)")
        pytest.skip(f"{VARIAVEL_URL} não definida: testes de banco pulados")

    identidade = _identidade_do_banco(url)
    if not identidade[2].endswith(SUFIXO_BANCO):
        pytest.fail(f"o banco de {VARIAVEL_URL} precisa ter nome terminado em {SUFIXO_BANCO!r}")

    url_real = ambiente.get("DATABASE_URL", "").strip()
    if url_real and _identidade_do_banco(url_real) == identidade:
        pytest.fail(f"{VARIAVEL_URL} aponta para o mesmo banco de DATABASE_URL: abortado")
    return url


def recriar_schema(engine: Engine) -> None:
    """Apaga tudo (inclusive `alembic_version`) e deixa o schema `public` vazio."""
    with engine.begin() as conexao:
        conexao.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conexao.execute(text("CREATE SCHEMA public"))


def preparar_banco(
    ambiente: Mapping[str, str],
    criar_engine: Callable[[SecretStr], Engine],
    migrar_para_head: Callable[[SecretStr], None],
) -> Engine:
    """Valida as travas e só então cria o engine, recria o schema e migra para head."""
    url = SecretStr(resolver_url_teste(ambiente))
    engine = criar_engine(url)
    recriar_schema(engine)
    migrar_para_head(url)
    return engine
