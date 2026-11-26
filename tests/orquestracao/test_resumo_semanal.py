"""Testes do F1-10c — resumo semanal (marco por semana ISO em São Paulo).

Assinaturas fixadas (radar.orquestracao.executar):
- `semana_iso_sp(agora) -> tuple[int, int]` (ano ISO, semana ISO) no fuso de SP;
- `chave_marco_resumo(agora) -> str` = "resumo_semanal:<ano>-W<semana com 2 dígitos>";
- `calcular_proximo_resumo(agora, *, resumo_agora: bool) -> datetime` (não existe mais
  `proximo_resumo` como função): resumo_agora → `agora`; senão a terça 00:00 de SP seguinte
  à próxima segunda 00:00 estritamente depois de `agora` (cobre a segunda inteira); sem fuso
  → ValueError. `proximo_resumo_provisorio` (F1-10b) continua igual;
- `montar_pedidos(..., resumo_agora: bool = False)`: destino resumo_semanal com
  resumo_agora, sem aviso/aviso_excepcional enviado e sem resumo_semanal enviado → pedido
  (saida ou None na falha) e `resumo_semanal_pedidos`; senão `resumo_semanal_adiado`.
  `resumo_semanal_pedidos` conta só pedidos NOVOS de resumo (das decisões); o reenvio de
  uma parte que ficou 'enviando' entra em `pendentes_pedidas` e não é contado de novo;
  pedido "aviso" (novo ou pendente) de evento com qualquer linha resumo_semanal
  ('enviado' ou 'enviando') ou com pedido de resumo já montado nesta execução é pulado e
  contado em `ResultadoExecucao.ja_em_resumo` (padrão 0; também em linhas_resumo);
- `executar`: `resumo_agora = not marco_resumo_existe(chave da semana atual)`; com
  resumo_agora e notificação sem interrupção grava o marco (também com resumo vazio).
  `ResultadoExecucao` ganha resumo_agora, resumo_semanal_pedidos, marco_gravado.
- `decidir_destino` com `proximo_resumo < agora` → ValueError.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from banco.conftest import linhas
from classificacao.fabricas import saida_valida
from sqlalchemy.orm import Session

from radar.config import Configuracao

from .dados_banco import (
    chave_semana,
    classificar_saida,
    erros_de_saida,
    gravar_marco,
    inserir_evento,
    notificacao,
)
from .falsos import LLMFalso, RedeLuma, RedeTelegram, calendarios

SP = ZoneInfo("America/Sao_Paulo")
UTC = ZoneInfo("UTC")
SEGUNDA = datetime(2026, 11, 30, 7, 40, tzinfo=SP)
TERCA = datetime(2026, 12, 1, 7, 40, tzinfo=SP)
QUARTA = datetime(2026, 12, 2, 12, 40, tzinfo=SP)
ESTRANGEIRO = saida_valida(idioma="en", trecho_idioma="in English")


@pytest.fixture
def ex() -> ModuleType:
    return importlib.import_module("radar.orquestracao.executar")


# --- Semana ISO e próximo resumo -----------------------------------------------------------


@pytest.mark.parametrize(
    ("agora", "semana", "chave"),
    [
        (datetime(2026, 11, 29, 23, 0, tzinfo=SP), (2026, 48), "resumo_semanal:2026-W48"),
        (datetime(2026, 11, 30, 1, 30, tzinfo=UTC), (2026, 48), "resumo_semanal:2026-W48"),
        (datetime(2026, 11, 30, 0, 0, tzinfo=SP), (2026, 49), "resumo_semanal:2026-W49"),
        (datetime(2026, 12, 31, 12, 0, tzinfo=SP), (2026, 53), "resumo_semanal:2026-W53"),
        (datetime(2027, 1, 3, 23, 0, tzinfo=SP), (2026, 53), "resumo_semanal:2026-W53"),
        (datetime(2027, 1, 4, 0, 0, tzinfo=SP), (2027, 1), "resumo_semanal:2027-W01"),
        (datetime(2025, 12, 29, 9, 0, tzinfo=SP), (2026, 1), "resumo_semanal:2026-W01"),
    ],
)
def test_semana_iso_em_sp(
    ex: ModuleType, agora: datetime, semana: tuple[int, int], chave: str
) -> None:
    assert ex.semana_iso_sp(agora) == semana
    assert ex.chave_marco_resumo(agora) == chave
    assert chave == chave_semana(agora)


def test_semana_iso_sem_fuso(ex: ModuleType) -> None:
    with pytest.raises(ValueError):
        ex.semana_iso_sp(datetime(2026, 11, 25, 12))


@pytest.mark.parametrize(
    ("agora", "esperado"),
    [
        (QUARTA, datetime(2026, 12, 8, tzinfo=SP)),
        (datetime(2026, 11, 29, 23, 0, tzinfo=SP), datetime(2026, 12, 1, tzinfo=SP)),
        (SEGUNDA, datetime(2026, 12, 8, tzinfo=SP)),
        (SEGUNDA.replace(hour=15), datetime(2026, 12, 8, tzinfo=SP)),
        (datetime(2026, 11, 30, 0, 0, tzinfo=SP), datetime(2026, 12, 8, tzinfo=SP)),
        (datetime(2026, 11, 30, 1, 30, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=SP)),
    ],
    ids=["quarta", "domingo-23h", "segunda-07h40", "segunda-15h", "segunda-00h", "utc-domingo"],
)
def test_calcular_proximo_resumo_sem_resumo_agora(
    ex: ModuleType, agora: datetime, esperado: datetime
) -> None:
    resultado = ex.calcular_proximo_resumo(agora, resumo_agora=False)
    assert resultado == esperado
    local = resultado.astimezone(SP)
    assert (local.weekday(), local.hour, local.minute) == (1, 0, 0)


def test_calcular_proximo_resumo_com_resumo_agora(ex: ModuleType) -> None:
    assert ex.calcular_proximo_resumo(QUARTA, resumo_agora=True) == QUARTA
    with pytest.raises(ValueError):
        ex.calcular_proximo_resumo(datetime(2026, 11, 25, 12), resumo_agora=True)
    with pytest.raises(ValueError):
        ex.calcular_proximo_resumo(datetime(2026, 11, 25, 12), resumo_agora=False)


def test_sem_apelido_proximo_resumo(ex: ModuleType) -> None:
    assert not hasattr(ex, "proximo_resumo")
    assert hasattr(ex, "proximo_resumo_provisorio")


def test_decidir_destino_com_proximo_no_passado() -> None:
    destino = importlib.import_module("radar.orquestracao.destino")
    from classificacao.fabricas import evento_orm

    with pytest.raises(ValueError):
        destino.decidir_destino(
            evento_orm(),
            None,
            em_falha_classificacao=False,
            slugs_comunidade_ia=frozenset(),
            agora=QUARTA,
            proximo_resumo=QUARTA - timedelta(seconds=1),
        )


# --- montar_pedidos com resumo_agora -------------------------------------------------------


def _montar(ex: ModuleType, sessao: Session, agora: datetime, resumo_agora: bool) -> tuple:
    resultado = ex.ResultadoExecucao(rodada=uuid.uuid4())
    pedidos = ex.montar_pedidos(
        sessao,
        slugs_comunidade=frozenset(),
        agora=agora,
        proximo_resumo=ex.calcular_proximo_resumo(agora, resumo_agora=resumo_agora),
        resultado=resultado,
        resumo_agora=resumo_agora,
    )
    return pedidos, resultado


def _tipos(pedidos: list[Any]) -> list[tuple[int, str]]:
    return [(p.evento.id, p.tipo) for p in pedidos]


def test_resumo_agora_gera_pedido(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=SEGUNDA + timedelta(days=3))
    classificar_saida(sessao, evento, ESTRANGEIRO)
    pedidos, resultado = _montar(ex, sessao, SEGUNDA, True)
    assert _tipos(pedidos) == [(evento.id, "resumo_semanal")]
    assert pedidos[0].saida is not None
    assert resultado.resumo_semanal_pedidos == 1
    assert resultado.resumo_semanal_adiado == 0


def test_sem_resumo_agora_adia(ex: ModuleType, sessao: Session) -> None:
    # depois da terça 08/12 00:00 (próximo resumo visto de uma quarta)
    evento = inserir_evento(sessao, inicio=QUARTA + timedelta(days=7))
    classificar_saida(sessao, evento, ESTRANGEIRO)
    pedidos, resultado = _montar(ex, sessao, QUARTA, False)
    assert pedidos == []
    assert resultado.resumo_semanal_adiado == 1
    assert resultado.resumo_semanal_pedidos == 0


def test_falha_futura_entra_no_resumo_sem_saida(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=SEGUNDA + timedelta(days=3))
    erros_de_saida(sessao, evento, 3)
    pedidos, _ = _montar(ex, sessao, SEGUNDA, True)
    assert _tipos(pedidos) == [(evento.id, "resumo_semanal")]
    assert pedidos[0].saida is None


def test_falha_em_andamento_vira_excepcional(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(
        sessao, inicio=SEGUNDA - timedelta(hours=1), fim=SEGUNDA + timedelta(hours=2)
    )
    erros_de_saida(sessao, evento, 3)
    pedidos, _ = _montar(ex, sessao, SEGUNDA, True)
    assert _tipos(pedidos) == [(evento.id, "aviso_excepcional")]


@pytest.mark.parametrize("tipo", ["aviso", "aviso_excepcional"])
def test_ja_avisado_fica_fora_do_resumo(ex: ModuleType, sessao: Session, tipo: str) -> None:
    evento = inserir_evento(sessao, inicio=SEGUNDA + timedelta(days=3))
    classificar_saida(sessao, evento, ESTRANGEIRO)
    notificacao(sessao, evento, tipo, "enviado")
    pedidos, resultado = _montar(ex, sessao, SEGUNDA, True)
    assert pedidos == []
    assert resultado.resumo_semanal_pedidos == 0


def test_ja_incluido_em_semana_anterior_nao_volta(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=SEGUNDA + timedelta(days=3))
    classificar_saida(sessao, evento, ESTRANGEIRO)
    notificacao(sessao, evento, "resumo_semanal", "enviado")
    pedidos, resultado = _montar(ex, sessao, SEGUNDA, True)
    assert pedidos == []
    assert resultado.resumo_semanal_pedidos == 0


def test_parte_pendente_reenviada_numa_quarta(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=QUARTA + timedelta(days=7))
    classificar_saida(sessao, evento, ESTRANGEIRO)
    notificacao(sessao, evento, "resumo_semanal", "enviando")
    pedidos, _ = _montar(ex, sessao, QUARTA, False)
    assert _tipos(pedidos) == [(evento.id, "resumo_semanal")]


def test_estrangeiro_antes_do_proximo_resumo_vira_aviso(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=QUARTA + timedelta(days=3))  # antes de 08/12
    classificar_saida(sessao, evento, ESTRANGEIRO)
    pedidos, resultado = _montar(ex, sessao, QUARTA, False)
    assert _tipos(pedidos) == [(evento.id, "aviso")]
    assert resultado.motivos["estrangeiro_antes_do_resumo"] == 1
    assert resultado.resumo_semanal_adiado == 0


def test_falha_na_segunda_cedo_vista_no_domingo_vira_excepcional(
    ex: ModuleType, sessao: Session
) -> None:
    domingo = datetime(2026, 11, 29, 22, 0, tzinfo=SP)
    evento = inserir_evento(sessao, inicio=datetime(2026, 11, 30, 5, 0, tzinfo=SP))
    erros_de_saida(sessao, evento, 3)
    pedidos, resultado = _montar(ex, sessao, domingo, False)
    assert _tipos(pedidos) == [(evento.id, "aviso_excepcional")]
    assert resultado.motivos["falha_antes_do_resumo"] == 1


def test_evento_ja_em_resumo_nao_recebe_aviso(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=datetime(2026, 12, 3, 19, 0, tzinfo=SP))  # quinta
    classificar_saida(sessao, evento, ESTRANGEIRO)
    pedidos, _ = _montar(ex, sessao, SEGUNDA, True)  # proximo_resumo == agora → resumo
    assert _tipos(pedidos) == [(evento.id, "resumo_semanal")]
    notificacao(sessao, evento, "resumo_semanal", "enviado")

    pedidos, resultado = _montar(ex, sessao, SEGUNDA.replace(hour=15), False)
    assert resultado.motivos["estrangeiro_antes_do_resumo"] == 1  # a decisão seria aviso
    assert pedidos == []
    assert resultado.ja_em_resumo == 1


def test_pendente_de_aviso_ja_em_resumo_nao_e_reenviado(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=QUARTA + timedelta(days=1))
    classificar_saida(sessao, evento, ESTRANGEIRO)
    notificacao(sessao, evento, "resumo_semanal", "enviado")
    notificacao(sessao, evento, "aviso", "enviando")
    pedidos, resultado = _montar(ex, sessao, QUARTA, False)
    assert pedidos == []
    assert resultado.ja_em_resumo == 1


@pytest.mark.parametrize("aviso_enviando", [False, True], ids=["so-resumo", "aviso-enviando"])
def test_resumo_enviando_bloqueia_aviso(
    ex: ModuleType, sessao: Session, aviso_enviando: bool
) -> None:
    # segunda 07:40: a parte do resumo com X falhou (500) sem interromper; marco gravado
    evento = inserir_evento(sessao, inicio=datetime(2026, 12, 2, 19, 0, tzinfo=SP))  # quarta
    classificar_saida(sessao, evento, ESTRANGEIRO)
    notificacao(sessao, evento, "resumo_semanal", "enviando")
    if aviso_enviando:
        notificacao(sessao, evento, "aviso", "enviando")
    # segunda 12:40, com marco: pendente de resumo volta; a decisão nova seria aviso
    pedidos, resultado = _montar(ex, sessao, SEGUNDA.replace(hour=12), False)
    assert resultado.motivos["estrangeiro_antes_do_resumo"] == 1
    assert _tipos(pedidos) == [(evento.id, "resumo_semanal")]
    assert resultado.ja_em_resumo == 1


def test_ja_em_resumo_comeca_em_zero_e_aparece_no_resumo(ex: ModuleType) -> None:
    resultado = ex.ResultadoExecucao(rodada=uuid.uuid4())
    assert resultado.ja_em_resumo == 0
    assert "ja_em_resumo=0" in ex.linhas_resumo(resultado)


# --- executar ------------------------------------------------------------------------------


def _executar(
    ex: ModuleType,
    sessao: Session,
    agora: datetime,
    telegram: RedeTelegram,
    llm: LLMFalso | None = None,
) -> Any:
    return ex.executar(
        sessao,
        config=Configuracao(
            limite_chamadas_llm_por_execucao=50, limite_paginas_enriquecimento_por_execucao=0
        ),
        calendarios=calendarios(),
        http=RedeLuma().cliente(),
        llm=llm or LLMFalso(estrangeiro=lambda _titulo: True),
        telegram=telegram.cliente(),
        agora=agora,
        rodada=uuid.uuid4(),
    )


def _marco(sessao: Session, agora: datetime) -> list[dict[str, Any]]:
    return linhas(
        sessao,
        "SELECT tipo, estado, enviado_em FROM envios_sem_evento WHERE chave = :c",
        c=chave_semana(agora),
    )


def _resumos(sessao: Session) -> list[dict[str, Any]]:
    return linhas(
        sessao, "SELECT evento_id, estado FROM notificacoes WHERE tipo = 'resumo_semanal'"
    )


def test_segunda_sem_marco_envia_e_grava(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    resultado = _executar(ex, sessao, SEGUNDA, telegram)
    assert resultado.resumo_agora is True
    assert resultado.resumo_semanal_pedidos > 0
    assert telegram.corpos
    assert resultado.marco_gravado is True
    assert _marco(sessao, SEGUNDA) == [
        {"tipo": "resumo_semanal", "estado": "enviado", "enviado_em": SEGUNDA}
    ]
    assert {linha["estado"] for linha in _resumos(sessao)} == {"enviado"}
    assert len(_resumos(sessao)) == resultado.resumo_semanal_pedidos


def test_segunda_a_tarde_com_marco_nao_envia(ex: ModuleType, sessao: Session) -> None:
    _executar(ex, sessao, SEGUNDA, RedeTelegram())
    telegram = RedeTelegram()
    tarde = SEGUNDA.replace(hour=15)
    antes = len(_resumos(sessao))
    resultado = _executar(ex, sessao, tarde, telegram)
    assert resultado.resumo_agora is False
    assert resultado.marco_gravado is False
    assert resultado.resumo_semanal_pedidos == 0
    assert len(_resumos(sessao)) == antes  # nenhum evento novo em resumo


def test_terca_sem_marco_envia_e_grava(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    resultado = _executar(ex, sessao, TERCA, telegram)
    assert resultado.resumo_agora is True
    assert resultado.resumo_semanal_pedidos > 0
    assert telegram.corpos
    assert resultado.marco_gravado is True
    assert len(_marco(sessao, TERCA)) == 1


def test_quarta_com_marco_adia(ex: ModuleType, sessao: Session) -> None:
    gravar_marco(sessao, QUARTA)
    sessao.commit()
    telegram = RedeTelegram()
    resultado = _executar(ex, sessao, QUARTA, telegram)
    assert resultado.resumo_agora is False
    assert resultado.resumo_semanal_pedidos == 0
    assert resultado.resumo_semanal_adiado > 0  # começam depois da terça 08/12
    assert resultado.motivos["estrangeiro_antes_do_resumo"] > 0  # começam antes: aviso
    assert _resumos(sessao) == []
    assert len(telegram.corpos) == resultado.destinos["aviso"]


def test_resumo_vazio_grava_marco(ex: ModuleType, sessao: Session) -> None:
    telegram = RedeTelegram()
    resultado = _executar(ex, sessao, SEGUNDA, telegram, llm=LLMFalso())  # só avisos
    assert resultado.resumo_agora is True
    assert resultado.resumo_semanal_pedidos == 0
    assert resultado.marco_gravado is True
    assert len(_marco(sessao, SEGUNDA)) == 1


def test_429_no_meio_nao_grava_marco_e_completa_depois(ex: ModuleType, sessao: Session) -> None:
    primeiro = RedeTelegram(respostas=[200, 429])
    resultado = _executar(ex, sessao, SEGUNDA, primeiro)
    assert len(primeiro.corpos) == 2, "o resumo precisa de pelo menos 2 partes"
    assert resultado.notificacao.interrompido_por == "rate_limit"
    assert resultado.marco_gravado is False
    assert _marco(sessao, SEGUNDA) == []
    enviados_antes = {
        linha["evento_id"] for linha in _resumos(sessao) if linha["estado"] == "enviado"
    }
    assert enviados_antes
    enviando_antes = {
        linha["evento_id"] for linha in _resumos(sessao) if linha["estado"] == "enviando"
    }
    assert enviando_antes

    segundo = RedeTelegram()
    depois = _executar(ex, sessao, SEGUNDA.replace(hour=12), segundo)
    assert depois.resumo_agora is True
    assert depois.marco_gravado is True
    assert segundo.corpos
    assert {linha["estado"] for linha in _resumos(sessao)} == {"enviado"}
    total = len(_resumos(sessao))
    # pendentes da parte que falhou voltam por `pendentes_pedidas`; novos por
    # `resumo_semanal_pedidos`; juntos cobrem exatamente o que faltava, sem duplicar
    assert depois.pendentes_pedidas == len(enviando_antes)
    assert depois.resumo_semanal_pedidos + depois.pendentes_pedidas == total - len(enviados_antes)
