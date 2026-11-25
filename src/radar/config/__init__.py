"""Configuração por variáveis de ambiente, validada com Pydantic Settings.

Não lê arquivo `.env` automaticamente: os valores vêm só do ambiente (local ou GitHub Secrets).
Segredos ficam em `SecretStr` para não aparecerem em `repr`, `str` nem em logs.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

FUSO_PADRAO = "America/Sao_Paulo"
LIMITE_AUSENCIAS_PADRAO = 3


class Configuracao(BaseSettings):
    """Parâmetros do radar lidos das variáveis de ambiente (nomes em maiúsculas, sem prefixo)."""

    model_config = SettingsConfigDict(env_prefix="", env_file=None, extra="ignore")

    database_url: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    telegram_token: SecretStr | None = None
    # Dado pessoal: fica fora do repr para não ir parar em log do Actions (repositório público).
    telegram_chat_id: int | None = Field(default=None, repr=False)
    fuso_horario: str = FUSO_PADRAO
    limite_chamadas_llm_por_execucao: int = Field(default=50, ge=1)
    limite_paginas_enriquecimento_por_execucao: int = Field(default=10, ge=0)
    # Ausências contáveis seguidas até o evento virar "nao_encontrado" (D12).
    limite_ausencias_nao_encontrado: int = Field(default=LIMITE_AUSENCIAS_PADRAO, ge=1)
    # Tetos de tokens acumulados por execução do classificador (D13).
    limite_tokens_entrada_por_execucao: int = Field(default=200_000, ge=1)
    limite_tokens_saida_por_execucao: int = Field(default=40_000, ge=1)

    @field_validator(
        "database_url", "anthropic_api_key", "telegram_token", "telegram_chat_id", mode="before"
    )
    @classmethod
    def _vazio_vira_none(cls, valor: object) -> object:
        """Variável exportada vazia (como no `.env.exemplo`) equivale a não configurada."""
        if isinstance(valor, str) and valor.strip() == "":
            return None
        return valor

    @field_validator("limite_ausencias_nao_encontrado", mode="before")
    @classmethod
    def _limite_ausencias_vazio_usa_padrao(cls, valor: object) -> object:
        if isinstance(valor, str) and valor.strip() == "":
            return LIMITE_AUSENCIAS_PADRAO
        return valor

    @field_validator("fuso_horario")
    @classmethod
    def _validar_fuso(cls, valor: str) -> str:
        try:
            ZoneInfo(valor)
        except (ZoneInfoNotFoundError, ValueError) as erro:
            raise ValueError(f"fuso horário IANA inválido: {valor!r}") from erro
        return valor


def carregar_configuracao() -> Configuracao:
    """Lê o ambiente atual a cada chamada (sem cache)."""
    return Configuracao()
