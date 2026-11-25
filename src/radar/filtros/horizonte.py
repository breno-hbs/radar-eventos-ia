"""Filtro de horizonte, aplicado antes do LLM (spec §2; D11).

Antes da classificação o tipo do evento não é conhecido, então todos usam o horizonte maior
(90 dias). O corte de 30 dias para meetups, palestras, workshops e online é aplicado depois
da classificação.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from radar.filtros.geografico import EventoLocalizado

HORIZONTE_DIAS = 90

MotivoDescarteHorizonte = Literal["passado", "alem_do_horizonte"]


class DescartadoHorizonte(BaseModel):
    """Evento fora da janela de datas; guarda só a identificação."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fonte: Literal["luma"]
    calendario: str
    id_fonte: str
    motivo: MotivoDescarteHorizonte


class ResultadoHorizonte(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    aprovados: list[EventoLocalizado]
    descartados: list[DescartadoHorizonte]


def calcular_termino(inicio: datetime, fim: datetime | None, dia_inteiro: bool) -> datetime:
    """`fim`; sem ele, dia inteiro dura até `inicio + 1 dia` e evento com hora usa `inicio`."""
    if fim is not None:
        return fim
    return inicio + timedelta(days=1) if dia_inteiro else inicio


def filtrar_por_horizonte(itens: Iterable[EventoLocalizado], agora: datetime) -> ResultadoHorizonte:
    """Descarta o que já terminou e o que começa depois de `agora + HORIZONTE_DIAS`.

    Passado: término (`fim`; sem ele, `inicio + 1 dia` em dia inteiro ou `inicio` nos
    demais) <= `agora`. O limite exato do horizonte
    é aprovado. `agora` precisa ter fuso.
    """
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("`agora` precisa ter fuso horário")
    limite = agora + timedelta(days=HORIZONTE_DIAS)

    aprovados: list[EventoLocalizado] = []
    descartados: list[DescartadoHorizonte] = []
    for item in itens:
        evento = item.evento
        termino = calcular_termino(evento.inicio, evento.fim, evento.dia_inteiro)
        motivo: MotivoDescarteHorizonte | None = None
        if termino <= agora:
            motivo = "passado"
        elif evento.inicio > limite:
            motivo = "alem_do_horizonte"
        if motivo is None:
            aprovados.append(item)
        else:
            descartados.append(
                DescartadoHorizonte(
                    fonte=evento.fonte,
                    calendario=evento.calendario,
                    id_fonte=evento.id_fonte,
                    motivo=motivo,
                )
            )
    return ResultadoHorizonte(aprovados=aprovados, descartados=descartados)
