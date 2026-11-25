"""Testes do F1-08 — fila de enriquecimento (Postgres de teste).

Assinatura fixada (radar.banco.repositorio):
`eventos_para_enriquecer(sessao, *, versao_prompt, modelo, versao_schema,
slugs_comunidade_ia: frozenset[str], limite: int) -> list[Evento]`: estado ativo/alterado,
localizacao ≠ 'fora', id_fonte 'evt-%', link_inscricao não nulo; classificação ok da chave
atual E (resultado e_ia true OU calendários ∩ slugs_comunidade_ia, só se não vazio) — D15:
comunidade só vale com classificação ok; sem
Enriquecimento para (evento.id, hash atual); ordem inicio, id; no máximo `limite`.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy.orm import Session

from .dados_banco import chave_atual, classificar, enriquecer, evento_ia, inserir_evento

COMUNIDADES = frozenset({"ai-collective", "codex-sp"})


@pytest.fixture
def repo() -> ModuleType:
    return importlib.import_module("radar.banco.repositorio")


def _fila(
    repo: ModuleType,
    sessao: Session,
    limite: int = 50,
    slugs: frozenset[str] = COMUNIDADES,
    **chave: str,
) -> list[Any]:
    return repo.eventos_para_enriquecer(
        sessao, **{**chave_atual(), **chave}, slugs_comunidade_ia=slugs, limite=limite
    )


def _ids(eventos: list[Any]) -> list[int]:
    return [e.id for e in eventos]


def test_evento_ia_entra(repo: ModuleType, sessao: Session) -> None:
    from radar.banco.modelos import Evento

    evento = evento_ia(sessao)
    fila = _fila(repo, sessao)
    assert _ids(fila) == [evento.id]
    assert isinstance(fila[0], Evento)


def test_evento_nao_ia_nao_entra(repo: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar(sessao, evento, e_ia=False)
    assert _fila(repo, sessao) == []


def test_comunidade_sem_classificacao_nao_entra(repo: ModuleType, sessao: Session) -> None:
    inserir_evento(sessao, calendarios=["mug-sp", "codex-sp"])
    assert _fila(repo, sessao) == []


def test_comunidade_com_so_classificacao_de_erro_nao_entra(
    repo: ModuleType, sessao: Session
) -> None:
    evento = inserir_evento(sessao, calendarios=["codex-sp"])
    classificar(sessao, evento, situacao="erro")
    assert _fila(repo, sessao) == []


def test_comunidade_entra_mesmo_com_e_ia_falso(repo: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, calendarios=["ai-collective"])
    classificar(sessao, evento, e_ia=False)
    assert _ids(_fila(repo, sessao)) == [evento.id]


def test_sem_slugs_comunidade_nao_conta(repo: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, calendarios=["ai-collective"])
    classificar(sessao, evento, e_ia=False)
    assert _fila(repo, sessao, slugs=frozenset()) == []


def test_calendario_fora_das_comunidades_nao_entra(repo: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, calendarios=["mug-sp"])
    classificar(sessao, evento, e_ia=False)
    assert _fila(repo, sessao) == []


def test_classificacao_com_erro_nao_conta(repo: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar(sessao, evento, situacao="erro")
    assert _fila(repo, sessao) == []


@pytest.mark.parametrize(
    "chave_antiga",
    [
        {"versao_prompt": "classificador_v0"},
        {"modelo": "modelo-antigo"},
        {"versao_schema": "0"},
        {"hash_conteudo": "f" * 64},
    ],
)
def test_classificacao_de_outra_chave_nao_conta(
    repo: ModuleType, sessao: Session, chave_antiga: dict[str, str]
) -> None:
    evento = inserir_evento(sessao)
    classificar(sessao, evento, **chave_antiga)
    assert _fila(repo, sessao) == []


@pytest.mark.parametrize("estado", ["cancelado", "encerrado", "nao_encontrado"])
def test_estados_fora_da_fila(repo: ModuleType, sessao: Session, estado: str) -> None:
    evento_ia(sessao, estado=estado)
    assert _fila(repo, sessao) == []


def test_alterado_entra(repo: ModuleType, sessao: Session) -> None:
    evento = evento_ia(sessao, estado="alterado")
    assert _ids(_fila(repo, sessao)) == [evento.id]


def test_fora_de_sp_nao_entra(repo: ModuleType, sessao: Session) -> None:
    evento_ia(sessao, localizacao="fora", evidencia_localizacao="texto")
    assert _fila(repo, sessao) == []


def test_id_que_nao_e_evt_nao_entra(repo: ModuleType, sessao: Session) -> None:
    evento_ia(sessao, id_fonte="calev-AbC123")
    assert _fila(repo, sessao) == []


def test_sem_link_nao_entra(repo: ModuleType, sessao: Session) -> None:
    evento_ia(sessao, link_inscricao=None)
    assert _fila(repo, sessao) == []


def test_ja_enriquecido_para_o_hash_atual_nao_entra(repo: ModuleType, sessao: Session) -> None:
    evento = evento_ia(sessao)
    enriquecer(sessao, evento)
    assert _fila(repo, sessao) == []


def test_enriquecido_em_hash_antigo_volta(repo: ModuleType, sessao: Session) -> None:
    evento = evento_ia(sessao)
    enriquecer(sessao, evento, hash_conteudo="e" * 64)
    assert _ids(_fila(repo, sessao)) == [evento.id]


def test_ordem_por_inicio_e_limite(repo: ModuleType, sessao: Session) -> None:
    tarde = evento_ia(sessao, dias=3)
    cedo = evento_ia(sessao, dias=0)
    meio = evento_ia(sessao, dias=1)
    assert _ids(_fila(repo, sessao)) == [cedo.id, meio.id, tarde.id]
    assert _ids(_fila(repo, sessao, limite=2)) == [cedo.id, meio.id]
