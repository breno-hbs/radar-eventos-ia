"""Testes do F1-10b — `executar` ponta a ponta (fixtures reais da Luma, LLM falso,
Telegram por MockTransport, Postgres de teste).

Assinaturas fixadas (radar.orquestracao.executar):
- `Etapa = Literal["coleta", "filtros", "banco", "classificacao", "enriquecimento",
  "decisao", "notificacao"]`;
- `ErroEtapa(etapa, tipo)` (Exception; atributos `etapa` e `tipo`; str/repr sem a mensagem
  original; levantado `from None`);
- `ResultadoExecucao` (rodada, resultados, descartados_geo, descartados_horizonte,
  rodada_banco, classificacao, enriquecimento, destinos: Counter, motivos: Counter,
  notificacao, pendentes_pedidas, pendentes_sem_classificacao, pendentes_terminadas,
  resumo_semanal_adiado) com a propriedade `codigo_saida` (3 se a notificação foi
  interrompida por autenticacao/proibido; senão 0);
- `executar(sessao, *, config, calendarios, http, llm, telegram, agora, rodada,
  proximo_resumo=None) -> ResultadoExecucao`; `linhas_resumo(resultado) -> list[str]`.
"""

from __future__ import annotations

import importlib
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from types import ModuleType
from typing import Any

import pytest
from banco.conftest import linhas
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from radar.config import Configuracao

from .falsos import AGORA, CHAT, LLMFalso, RedeLuma, RedeTelegram, calendarios

LIMITE = 5


@pytest.fixture
def ex() -> ModuleType:
    return importlib.import_module("radar.orquestracao.executar")


def _config(**campos: Any) -> Configuracao:
    campos.setdefault("limite_chamadas_llm_por_execucao", LIMITE)
    campos.setdefault("limite_paginas_enriquecimento_por_execucao", 10)
    return Configuracao(**campos)


def _executar(
    ex: ModuleType,
    sessao: Session,
    *,
    luma: RedeLuma | None = None,
    telegram: RedeTelegram | None = None,
    llm: LLMFalso | None = None,
    agora: datetime = AGORA,
    rodada: uuid.UUID | None = None,
    **config: Any,
) -> Any:
    luma = luma or RedeLuma()
    telegram = telegram or RedeTelegram()
    return ex.executar(
        sessao,
        config=_config(**config),
        calendarios=calendarios(),
        http=luma.cliente(),
        llm=llm or LLMFalso(),
        telegram=telegram.cliente(),
        agora=agora,
        rodada=rodada or uuid.uuid4(),
    )


def _contar(sessao: Session, sql: str, **parametros: Any) -> int:
    return linhas(sessao, sql, **parametros)[0]["n"]


# --- Ponta a ponta -------------------------------------------------------------------------


def test_ponta_a_ponta(ex: ModuleType, sessao: Session) -> None:
    luma, telegram, llm = RedeLuma(), RedeTelegram(), LLMFalso()
    rodada = uuid.uuid4()
    resultado = _executar(ex, sessao, luma=luma, telegram=telegram, llm=llm, rodada=rodada)

    assert resultado.rodada == rodada
    assert _contar(sessao, "SELECT count(*) AS n FROM execucoes WHERE rodada = :r", r=rodada) == 6
    assert _contar(sessao, "SELECT count(*) AS n FROM eventos") > 0
    assert _contar(sessao, "SELECT count(*) AS n FROM eventos WHERE localizacao = 'fora'") == 0
    classificadas = _contar(sessao, "SELECT count(*) AS n FROM classificacoes")
    assert 0 < classificadas <= LIMITE
    assert len(llm.chamadas) == classificadas

    # todo evt- classificado como IA nesta execução foi enriquecido nesta execução
    ia = linhas(
        sessao,
        "SELECT c.evento_id FROM classificacoes c JOIN eventos e ON e.id = c.evento_id "
        "WHERE c.situacao = 'ok' AND (c.resultado->>'e_ia')::boolean AND e.id_fonte LIKE 'evt-%'",
    )
    enriquecidos = {
        linha["evento_id"] for linha in linhas(sessao, "SELECT evento_id FROM enriquecimentos")
    }
    assert ia
    assert {linha["evento_id"] for linha in ia} <= enriquecidos

    assert isinstance(resultado.destinos, Counter)
    assert isinstance(resultado.motivos, Counter)
    esperadas = resultado.destinos["aviso"] + resultado.destinos["aviso_excepcional"]
    assert esperadas > 0
    assert len(telegram.corpos) == esperadas
    assert {c["chat_id"] for c in telegram.corpos} == {CHAT}
    estados = {linha["estado"] for linha in linhas(sessao, "SELECT estado FROM notificacoes")}
    assert estados == {"enviado"}
    assert resultado.codigo_saida == 0
    assert sum(resultado.destinos.values()) == sum(resultado.motivos.values())


def test_segunda_execucao_nao_repete(ex: ModuleType, sessao: Session) -> None:
    telegram, llm = RedeTelegram(), LLMFalso()
    _executar(ex, sessao, telegram=telegram, llm=llm, limite_chamadas_llm_por_execucao=50)
    mensagens, chamadas = len(telegram.corpos), len(llm.chamadas)
    assert mensagens > 0
    segunda = _executar(ex, sessao, telegram=telegram, llm=llm, limite_chamadas_llm_por_execucao=50)
    assert len(telegram.corpos) == mensagens
    assert len(llm.chamadas) == chamadas
    assert segunda.codigo_saida == 0


def test_feed_com_falha_nao_derruba_os_outros(ex: ModuleType, sessao: Session) -> None:
    rodada = uuid.uuid4()
    luma = RedeLuma(status_por_slug={"mug-sp": 503})
    resultado = _executar(ex, sessao, luma=luma, rodada=rodada)
    situacoes = {
        linha["calendario"]: linha["situacao"]
        for linha in linhas(
            sessao, "SELECT calendario, situacao FROM execucoes WHERE rodada = :r", r=rodada
        )
    }
    assert situacoes["mug-sp"] == "falha"
    assert {s for c, s in situacoes.items() if c != "mug-sp"} == {"ok"}
    assert _contar(sessao, "SELECT count(*) AS n FROM classificacoes") > 0
    assert resultado.codigo_saida == 0


def test_comunidade_nunca_e_gravada(ex: ModuleType, sessao: Session) -> None:
    llm = LLMFalso(e_ia=lambda _titulo: False)
    _executar(ex, sessao, llm=llm, limite_chamadas_llm_por_execucao=50)
    resultados = linhas(sessao, "SELECT resultado FROM classificacoes WHERE situacao = 'ok'")
    assert resultados
    for linha in resultados:
        assert linha["resultado"]["e_ia"] is False
        assert "e_ia_por_comunidade" not in linha["resultado"]


# --- Erros ---------------------------------------------------------------------------------


def test_erro_no_llm_vira_erro_de_etapa(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    llm = LLMFalso(erro=RuntimeError("dado-sensivel do evento"))
    with pytest.raises(ex.ErroEtapa) as info:
        _executar(ex, sessao, telegram=telegram, llm=llm)
    erro = info.value
    assert (erro.etapa, erro.tipo) == ("classificacao", "RuntimeError")
    for texto in (str(erro), repr(erro), str(erro.args)):
        assert "dado-sensivel" not in texto
    assert erro.__cause__ is None
    assert erro.__suppress_context__ is True
    assert telegram.corpos == []
    sessao.rollback()
    assert _contar(sessao, "SELECT count(*) AS n FROM eventos") > 0  # banco commitado
    assert _contar(sessao, "SELECT count(*) AS n FROM execucoes") == 6


def test_erro_etapa_construcao(ex: ModuleType) -> None:
    erro = ex.ErroEtapa("coleta", "KeyError")
    assert isinstance(erro, Exception)
    assert (erro.etapa, erro.tipo) == ("coleta", "KeyError")


@pytest.mark.parametrize(("status", "codigo"), [(401, 3), (403, 3), (429, 0), (500, 0)])
def test_codigo_de_saida_pelo_telegram(
    ex: ModuleType, sessao: Session, status: int, codigo: int
) -> None:
    telegram = RedeTelegram(status=status)
    resultado = _executar(ex, sessao, telegram=telegram)
    assert telegram.corpos  # tentou
    assert resultado.notificacao is not None
    assert resultado.codigo_saida == codigo


def test_agora_sem_fuso(ex: ModuleType, sessao: Session) -> None:
    luma, telegram, llm = RedeLuma(), RedeTelegram(), LLMFalso()
    with pytest.raises(ValueError):
        _executar(
            ex, sessao, luma=luma, telegram=telegram, llm=llm, agora=datetime(2026, 11, 25, 12)
        )
    assert luma.requisicoes == []
    assert telegram.corpos == []
    assert llm.chamadas == []


def test_pendente_anterior_e_reenviada(ex: ModuleType, sessao: Session) -> None:
    falhando = RedeTelegram(status=500)
    primeira = _executar(ex, sessao, telegram=falhando)
    pendentes = {
        (linha["evento_id"], linha["tipo"])
        for linha in linhas(
            sessao, "SELECT evento_id, tipo FROM notificacoes WHERE estado = 'enviando'"
        )
    }
    assert pendentes
    assert primeira.codigo_saida == 0

    ok = RedeTelegram()
    segunda = _executar(ex, sessao, telegram=ok)
    assert len(ok.corpos) >= len(pendentes)
    assert segunda.pendentes_pedidas >= len(pendentes)
    estados = {linha["estado"] for linha in linhas(sessao, "SELECT estado FROM notificacoes")}
    assert estados == {"enviado"}


def test_linhas_resumo(ex: ModuleType, sessao: Session) -> None:
    resultado = _executar(ex, sessao)
    saida = ex.linhas_resumo(resultado)
    assert saida
    titulos = [t for (t,) in sessao.execute(text("SELECT titulo FROM eventos")).all()]
    for linha in saida:
        assert "=" in linha
        chave, _, valor = linha.partition("=")
        assert chave.strip() and " " not in chave.strip()
        assert "http" not in linha
        assert "luma.com" not in linha
        for titulo in titulos:
            assert titulo not in linha
    assert any(str(resultado.rodada) in linha for linha in saida)


# --- Sessão ligada ao Engine (produção) ----------------------------------------------------


@pytest.fixture
def sessao_engine(engine: Engine) -> Iterator[Session]:
    """Sessão com bind = Engine (commits reais); limpa todas as tabelas no fim."""
    real = Session(bind=engine)
    try:
        yield real
    finally:
        real.rollback()
        real.close()
        with engine.begin() as conexao:
            conexao.execute(
                text(
                    "TRUNCATE notificacoes, enriquecimentos, classificacoes, execucoes, "
                    "eventos RESTART IDENTITY CASCADE"
                )
            )


def test_sessao_ligada_ao_engine(ex: ModuleType, sessao_engine: Session) -> None:
    telegram = RedeTelegram()
    resultado = _executar(ex, sessao_engine, telegram=telegram)
    assert resultado.codigo_saida == 0
    assert resultado.notificacao is not None
    assert telegram.corpos
    estados = {e for (e,) in sessao_engine.execute(text("SELECT estado FROM notificacoes")).all()}
    assert estados == {"enviado"}
