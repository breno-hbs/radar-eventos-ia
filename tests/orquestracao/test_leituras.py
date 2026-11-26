"""Testes do F1-10b — leituras para a decisão (radar.banco.leituras, só SELECT).

Assinaturas fixadas:
- `eventos_para_decidir(sessao) -> list[Evento]`: ativo/alterado, localizacao ≠ 'fora',
  ordem inicio, id;
- `classificacoes_ok_atuais(sessao, evento_ids, *, versao_prompt, modelo, versao_schema)
  -> dict[int, dict]`: resultado JSON bruto da classificação ok do hash atual e da chave;
- `enriquecimentos_atuais(sessao, evento_ids) -> dict[int, Enriquecimento]` (hash atual);
- `notificacoes_enviadas(sessao, evento_ids) -> set[tuple[int, str]]` (estado 'enviado');
- `eventos_por_id(sessao, ids) -> dict[int, Evento]`.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest
from classificacao.fabricas import saida_valida
from sqlalchemy.orm import Session

from .dados_banco import (
    chave_atual,
    classificar,
    classificar_saida,
    enriquecer,
    inserir_evento,
    notificacao,
)


@pytest.fixture
def leituras() -> ModuleType:
    return importlib.import_module("radar.banco.leituras")


def _ids(eventos: list[Any]) -> list[int]:
    return [e.id for e in eventos]


def test_eventos_para_decidir(leituras: ModuleType, sessao: Session) -> None:
    tarde = inserir_evento(sessao, dias=5)
    cedo = inserir_evento(sessao, dias=1)
    alterado = inserir_evento(sessao, dias=3, estado="alterado")
    for estado in ("cancelado", "encerrado", "nao_encontrado"):
        inserir_evento(sessao, dias=2, estado=estado)
    inserir_evento(sessao, dias=2, localizacao="fora", evidencia_localizacao="texto")
    indefinida = inserir_evento(sessao, dias=4, localizacao="indefinida")
    assert _ids(leituras.eventos_para_decidir(sessao)) == [
        cedo.id,
        alterado.id,
        indefinida.id,
        tarde.id,
    ]


def test_classificacoes_ok_atuais(leituras: ModuleType, sessao: Session) -> None:
    ok = inserir_evento(sessao)
    resultado = saida_valida(e_ia=False)
    classificar_saida(sessao, ok, resultado)
    so_erro = inserir_evento(sessao)
    classificar(sessao, so_erro, situacao="erro")
    hash_antigo = inserir_evento(sessao)
    classificar(sessao, hash_antigo, hash_conteudo="f" * 64)
    outra_chave = inserir_evento(sessao)
    classificar(sessao, outra_chave, versao_prompt="classificador_v0")
    nao_pedido = inserir_evento(sessao)
    classificar(sessao, nao_pedido)

    ids = [ok.id, so_erro.id, hash_antigo.id, outra_chave.id]
    achadas = leituras.classificacoes_ok_atuais(sessao, ids, **chave_atual())
    assert achadas == {ok.id: resultado}


def test_classificacao_de_outra_versao_de_schema_nao_conta(
    leituras: ModuleType, sessao: Session
) -> None:
    evento = inserir_evento(sessao)
    classificar(sessao, evento, versao_schema="0")
    assert leituras.classificacoes_ok_atuais(sessao, [evento.id], **chave_atual()) == {}


def test_enriquecimentos_atuais(leituras: ModuleType, sessao: Session) -> None:
    atual = inserir_evento(sessao)
    enriquecer(sessao, atual)
    antigo = inserir_evento(sessao)
    enriquecer(sessao, antigo, hash_conteudo="e" * 64)
    sem = inserir_evento(sessao)
    achados = leituras.enriquecimentos_atuais(sessao, [atual.id, antigo.id, sem.id])
    assert set(achados) == {atual.id}
    from radar.banco.modelos import Enriquecimento

    assert isinstance(achados[atual.id], Enriquecimento)
    assert achados[atual.id].hash_conteudo == atual.hash_conteudo


def test_notificacoes_enviadas(leituras: ModuleType, sessao: Session) -> None:
    um = inserir_evento(sessao)
    dois = inserir_evento(sessao)
    fora_do_pedido = inserir_evento(sessao)
    notificacao(sessao, um, "aviso", "enviado")
    notificacao(sessao, um, "resumo_semanal", "enviando")
    notificacao(sessao, dois, "aviso_excepcional", "enviado")
    notificacao(sessao, fora_do_pedido, "aviso", "enviado")
    assert leituras.notificacoes_enviadas(sessao, [um.id, dois.id]) == {
        (um.id, "aviso"),
        (dois.id, "aviso_excepcional"),
    }


def test_eventos_por_id(leituras: ModuleType, sessao: Session) -> None:
    um = inserir_evento(sessao)
    dois = inserir_evento(sessao, estado="cancelado")
    achados = leituras.eventos_por_id(sessao, [um.id, dois.id, 999_999_999])
    assert set(achados) == {um.id, dois.id}
    assert achados[dois.id].estado == "cancelado"


def test_leituras_nao_escrevem(leituras: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    sessao.commit()
    leituras.eventos_para_decidir(sessao)
    leituras.classificacoes_ok_atuais(sessao, [evento.id], **chave_atual())
    leituras.enriquecimentos_atuais(sessao, [evento.id])
    leituras.notificacoes_enviadas(sessao, [evento.id])
    leituras.eventos_por_id(sessao, [evento.id])
    assert not (sessao.new or sessao.dirty or sessao.deleted)
