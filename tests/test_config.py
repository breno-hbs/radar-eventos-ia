"""Testes do ticket F1-01 — configuração por variáveis de ambiente (Pydantic Settings)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr, ValidationError

if TYPE_CHECKING:
    from radar.config import Configuracao

VARIAVEIS = (
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "FUSO_HORARIO",
    "LIMITE_CHAMADAS_LLM_POR_EXECUCAO",
    "LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO",
)

# Valores falsos, só para teste. Nunca usar credenciais reais aqui.
DATABASE_URL_FALSA = "postgresql://usuario:senha-falsa-db@localhost:5432/radar_teste"
CHAVE_ANTHROPIC_FALSA = "chave-falsa-anthropic-123"
TOKEN_TELEGRAM_FALSO = "token-falso-telegram-456"


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove as variáveis relevantes (com e sem prefixo) e isola o diretório atual."""
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)
        monkeypatch.delenv(nome.lower(), raising=False)
        monkeypatch.delenv(f"RADAR_{nome}", raising=False)
    monkeypatch.chdir(tmp_path)


def _carregar() -> Configuracao:
    from radar.config import carregar_configuracao

    return carregar_configuracao()


# --- Contrato --------------------------------------------------------------------


def test_carregar_configuracao_devolve_configuracao() -> None:
    from radar.config import Configuracao

    assert isinstance(_carregar(), Configuracao)


def test_configuracao_e_pydantic_settings() -> None:
    from pydantic_settings import BaseSettings

    from radar.config import Configuracao

    assert issubclass(Configuracao, BaseSettings)


# --- Valores padrão ----------------------------------------------------------------


def test_padroes_sem_nenhuma_variavel() -> None:
    config = _carregar()
    assert config.database_url is None
    assert config.anthropic_api_key is None
    assert config.telegram_token is None
    assert config.telegram_chat_id is None
    assert config.fuso_horario == "America/Sao_Paulo"
    assert config.limite_chamadas_llm_por_execucao == 50
    assert config.limite_paginas_enriquecimento_por_execucao == 10


def test_fuso_padrao_e_valido_no_zoneinfo() -> None:
    assert ZoneInfo(_carregar().fuso_horario).key == "America/Sao_Paulo"


# --- Leitura das variáveis -----------------------------------------------------------


def test_le_todas_as_variaveis_de_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_FALSA)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_ANTHROPIC_FALSA)
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN_TELEGRAM_FALSO)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("FUSO_HORARIO", "UTC")
    monkeypatch.setenv("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "7")
    monkeypatch.setenv("LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO", "3")

    config = _carregar()

    assert config.database_url is not None
    assert config.database_url.get_secret_value() == DATABASE_URL_FALSA
    assert config.anthropic_api_key is not None
    assert config.anthropic_api_key.get_secret_value() == CHAVE_ANTHROPIC_FALSA
    assert config.telegram_token is not None
    assert config.telegram_token.get_secret_value() == TOKEN_TELEGRAM_FALSO
    assert config.telegram_chat_id == 123456789
    assert config.fuso_horario == "UTC"
    assert config.limite_chamadas_llm_por_execucao == 7
    assert config.limite_paginas_enriquecimento_por_execucao == 3


def test_segredos_sao_secretstr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_FALSA)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_ANTHROPIC_FALSA)
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN_TELEGRAM_FALSO)

    config = _carregar()

    assert isinstance(config.database_url, SecretStr)
    assert isinstance(config.anthropic_api_key, SecretStr)
    assert isinstance(config.telegram_token, SecretStr)


def test_chat_id_negativo_de_grupo_e_aceito(monkeypatch: pytest.MonkeyPatch) -> None:
    """IDs de grupo/canal do Telegram são negativos (ex.: -100...)."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")
    assert _carregar().telegram_chat_id == -1001234567890


def test_carregar_reflete_mudanca_no_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem cache: cada chamada lê o ambiente atual (necessário para testes isolados)."""
    monkeypatch.setenv("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "5")
    assert _carregar().limite_chamadas_llm_por_execucao == 5
    monkeypatch.setenv("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "9")
    assert _carregar().limite_chamadas_llm_por_execucao == 9


def test_prefixo_radar_nao_e_usado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_ANTHROPIC_API_KEY", CHAVE_ANTHROPIC_FALSA)
    monkeypatch.setenv("RADAR_LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "3")

    config = _carregar()

    assert config.anthropic_api_key is None
    assert config.limite_chamadas_llm_por_execucao == 50


@pytest.mark.parametrize("nome_arquivo", [".env", ".env.exemplo"])
def test_nao_le_arquivo_env_automaticamente(tmp_path: Path, nome_arquivo: str) -> None:
    (tmp_path / nome_arquivo).write_text(
        f"ANTHROPIC_API_KEY={CHAVE_ANTHROPIC_FALSA}\n"
        "LIMITE_CHAMADAS_LLM_POR_EXECUCAO=3\n"
        "FUSO_HORARIO=UTC\n",
        encoding="utf-8",
    )

    config = _carregar()

    assert config.anthropic_api_key is None
    assert config.limite_chamadas_llm_por_execucao == 50
    assert config.fuso_horario == "America/Sao_Paulo"


def test_chat_id_nao_aparece_no_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    configuracao = _carregar()
    assert configuracao.telegram_chat_id == 987654321
    assert "987654321" not in repr(configuracao)


# --- Segredos fora de repr/str -----------------------------------------------------------


@pytest.mark.parametrize("formatar", [repr, str, lambda c: f"{c}", lambda c: str(c.model_dump())])
def test_segredos_nao_aparecem_na_representacao(
    monkeypatch: pytest.MonkeyPatch, formatar: Callable[[Configuracao], str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_FALSA)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_ANTHROPIC_FALSA)
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN_TELEGRAM_FALSO)

    texto = formatar(_carregar())

    assert CHAVE_ANTHROPIC_FALSA not in texto
    assert TOKEN_TELEGRAM_FALSO not in texto
    assert DATABASE_URL_FALSA not in texto
    assert "senha-falsa-db" not in texto


def test_segredos_nao_aparecem_no_erro_de_validacao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um erro em outro campo não pode vazar segredos na mensagem (que vai para log)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_ANTHROPIC_FALSA)
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN_TELEGRAM_FALSO)
    monkeypatch.setenv("FUSO_HORARIO", "Fuso/Inexistente")

    with pytest.raises(ValidationError) as erro:
        _carregar()

    assert CHAVE_ANTHROPIC_FALSA not in str(erro.value)
    assert TOKEN_TELEGRAM_FALSO not in str(erro.value)


# --- Validação --------------------------------------------------------------------------


@pytest.mark.parametrize("fuso", ["Fuso/Inexistente", "Sao_Paulo", "GMT-3 BRT", ""])
def test_fuso_invalido_levanta_validation_error(monkeypatch: pytest.MonkeyPatch, fuso: str) -> None:
    monkeypatch.setenv("FUSO_HORARIO", fuso)
    with pytest.raises(ValidationError):
        _carregar()


@pytest.mark.parametrize("fuso", ["UTC", "Europe/Helsinki", "America/Sao_Paulo"])
def test_fuso_valido_e_aceito(monkeypatch: pytest.MonkeyPatch, fuso: str) -> None:
    monkeypatch.setenv("FUSO_HORARIO", fuso)
    assert _carregar().fuso_horario == fuso


@pytest.mark.parametrize("valor", ["0", "-1"])
def test_limite_chamadas_llm_menor_que_um_e_rejeitado(
    monkeypatch: pytest.MonkeyPatch, valor: str
) -> None:
    monkeypatch.setenv("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", valor)
    with pytest.raises(ValidationError):
        _carregar()


def test_limite_chamadas_llm_igual_a_um_e_aceito(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "1")
    assert _carregar().limite_chamadas_llm_por_execucao == 1


def test_limite_paginas_zero_e_aceito(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero desliga o enriquecimento; é um valor válido."""
    monkeypatch.setenv("LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO", "0")
    assert _carregar().limite_paginas_enriquecimento_por_execucao == 0


def test_limite_paginas_negativo_e_rejeitado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO", "-1")
    with pytest.raises(ValidationError):
        _carregar()


@pytest.mark.parametrize(
    ("variavel", "valor"),
    [
        ("TELEGRAM_CHAT_ID", "nao-e-numero"),
        ("LIMITE_CHAMADAS_LLM_POR_EXECUCAO", "muitas"),
        ("LIMITE_PAGINAS_ENRIQUECIMENTO_POR_EXECUCAO", "1.5"),
    ],
)
def test_inteiro_invalido_levanta_validation_error(
    monkeypatch: pytest.MonkeyPatch, variavel: str, valor: str
) -> None:
    monkeypatch.setenv(variavel, valor)
    with pytest.raises(ValidationError):
        _carregar()
