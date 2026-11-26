"""Gravação direta pelo ORM para os testes do F1-10b (Postgres de teste)."""

from __future__ import annotations

from typing import Any

from enriquecimento.dados_banco import (
    chave_atual,
    classificar,
    enriquecer,
    evento_ia,
    inserir_evento,
)
from sqlalchemy.orm import Session

__all__ = [
    "chave_semana",
    "gravar_marco",
    "tabela_existe",
    "chave_atual",
    "classificar",
    "classificar_saida",
    "enriquecer",
    "erros_de_saida",
    "evento_ia",
    "inserir_evento",
    "notificacao",
]


def classificar_saida(sessao: Session, evento: Any, resultado: dict[str, Any]) -> None:
    import uuid

    from classificacao.fabricas import AGORA

    from radar.banco.modelos import Classificacao

    sessao.add(
        Classificacao(
            evento_id=evento.id,
            rodada=uuid.uuid4(),
            criada_em=AGORA,
            hash_conteudo=evento.hash_conteudo,
            **chave_atual(),
            situacao="ok",
            resultado=resultado,
        )
    )
    sessao.flush()


def erros_de_saida(sessao: Session, evento: Any, quantidade: int = 3) -> None:
    for _ in range(quantidade):
        classificar(sessao, evento, situacao="erro")


def notificacao(sessao: Session, evento: Any, tipo: str, estado: str) -> None:
    from classificacao.fabricas import AGORA

    from radar.banco.modelos import Notificacao

    sessao.add(
        Notificacao(
            evento_id=evento.id,
            tipo=tipo,
            estado=estado,
            criada_em=AGORA,
            enviada_em=AGORA if estado == "enviado" else None,
        )
    )
    sessao.flush()


def chave_semana(agora: Any) -> str:
    """`resumo_semanal:<ano ISO>-W<semana ISO com 2 dígitos>` no fuso de São Paulo."""
    from zoneinfo import ZoneInfo

    ano, semana, _ = agora.astimezone(ZoneInfo("America/Sao_Paulo")).isocalendar()
    return f"resumo_semanal:{ano}-W{semana:02d}"


def tabela_existe(sessao: Session, nome: str) -> bool:
    from sqlalchemy import text

    return bool(sessao.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": nome}).scalar())


def gravar_marco(sessao: Session, agora: Any) -> None:
    """Marco do resumo semanal da semana de `agora`, gravado como enviado (F1-10c).

    Antes da migração 0002 a tabela não existe: não faz nada."""
    from sqlalchemy import text

    if not tabela_existe(sessao, "envios_sem_evento"):
        return
    sessao.execute(
        text(
            "INSERT INTO envios_sem_evento (chave, tipo, estado, criado_em, enviado_em) "
            "VALUES (:c, 'resumo_semanal', 'enviado', :a, :a)"
        ),
        {"c": chave_semana(agora), "a": agora},
    )
    sessao.flush()
