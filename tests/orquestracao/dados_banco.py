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
