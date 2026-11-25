"""Testes das travas do banco de teste (F1-05): nenhuma configuração perigosa chega a
executar `DROP SCHEMA`. Usam engine falso; não precisam de Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from pydantic import SecretStr

from .seguranca import preparar_banco, resolver_url_teste

SENHA = "senha-secreta-xyz"
URL_TESTE = f"postgresql+psycopg://postgres:{SENHA}@localhost:5433/radar_teste"


class _ConexaoFalsa:
    def __init__(self, registro: list[str]) -> None:
        self._registro = registro

    def execute(self, comando: Any, *args: Any, **kwargs: Any) -> None:
        self._registro.append(str(comando))


class _EngineFalso:
    def __init__(self) -> None:
        self.comandos: list[str] = []

    @contextmanager
    def begin(self) -> Iterator[_ConexaoFalsa]:
        yield _ConexaoFalsa(self.comandos)

    @contextmanager
    def connect(self) -> Iterator[_ConexaoFalsa]:
        yield _ConexaoFalsa(self.comandos)


class _Chamadas:
    def __init__(self) -> None:
        self.ordem: list[str] = []
        self.engine = _EngineFalso()

    def criar_engine(self, url: SecretStr) -> _EngineFalso:
        assert isinstance(url, SecretStr)
        self.ordem.append("criar_engine")
        return self.engine

    def migrar(self, url: SecretStr) -> None:
        assert isinstance(url, SecretStr)
        self.ordem.append("migrar")


def _preparar(ambiente: dict[str, str]) -> _Chamadas:
    chamadas = _Chamadas()
    preparar_banco(ambiente, chamadas.criar_engine, chamadas.migrar)  # type: ignore[arg-type]
    return chamadas


def _nada_destrutivo(chamadas: _Chamadas) -> None:
    assert chamadas.ordem == []
    assert not any("DROP" in c.upper() for c in chamadas.engine.comandos)


def _sem_url_na_mensagem(mensagem: str, *urls: str) -> None:
    assert SENHA not in mensagem
    for url in urls:
        assert url not in mensagem


# --- Caminho feliz ----------------------------------------------------------------------------


def test_ambiente_valido_recria_schema_e_migra_nesta_ordem() -> None:
    chamadas = _Chamadas()
    engine = preparar_banco(
        {"TEST_DATABASE_URL": URL_TESTE},
        chamadas.criar_engine,  # type: ignore[arg-type]
        chamadas.migrar,
    )
    assert engine is chamadas.engine
    assert chamadas.ordem == ["criar_engine", "migrar"]
    assert any("DROP SCHEMA" in c.upper() for c in chamadas.engine.comandos)


def test_url_valida_e_devolvida() -> None:
    assert resolver_url_teste({"TEST_DATABASE_URL": URL_TESTE}) == URL_TESTE


def test_database_url_de_outro_banco_e_aceita() -> None:
    ambiente = {
        "TEST_DATABASE_URL": URL_TESTE,
        "DATABASE_URL": f"postgresql://postgres:{SENHA}@localhost:5433/radar",
    }
    assert resolver_url_teste(ambiente) == URL_TESTE


# --- Travas -----------------------------------------------------------------------------------


@pytest.mark.parametrize("valor", [None, "", "   "])
def test_sem_variavel_fora_do_ci_pula(valor: str | None) -> None:
    ambiente = {} if valor is None else {"TEST_DATABASE_URL": valor}
    chamadas = _Chamadas()
    with pytest.raises(pytest.skip.Exception):
        preparar_banco(ambiente, chamadas.criar_engine, chamadas.migrar)  # type: ignore[arg-type]
    _nada_destrutivo(chamadas)


def test_sem_variavel_no_ci_e_erro() -> None:
    chamadas = _Chamadas()
    with pytest.raises(pytest.fail.Exception):
        preparar_banco({"GITHUB_ACTIONS": "true"}, chamadas.criar_engine, chamadas.migrar)  # type: ignore[arg-type]
    _nada_destrutivo(chamadas)


@pytest.mark.parametrize("github_actions", [None, "true"])
def test_database_url_nunca_e_usada_como_alternativa(github_actions: str | None) -> None:
    ambiente = {"DATABASE_URL": f"postgresql://postgres:{SENHA}@localhost:5433/radar_teste"}
    if github_actions:
        ambiente["GITHUB_ACTIONS"] = github_actions
    esperado = pytest.fail.Exception if github_actions else pytest.skip.Exception
    chamadas = _Chamadas()
    with pytest.raises(esperado) as erro:
        preparar_banco(ambiente, chamadas.criar_engine, chamadas.migrar)  # type: ignore[arg-type]
    _nada_destrutivo(chamadas)
    _sem_url_na_mensagem(str(erro.value), ambiente["DATABASE_URL"])


@pytest.mark.parametrize("banco", ["radar", "radar_teste_real", "teste", "radar-teste", ""])
def test_nome_sem_sufixo_teste_e_erro(banco: str) -> None:
    url = f"postgresql+psycopg://postgres:{SENHA}@localhost:5433/{banco}"
    chamadas = _Chamadas()
    with pytest.raises(pytest.fail.Exception) as erro:
        preparar_banco({"TEST_DATABASE_URL": url}, chamadas.criar_engine, chamadas.migrar)  # type: ignore[arg-type]
    _nada_destrutivo(chamadas)
    _sem_url_na_mensagem(str(erro.value), url)


@pytest.mark.parametrize(
    "database_url",
    [
        f"postgresql+psycopg://postgres:{SENHA}@localhost:5433/radar_teste",
        f"postgres://outro:{SENHA}@localhost:5433/radar_teste",
        f"postgresql://postgres:{SENHA}@127.0.0.1:5433/radar_teste?sslmode=disable",
    ],
)
def test_mesmo_banco_da_database_url_e_erro(database_url: str) -> None:
    ambiente = {"TEST_DATABASE_URL": URL_TESTE, "DATABASE_URL": database_url}
    chamadas = _Chamadas()
    with pytest.raises(pytest.fail.Exception) as erro:
        preparar_banco(ambiente, chamadas.criar_engine, chamadas.migrar)  # type: ignore[arg-type]
    _nada_destrutivo(chamadas)
    _sem_url_na_mensagem(str(erro.value), URL_TESTE, database_url)


def test_porta_padrao_conta_como_mesmo_banco() -> None:
    ambiente = {
        "TEST_DATABASE_URL": f"postgresql://u:{SENHA}@db.exemplo.invalid/radar_teste",
        "DATABASE_URL": f"postgresql://u:{SENHA}@db.exemplo.invalid:5432/radar_teste",
    }
    with pytest.raises(pytest.fail.Exception):
        resolver_url_teste(ambiente)


def test_database_url_vazia_e_ignorada() -> None:
    assert resolver_url_teste({"TEST_DATABASE_URL": URL_TESTE, "DATABASE_URL": ""}) == URL_TESTE
