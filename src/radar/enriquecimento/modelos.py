"""Resultado do enriquecimento de preço pela página do evento (D4, D18)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.classificacao.modelos import EstadoPreco

__all__ = ["EstadoPreco", "MotivoEnriquecimento", "ResultadoPreco"]

# Conjunto fechado: nenhum texto da página entra no motivo.
MotivoEnriquecimento = Literal[
    "bloqueado_robots",
    "link_invalido",
    "rede",
    "redirecionamento",
    "status_http",
    "pagina_grande",
    "sem_json_ld",
    "json_ld_invalido",
    "sem_offers",
    "oferta_invalida",
]


class ResultadoPreco(BaseModel):
    """Preço de um evento segundo a página.

    `valor_centavos` e `moeda` existem só em `pago_com_valor`. `preco_desconhecido` sempre traz
    o motivo; resultado conclusivo nunca traz motivo.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    estado_preco: EstadoPreco
    valor_centavos: int | None = Field(default=None, ge=0)
    moeda: Literal["BRL"] | None = None
    motivo: MotivoEnriquecimento | None = None
    status_http: int | None = None

    @model_validator(mode="after")
    def _coerencia(self) -> Self:
        com_valor = self.valor_centavos is not None and self.moeda is not None
        sem_valor = self.valor_centavos is None and self.moeda is None
        if self.estado_preco == "pago_com_valor":
            if not com_valor:
                raise ValueError("pago_com_valor exige valor e moeda")
        elif not sem_valor:
            raise ValueError("valor e moeda só existem em pago_com_valor")
        if (self.estado_preco == "preco_desconhecido") != (self.motivo is not None):
            raise ValueError("motivo existe se e só se o preço é desconhecido")
        return self
