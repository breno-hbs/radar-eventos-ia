"""Eventos, saídas do classificador e enriquecimentos sintéticos para o F1-09."""

from __future__ import annotations

import importlib
import itertools
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from classificacao.fabricas import campos_evento, saida_valida

SP = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=SP)
# sábado, 05/12/2026, 19:00–21:00 em São Paulo
INICIO = datetime(2026, 12, 5, 22, 0, tzinfo=ZoneInfo("UTC"))
FIM = INICIO + timedelta(hours=2)

_contador = itertools.count(1)


def evento(**campos: Any) -> Any:
    """`Evento` ORM em memória (sem sessão)."""
    from radar.banco.modelos import Evento

    numero = next(_contador)
    campos.setdefault("id_fonte", f"evt-Aviso{numero}")
    campos.setdefault("titulo", "Meetup de IA em SP")
    campos.setdefault("inicio", INICIO)
    campos.setdefault("fim", FIM)
    campos.setdefault("link_inscricao", f"https://luma.com/aviso-{numero}")
    return Evento(**campos_evento(**campos))


def saida(**campos: Any) -> Any:
    modelos = importlib.import_module("radar.classificacao.modelos")
    return modelos.SaidaClassificador.model_validate(saida_valida(**campos))


def enriquecimento(estado: str, valor_centavos: int | None = None) -> Any:
    from radar.banco.modelos import Enriquecimento

    pago = estado == "pago_com_valor" and valor_centavos is not None
    return Enriquecimento(
        estado_preco=estado,
        valor_centavos=valor_centavos if pago else None,
        moeda="BRL" if pago else None,
        motivo="sem_offers" if estado == "preco_desconhecido" else None,
    )
