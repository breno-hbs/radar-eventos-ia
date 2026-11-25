"""Modelos de saída dos coletores: evento normalizado e resultado por calendário.

Nenhum modelo guarda conteúdo bruto do feed além dos campos extraídos do evento: falhas e
eventos inválidos carregam só motivos fixos, status HTTP e IDs já validados.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

FUSO_SP = ZoneInfo("America/Sao_Paulo")

# `evt-…` é o ID do evento na Luma; `calev-…` é um evento de calendário sem página própria.
PADRAO_ID_FONTE = r"^(evt|calev)-[A-Za-z0-9]+$"

# TENTATIVE é o padrão da Luma em todos os eventos e não é sinal: vira "desconhecido".
StatusFonte = Literal["confirmado", "cancelado", "desconhecido"]
MotivoFalha = Literal[
    "bloqueado_robots", "status_http", "redirecionamento", "rede", "formato_invalido"
]
MotivoEventoInvalido = Literal[
    "uid_ausente_ou_fora_do_padrao",
    "titulo_ausente",
    "inicio_ausente",
    "horario_sem_fuso",
    "horario_invalido",
    "formato_invalido",
]
Situacao = Literal["ok", "vazio", "falha"]


class EventoNormalizado(BaseModel):
    """Evento extraído de uma fonte, com horários em America/Sao_Paulo."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fonte: Literal["luma"]
    calendario: str
    id_fonte: str = Field(pattern=PADRAO_ID_FONTE)
    titulo: str
    inicio: AwareDatetime
    fim: AwareDatetime | None
    dia_inteiro: bool
    local_texto: str | None
    # URL https do LOCATION (exceto o endereço oculto `luma.com/event/evt-…`).
    local_url: str | None = Field(pattern=r"^https://\S+$")
    endereco_oculto: bool
    latitude: float | None = Field(ge=-90, le=90)
    longitude: float | None = Field(ge=-180, le=180)
    status_fonte: StatusFonte
    link_inscricao: str | None
    descricao: str | None
    organizador: str | None

    @field_validator("titulo")
    @classmethod
    def _titulo_preenchido(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("título vazio")
        return valor

    @field_validator("inicio", "fim")
    @classmethod
    def _em_sao_paulo(cls, valor: datetime | None) -> datetime | None:
        return None if valor is None else valor.astimezone(FUSO_SP)

    @model_validator(mode="after")
    def _coerencia(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude e longitude vêm sempre em par")
        if self.fim is not None and self.fim <= self.inicio:
            raise ValueError("fim precisa ser depois do início")
        if self.endereco_oculto and (self.local_texto is not None or self.local_url is not None):
            raise ValueError("endereço oculto não tem local")
        if self.dia_inteiro and not all(
            _meia_noite(h) for h in (self.inicio, self.fim) if h is not None
        ):
            raise ValueError("evento de dia inteiro começa e termina às 00:00 de São Paulo")
        return self


def _meia_noite(horario: datetime) -> bool:
    return horario.timetz().replace(tzinfo=None) == time(0, 0)


class FalhaExtracao(BaseModel):
    """Falha que impediu ler um calendário. `detalhe` nunca traz conteúdo recebido."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    motivo: MotivoFalha
    status_http: int | None
    detalhe: str | None


class EventoInvalido(BaseModel):
    """Evento pulado por falta de campo obrigatório; guarda só o ID já validado."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id_fonte: str | None = Field(pattern=PADRAO_ID_FONTE)
    motivo: MotivoEventoInvalido


class ResultadoCalendario(BaseModel):
    """Resultado da leitura de um calendário: `ok`, `vazio` (zero eventos) ou `falha`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fonte: Literal["luma"]
    calendario: str
    situacao: Situacao
    eventos: list[EventoNormalizado]
    eventos_invalidos: list[EventoInvalido]
    falha: FalhaExtracao | None

    @model_validator(mode="after")
    def _coerencia(self) -> Self:
        if (self.situacao == "falha") != (self.falha is not None):
            raise ValueError("`falha` é preenchida se e só se a situação é falha")
        if self.situacao == "ok" and not self.eventos:
            raise ValueError("situação ok exige ao menos um evento")
        if self.situacao != "ok" and self.eventos:
            raise ValueError("só a situação ok traz eventos")
        if self.situacao == "vazio" and self.eventos_invalidos:
            raise ValueError("situação vazio não tem eventos inválidos")
        falha_sem_formato = self.falha is not None and self.falha.motivo != "formato_invalido"
        if falha_sem_formato and self.eventos_invalidos:
            raise ValueError("eventos inválidos só acompanham falha de formato")
        if any(e.calendario != self.calendario for e in self.eventos):
            raise ValueError("evento de outro calendário")
        return self
