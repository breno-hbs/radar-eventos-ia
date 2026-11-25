"""Dados sintéticos do F1-06: evento ORM em memória e saída válida do classificador."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from radar.banco.modelos import Evento

SP = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=SP)
INICIO_PADRAO = AGORA + timedelta(days=10)

LOCAL_TEXTO = "Av. Paulista, 1000 - São Paulo"
DESCRICAO = (
    "Encontro gratuito sobre modelos de linguagem. Palestras em português. "
    "Presencial na Av. Paulista."
)


def saida_valida(**campos: Any) -> dict[str, Any]:
    """Saída coerente com `evento_orm()` (localizacao do filtro "sp"): todo trecho aparece em
    DESCRICAO ou LOCAL_TEXTO e, como o filtro já decidiu a localização, a resposta é
    "nao_se_aplica" (D6: com filtro diferente de "indefinida", outra resposta é indevida)."""
    base: dict[str, Any] = {
        "e_ia": True,
        "categoria": "IA generativa",
        "topicos": ["LLM", "agentes"],
        "tipo_evento": "meetup",
        "modalidade": "presencial",
        "trecho_modalidade": "Presencial na Av. Paulista",
        "idioma": "pt",
        "tem_traducao": False,
        "trecho_idioma": "Palestras em português",
        "estado_preco": "gratuito_confirmado",
        "trecho_preco": "Encontro gratuito",
        "localizacao": "nao_se_aplica",
        "trecho_localizacao": "",
        "resumo": "Meetup sobre modelos de linguagem na Paulista.",
        "trecho_evidencia": "modelos de linguagem",
    }
    base.update(campos)
    return base


def campos_evento(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fonte": "luma",
        "id_fonte": "evt-Alfa1",
        "calendarios": ["cal-a"],
        "titulo": "Meetup de IA",
        "inicio": INICIO_PADRAO,
        "fim": INICIO_PADRAO + timedelta(hours=2),
        "dia_inteiro": False,
        "local_texto": LOCAL_TEXTO,
        "local_url": None,
        "link_inscricao": "https://luma.com/inscricao-secreta-xyz",
        "organizador": "Organização X",
        "endereco_oculto": False,
        "latitude": -23.561234,
        "longitude": -46.656789,
        "descricoes": {"cal-a": DESCRICAO},
        "status_fonte": "confirmado",
        "localizacao": "sp",
        "evidencia_localizacao": "geo",
        "hash_conteudo": "a" * 64,
        "estado": "ativo",
        "ausencias_consecutivas": 0,
        "rodadas_escalares_mantidos": 0,
        "primeira_vez_visto": AGORA,
        "ultima_vez_visto": AGORA,
        "atualizado_em": AGORA,
    }
    base.update(campos)
    return base


def evento_orm(**campos: Any) -> Evento:
    """Objeto ORM `Evento` em memória (sem sessão)."""
    return Evento(**campos_evento(**campos))
