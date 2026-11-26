"""Testes do F1-10c — alertas na execução (ponta a ponta; LLM falso; MockTransport).

Assinaturas fixadas: `Etapa` ganha "alertas"; `executar(..., ultimo_commit_em=None)`;
`ResultadoExecucao` ganha `alertas: list[Alerta]` e `alertas_envio: ResumoAlertas | None`;
`codigo_saida` = 3 também se `alertas_envio.interrompido_por` em {autenticacao, proibido}.
Alertas saem depois dos eventos; notificação interrompida por autenticacao/proibido/
rate_limit → nenhum alerta enviado (todos em nao_enviados); etapa com ErroEtapa → nenhum
alerta. `linhas_resumo` ganha resumo_semanal_agora/pedidos/marco, alertas_detectados,
alerta_<tipo>=N e alertas_enviados/ja_enviados/falhas/nao_enviados/interrompidos.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any

import pytest
from banco.conftest import linhas
from sqlalchemy import text
from sqlalchemy.orm import Session

from radar.config import Configuracao

from .dados_banco import gravar_marco
from .falsos import AGORA, LLMFalso, RedeLuma, RedeTelegram, calendarios

PREFIXO = "Alerta do radar"


@pytest.fixture
def ex() -> ModuleType:
    return importlib.import_module("radar.orquestracao.executar")


@pytest.fixture
def cliente_llm() -> ModuleType:
    return importlib.import_module("radar.classificacao.cliente")


@pytest.fixture(autouse=True)
def marco_da_semana(sessao: Session) -> None:
    """Resumo semanal da semana de AGORA já saiu: estes testes tratam só de alertas."""
    gravar_marco(sessao, AGORA)
    sessao.commit()


def _executar(
    ex: ModuleType,
    sessao: Session,
    *,
    luma: RedeLuma | None = None,
    telegram: RedeTelegram | None = None,
    llm: LLMFalso | None = None,
    agora: datetime = AGORA,
    ultimo_commit_em: datetime | None = None,
    **config: Any,
) -> Any:
    config.setdefault("limite_chamadas_llm_por_execucao", 5)
    config.setdefault("limite_paginas_enriquecimento_por_execucao", 5)
    return ex.executar(
        sessao,
        config=Configuracao(**config),
        calendarios=calendarios(),
        http=(luma or RedeLuma()).cliente(),
        llm=llm or LLMFalso(),
        telegram=(telegram or RedeTelegram()).cliente(),
        agora=agora,
        rodada=uuid.uuid4(),
        ultimo_commit_em=ultimo_commit_em,
    )


def _alertas(telegram: RedeTelegram) -> list[str]:
    return [c["text"] for c in telegram.corpos if c["text"].startswith(PREFIXO)]


def test_alertas_saem_depois_dos_eventos(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    resultado = _executar(
        ex, sessao, luma=RedeLuma(status_por_slug={"mug-sp": 503}), telegram=telegram
    )
    textos = [c["text"] for c in telegram.corpos]
    alertas = _alertas(telegram)
    assert len(alertas) == 1
    assert "mug-sp" in alertas[0]
    assert "503" in alertas[0]
    assert len(textos) > 1
    assert textos[-1] == alertas[0]
    assert all(not t.startswith(PREFIXO) for t in textos[:-1])
    assert [a.tipo for a in resultado.alertas] == ["falha_extracao"]
    assert resultado.alertas_envio is not None
    assert len(resultado.alertas_envio.enviados) == 1
    assert resultado.codigo_saida == 0
    [linha] = linhas(
        sessao, "SELECT tipo, estado FROM envios_sem_evento WHERE tipo = 'falha_extracao'"
    )
    assert linha["estado"] == "enviado"


def test_segunda_execucao_nao_repete_alerta(ex: ModuleType, sessao: Session) -> None:
    luma = RedeLuma(status_por_slug={"mug-sp": 503})
    _executar(ex, sessao, luma=luma)
    telegram = RedeTelegram()
    resultado = _executar(
        ex, sessao, luma=luma, telegram=telegram, agora=AGORA + timedelta(hours=7)
    )
    assert _alertas(telegram) == []
    assert len(resultado.alertas_envio.ja_enviados) == 1


def test_robots_da_luma_com_indisponivel(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    resultado = _executar(ex, sessao, luma=RedeLuma(robots_luma_com=500), telegram=telegram)
    assert "robots_bloqueado" in [a.tipo for a in resultado.alertas]
    assert len(_alertas(telegram)) == 1


def test_llm_429_alerta_classificador(
    ex: ModuleType, cliente_llm: ModuleType, sessao: Session
) -> None:
    telegram = RedeTelegram()
    llm = LLMFalso(erro=cliente_llm.ErroAPI(categoria="rate_limit", status_http=429))
    resultado = _executar(ex, sessao, llm=llm, telegram=telegram)
    [alerta] = [a for a in resultado.alertas if a.tipo == "classificador_interrompido"]
    assert alerta.codigo == "rate_limit"
    assert any("rate_limit" in t for t in _alertas(telegram))


def test_notificacao_401_nao_envia_alerta(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram(status=401)
    resultado = _executar(
        ex, sessao, luma=RedeLuma(status_por_slug={"mug-sp": 503}), telegram=telegram
    )
    assert resultado.notificacao is not None
    assert resultado.notificacao.interrompido_por == "autenticacao"
    assert len(telegram.corpos) == 1  # só o primeiro aviso
    assert _alertas(telegram) == []
    assert resultado.alertas
    assert len(resultado.alertas_envio.nao_enviados) == len(resultado.alertas)
    assert resultado.codigo_saida == 3
    assert linhas(sessao, "SELECT * FROM envios_sem_evento WHERE tipo <> 'resumo_semanal'") == []


def test_so_alerta_com_401_sai_com_3(
    ex: ModuleType, cliente_llm: ModuleType, sessao: Session
) -> None:
    # LLM sempre com erro de servidor: nenhum evento classificado, nenhum aviso
    telegram = RedeTelegram(status=401)
    llm = LLMFalso(erro=cliente_llm.ErroAPI(categoria="servidor", status_http=500))
    resultado = _executar(
        ex, sessao, luma=RedeLuma(status_por_slug={"mug-sp": 503}), llm=llm, telegram=telegram
    )
    assert resultado.notificacao is None or not resultado.notificacao.enviados
    assert len(telegram.corpos) == 1
    assert telegram.corpos[0]["text"].startswith(PREFIXO)
    assert resultado.alertas_envio.interrompido_por == "autenticacao"
    assert resultado.codigo_saida == 3


def test_etapa_que_falha_nao_envia_alerta(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    with pytest.raises(ex.ErroEtapa):
        _executar(
            ex,
            sessao,
            luma=RedeLuma(status_por_slug={"mug-sp": 503}),
            llm=LLMFalso(erro=RuntimeError("dado-sensivel")),
            telegram=telegram,
        )
    assert telegram.corpos == []
    sessao.rollback()
    assert linhas(sessao, "SELECT * FROM envios_sem_evento WHERE tipo <> 'resumo_semanal'") == []


def test_inatividade(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    resultado = _executar(
        ex, sessao, telegram=telegram, ultimo_commit_em=AGORA - timedelta(days=55)
    )
    assert "inatividade" in [a.tipo for a in resultado.alertas]
    [texto] = [t for t in _alertas(telegram) if "Reativação" in t]
    assert "60 dias" in texto


def test_sem_inatividade_recente(ex: ModuleType, sessao: Session) -> None:
    resultado = _executar(ex, sessao, ultimo_commit_em=AGORA - timedelta(days=10))
    assert "inatividade" not in [a.tipo for a in resultado.alertas]


def test_fonte_vazia_seguida(ex: ModuleType, sessao: Session) -> None:
    from radar.banco.modelos import Execucao

    for indice, situacao in enumerate(["ok"] + ["vazio"] * 5):
        sessao.add(
            Execucao(
                rodada=uuid.uuid4(),
                fonte="luma",
                calendario="codex-sp",
                executada_em=AGORA - timedelta(days=6 - indice),
                situacao=situacao,
                quantidade_eventos=1 if situacao == "ok" else 0,
                quantidade_invalidos=0,
                motivo_falha=None,
                status_http=None,
                detalhe=None,
            )
        )
    sessao.commit()

    class RedeCodexVazio(RedeLuma):
        def __call__(self, requisicao: Any) -> Any:
            import httpx

            if requisicao.url.params.get("id") == "cal-sAAnGigXnTQWpw6":
                self.requisicoes.append(requisicao)
                return httpx.Response(200, text="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")
            return super().__call__(requisicao)

    resultado = _executar(ex, sessao, luma=RedeCodexVazio())
    assert ("fonte_vazia", "codex-sp") in [(a.tipo, a.alvo) for a in resultado.alertas]


def test_linhas_resumo_com_alertas(ex: ModuleType, sessao: Session) -> None:
    resultado = _executar(ex, sessao, luma=RedeLuma(status_por_slug={"mug-sp": 503}))
    saida = ex.linhas_resumo(resultado)
    chaves = {linha.partition("=")[0] for linha in saida}
    for chave in (
        "resumo_semanal_agora",
        "resumo_semanal_pedidos",
        "resumo_semanal_marco",
        "alertas_detectados",
        "alerta_falha_extracao",
        "alertas_enviados",
        "alertas_ja_enviados",
        "alertas_falhas",
        "alertas_nao_enviados",
        "alertas_interrompidos",
    ):
        assert chave in chaves, chave
    assert "alertas_detectados=1" in saida
    titulos = [t for (t,) in sessao.execute(text("SELECT titulo FROM eventos")).all()]
    for linha in saida:
        assert "http://" not in linha
        assert "https://" not in linha
        for titulo in titulos:
            assert titulo not in linha
