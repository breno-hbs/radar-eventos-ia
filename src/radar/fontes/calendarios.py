"""Lista versionada de calendários da Luma (`config/fontes.yaml`).

O arquivo guarda só slug, nome, entidade e ID; a URL do feed é montada pelo coletor
(`radar.fontes.luma.url_ical`), o que impede apontar a coleta para outro host.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Prefixo de ID aceito para cada tipo de entidade do iCal da Luma.
_PREFIXO_POR_ENTIDADE = {"calendar": "cal-", "discover": "discplace-"}


class CalendarioLuma(BaseModel):
    """Um feed iCal da Luma: calendário de organizador ou feed de cidade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    nome: str = Field(pattern=r"\S")
    entidade: Literal["calendar", "discover"]
    id: str = Field(pattern=r"^(cal|discplace)-[A-Za-z0-9]+$")

    @model_validator(mode="after")
    def _prefixo_combina_com_entidade(self) -> Self:
        if not self.id.startswith(_PREFIXO_POR_ENTIDADE[self.entidade]):
            raise ValueError("o prefixo do id não combina com a entidade")
        return self


class _ArquivoFontes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendarios_luma: list[CalendarioLuma]


def carregar_calendarios(caminho: Path) -> list[CalendarioLuma]:
    """Lê e valida o arquivo; qualquer problema vira `ValueError` (ou erro do YAML)."""
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    calendarios = _ArquivoFontes.model_validate(dados).calendarios_luma
    slugs = [c.slug for c in calendarios]
    if len(set(slugs)) != len(slugs):
        raise ValueError("slug duplicado em config/fontes.yaml")
    ids = [(c.entidade, c.id) for c in calendarios]
    if len(set(ids)) != len(ids):
        raise ValueError("calendário repetido em config/fontes.yaml")
    return calendarios
