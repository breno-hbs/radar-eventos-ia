"""Fábricas de modelos de entrada (`EventoNormalizado`, `ResultadoCalendario`,
`EventoLocalizado`) para os testes do F1-05. Só dados sintéticos."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from radar.filtros.geografico import EventoLocalizado, Localizacao
from radar.fontes.modelos import (
    EventoInvalido,
    EventoNormalizado,
    FalhaExtracao,
    ResultadoCalendario,
)

SP = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=SP)
INICIO_PADRAO = AGORA + timedelta(days=10)


def evento(
    id_fonte: str = "evt-Alfa1", calendario: str = "cal-a", **campos: Any
) -> EventoNormalizado:
    base: dict[str, Any] = {
        "fonte": "luma",
        "calendario": calendario,
        "id_fonte": id_fonte,
        "titulo": "Meetup de IA",
        "inicio": INICIO_PADRAO,
        "fim": INICIO_PADRAO + timedelta(hours=2),
        "dia_inteiro": False,
        "local_texto": "Av. Paulista, 1000 - São Paulo",
        "local_url": None,
        "endereco_oculto": False,
        "latitude": -23.561234,
        "longitude": -46.656789,
        "status_fonte": "desconhecido",
        "link_inscricao": f"https://luma.com/{id_fonte.lower()}",
        "descricao": "Descrição do evento",
        "organizador": "Organização X",
    }
    base.update(campos)
    return EventoNormalizado(**base)


def ok(
    calendario: str,
    eventos: Sequence[EventoNormalizado],
    invalidos: Sequence[str] = (),
) -> ResultadoCalendario:
    return ResultadoCalendario(
        fonte="luma",
        calendario=calendario,
        situacao="ok",
        eventos=list(eventos),
        eventos_invalidos=[
            EventoInvalido(id_fonte=id_fonte, motivo="horario_invalido") for id_fonte in invalidos
        ],
        falha=None,
    )


def vazio(calendario: str) -> ResultadoCalendario:
    return ResultadoCalendario(
        fonte="luma",
        calendario=calendario,
        situacao="vazio",
        eventos=[],
        eventos_invalidos=[],
        falha=None,
    )


def falha(
    calendario: str, motivo: str = "status_http", status_http: int | None = 503
) -> ResultadoCalendario:
    return ResultadoCalendario(
        fonte="luma",
        calendario=calendario,
        situacao="falha",
        eventos=[],
        eventos_invalidos=[],
        falha=FalhaExtracao(motivo=motivo, status_http=status_http, detalhe=None),
    )


def localizado(
    item: EventoNormalizado, situacao: str = "sp", evidencia: str = "geo"
) -> EventoLocalizado:
    return EventoLocalizado(
        evento=item, localizacao=Localizacao(situacao=situacao, evidencia=evidencia)
    )


def aprovar(resultados: Sequence[ResultadoCalendario]) -> list[EventoLocalizado]:
    """Aprova (como `sp`/`geo`) todos os eventos dos resultados ok."""
    return [localizado(item) for r in resultados for item in r.eventos]
