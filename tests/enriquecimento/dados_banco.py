"""Eventos, classificações e enriquecimentos gravados direto pelo ORM (Postgres de teste)."""

from __future__ import annotations

import importlib
import itertools
import uuid
from datetime import timedelta
from typing import Any

from classificacao.fabricas import AGORA, INICIO_PADRAO, campos_evento, saida_valida
from sqlalchemy.orm import Session

_contador = itertools.count(1)


def chave_atual() -> dict[str, str]:
    prompt = importlib.import_module("radar.classificacao.prompt")
    modelos = importlib.import_module("radar.classificacao.modelos")
    return {
        "versao_prompt": prompt.VERSAO_PROMPT,
        "modelo": modelos.MODELO,
        "versao_schema": modelos.VERSAO_SCHEMA,
    }


def inserir_evento(sessao: Session, dias: int = 0, **campos: Any) -> Any:
    from radar.banco.modelos import Evento

    numero = next(_contador)
    campos.setdefault("id_fonte", f"evt-Preco{numero}")
    campos.setdefault("link_inscricao", f"https://luma.com/evento-{numero}")
    campos.setdefault("inicio", INICIO_PADRAO + timedelta(days=dias))
    campos.setdefault("fim", None)
    linha = Evento(**campos_evento(**campos))
    sessao.add(linha)
    sessao.flush()
    return linha


def classificar(
    sessao: Session, evento: Any, e_ia: bool = True, situacao: str = "ok", **chave: str
) -> None:
    from radar.banco.modelos import Classificacao

    valores: dict[str, Any] = {
        "evento_id": evento.id,
        "rodada": uuid.uuid4(),
        "criada_em": AGORA,
        "hash_conteudo": evento.hash_conteudo,
        **chave_atual(),
        "situacao": situacao,
        "resultado": saida_valida(e_ia=e_ia) if situacao == "ok" else None,
        "motivo_erro": None if situacao == "ok" else "saida_json_invalido",
    }
    valores.update(chave)
    sessao.add(Classificacao(**valores))
    sessao.flush()


def evento_ia(sessao: Session, dias: int = 0, **campos: Any) -> Any:
    evento = inserir_evento(sessao, dias, **campos)
    classificar(sessao, evento)
    return evento


def enriquecer(sessao: Session, evento: Any, hash_conteudo: str | None = None) -> None:
    from radar.banco.modelos import Enriquecimento

    sessao.add(
        Enriquecimento(
            evento_id=evento.id,
            hash_conteudo=hash_conteudo or evento.hash_conteudo,
            rodada=uuid.uuid4(),
            criado_em=AGORA,
            estado_preco="preco_desconhecido",
            motivo="rede",
        )
    )
    sessao.flush()
