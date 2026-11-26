"""Leituras para a decisão de destino (D20, D21): só SELECT, nada é gravado."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from radar.banco.modelos import (
    Classificacao,
    Enriquecimento,
    EnvioSemEvento,
    Evento,
    Execucao,
    Notificacao,
)

ESTADOS_ATIVOS = ("ativo", "alterado")


def eventos_para_decidir(sessao: Session) -> list[Evento]:
    """Eventos ativos ou alterados fora de `localizacao='fora'`, por início."""
    consulta = (
        select(Evento)
        .where(Evento.estado.in_(ESTADOS_ATIVOS), Evento.localizacao != "fora")
        .order_by(Evento.inicio, Evento.id)
    )
    return list(sessao.scalars(consulta))


def classificacoes_ok_atuais(
    sessao: Session,
    evento_ids: Sequence[int],
    *,
    versao_prompt: str,
    modelo: str,
    versao_schema: str,
) -> dict[int, dict[str, Any]]:
    """Resultado JSON bruto da classificação `ok` do hash atual e da chave atual."""
    if not evento_ids:
        return {}
    consulta = (
        select(Classificacao.evento_id, Classificacao.resultado)
        .join(Evento, Evento.id == Classificacao.evento_id)
        .where(
            Classificacao.evento_id.in_(evento_ids),
            Classificacao.situacao == "ok",
            Classificacao.hash_conteudo == Evento.hash_conteudo,
            Classificacao.versao_prompt == versao_prompt,
            Classificacao.modelo == modelo,
            Classificacao.versao_schema == versao_schema,
        )
    )
    return {evento_id: resultado for evento_id, resultado in sessao.execute(consulta)}


def enriquecimentos_atuais(sessao: Session, evento_ids: Sequence[int]) -> dict[int, Enriquecimento]:
    """Enriquecimento de preço do hash atual de cada evento."""
    if not evento_ids:
        return {}
    consulta = (
        select(Enriquecimento)
        .join(
            Evento,
            and_(
                Evento.id == Enriquecimento.evento_id,
                Evento.hash_conteudo == Enriquecimento.hash_conteudo,
            ),
        )
        .where(Enriquecimento.evento_id.in_(evento_ids))
    )
    return {linha.evento_id: linha for linha in sessao.scalars(consulta)}


def notificacoes_enviadas(sessao: Session, evento_ids: Sequence[int]) -> set[tuple[int, str]]:
    """(evento_id, tipo) já entregues (estado 'enviado')."""
    if not evento_ids:
        return set()
    consulta = select(Notificacao.evento_id, Notificacao.tipo).where(
        Notificacao.evento_id.in_(evento_ids), Notificacao.estado == "enviado"
    )
    return {(evento_id, tipo) for evento_id, tipo in sessao.execute(consulta)}


def eventos_por_id(sessao: Session, ids: Sequence[int]) -> dict[int, Evento]:
    if not ids:
        return {}
    return {e.id: e for e in sessao.scalars(select(Evento).where(Evento.id.in_(ids)))}


def calendarios_vazios_seguidos(
    sessao: Session, fonte: str, slugs: Sequence[str], n: int
) -> list[str]:
    """Slugs (dentre os pedidos) cujas `n` execuções mais recentes são todas 'vazio' e que
    tiveram um 'ok' antes delas (D20, F1-10c)."""
    achados: list[str] = []
    for slug in slugs:
        recentes = list(
            sessao.execute(
                select(Execucao.situacao, Execucao.executada_em)
                .where(Execucao.fonte == fonte, Execucao.calendario == slug)
                .order_by(Execucao.executada_em.desc(), Execucao.id.desc())
                .limit(n)
            )
        )
        if len(recentes) < n or any(situacao != "vazio" for situacao, _ in recentes):
            continue
        mais_antiga = recentes[-1][1]
        teve_ok = sessao.scalar(
            select(Execucao.id)
            .where(
                Execucao.fonte == fonte,
                Execucao.calendario == slug,
                Execucao.situacao == "ok",
                Execucao.executada_em < mais_antiga,
            )
            .limit(1)
        )
        if teve_ok is not None:
            achados.append(slug)
    return achados


def marco_resumo_existe(sessao: Session, chave: str) -> bool:
    """O resumo semanal desta chave (semana ISO em SP) já saiu."""
    return sessao.scalar(select(EnvioSemEvento.id).where(EnvioSemEvento.chave == chave)) is not None
