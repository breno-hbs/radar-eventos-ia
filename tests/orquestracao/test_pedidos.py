"""Testes do F1-10b — `montar_pedidos` (decisão; só leitura; Postgres de teste).

Assinaturas fixadas (radar.orquestracao.executar):
- `ResultadoExecucao(rodada=...)` com os demais campos com padrão (contadores 0, Counters
  vazios, listas vazias, `classificacao`/`enriquecimento`/`notificacao`/`rodada_banco` None);
- `montar_pedidos(sessao, *, slugs_comunidade, agora, proximo_resumo, resultado)
  -> list[PedidoNotificacao]`: pendentes primeiro, depois as decisões novas; atualiza
  `resultado.destinos`, `resultado.motivos`, `pendentes_pedidas`,
  `pendentes_sem_classificacao`, `pendentes_terminadas`, `pendentes_inativas`,
  `pendentes_sem_aviso` e `resumo_semanal_adiado`;
- pendente de evento com estado fora de ativo/alterado → sem pedido (`pendentes_inativas`),
  qualquer tipo; pendente `aviso` só é pedido de novo se `decidir_destino` ainda der "aviso"
  (senão `pendentes_sem_aviso`, e a linha continua 'enviando').
"""

from __future__ import annotations

import importlib
import uuid
from datetime import timedelta
from types import ModuleType
from typing import Any

import pytest
from banco.conftest import linhas
from classificacao.fabricas import AGORA, saida_valida
from sqlalchemy.orm import Session

from .dados_banco import (
    classificar_saida,
    enriquecer,
    erros_de_saida,
    inserir_evento,
    notificacao,
)

PROXIMO = AGORA + timedelta(days=5)
COMUNIDADES = frozenset({"codex-sp"})


@pytest.fixture
def ex() -> ModuleType:
    return importlib.import_module("radar.orquestracao.executar")


def _montar(ex: ModuleType, sessao: Session, slugs: frozenset[str] = COMUNIDADES) -> tuple:
    resultado = ex.ResultadoExecucao(rodada=uuid.uuid4())
    pedidos = ex.montar_pedidos(
        sessao, slugs_comunidade=slugs, agora=AGORA, proximo_resumo=PROXIMO, resultado=resultado
    )
    return pedidos, resultado


def _resumo(pedidos: list[Any]) -> list[tuple[int, str]]:
    return [(p.evento.id, p.tipo) for p in pedidos]


def test_aviso(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, saida_valida())
    enriquecer(sessao, evento)
    pedidos, resultado = _montar(ex, sessao)
    assert _resumo(pedidos) == [(evento.id, "aviso")]
    [pedido] = pedidos
    assert pedido.saida.model_dump(mode="json") == saida_valida()
    assert pedido.enriquecimento is not None
    assert pedido.enriquecimento.hash_conteudo == evento.hash_conteudo
    assert resultado.destinos["aviso"] == 1
    assert resultado.motivos["idioma_ok"] == 1


def test_aviso_ja_enviado_nao_repete(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, saida_valida())
    notificacao(sessao, evento, "aviso", "enviado")
    pedidos, resultado = _montar(ex, sessao)
    assert pedidos == []
    assert resultado.destinos["aviso"] == 1  # a decisão é contada mesmo sem pedido


def test_falha_vira_aviso_excepcional_sem_saida(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, inicio=AGORA + timedelta(days=2))
    erros_de_saida(sessao, evento, 3)
    pedidos, resultado = _montar(ex, sessao)
    assert _resumo(pedidos) == [(evento.id, "aviso_excepcional")]
    assert pedidos[0].saida is None
    assert resultado.motivos["falha_antes_do_resumo"] == 1


def test_estrangeiro_presencial_e_adiado(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, saida_valida(idioma="en", trecho_idioma="in English"))
    pedidos, resultado = _montar(ex, sessao)
    assert pedidos == []
    assert resultado.resumo_semanal_adiado == 1
    assert resultado.destinos["resumo_semanal"] == 1
    assert resultado.motivos["estrangeiro_presencial"] == 1


def test_comunidade_com_e_ia_false_vira_aviso_sem_gravar(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, calendarios=["codex-sp"])
    classificar_saida(sessao, evento, saida_valida(e_ia=False))
    pedidos, _ = _montar(ex, sessao)
    assert _resumo(pedidos) == [(evento.id, "aviso")]
    [gravada] = linhas(
        sessao, "SELECT resultado FROM classificacoes WHERE evento_id = :e", e=evento.id
    )
    assert gravada["resultado"]["e_ia"] is False
    assert "e_ia_por_comunidade" not in gravada["resultado"]


def test_sem_comunidade_e_ia_false_nao_gera_pedido(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, calendarios=["codex-sp"])
    classificar_saida(sessao, evento, saida_valida(e_ia=False))
    pedidos, resultado = _montar(ex, sessao, slugs=frozenset())
    assert pedidos == []
    assert resultado.motivos["nao_ia"] == 1


def test_pendente_com_saida(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, saida_valida())
    notificacao(sessao, evento, "aviso", "enviando")
    pedidos, resultado = _montar(ex, sessao)
    assert _resumo(pedidos) == [(evento.id, "aviso")]
    assert pedidos[0].saida is not None
    assert resultado.pendentes_pedidas == 1


def test_pendente_sem_classificacao_ok_atual(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    notificacao(sessao, evento, "aviso", "enviando")
    pedidos, resultado = _montar(ex, sessao)
    assert pedidos == []
    assert resultado.pendentes_sem_classificacao == 1
    [linha] = linhas(sessao, "SELECT estado FROM notificacoes WHERE evento_id = :e", e=evento.id)
    assert linha["estado"] == "enviando"


def test_pendente_de_evento_terminado(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(
        sessao, inicio=AGORA - timedelta(hours=3), fim=AGORA - timedelta(hours=1)
    )
    classificar_saida(sessao, evento, saida_valida())
    notificacao(sessao, evento, "aviso", "enviando")
    pedidos, resultado = _montar(ex, sessao)
    assert pedidos == []
    assert resultado.pendentes_terminadas == 1


@pytest.mark.parametrize("classificado", [True, False])
def test_pendente_de_resumo_semanal(ex: ModuleType, sessao: Session, classificado: bool) -> None:
    evento = inserir_evento(sessao)
    if classificado:
        classificar_saida(sessao, evento, saida_valida(idioma="en", trecho_idioma="in English"))
    notificacao(sessao, evento, "resumo_semanal", "enviando")
    pedidos, _ = _montar(ex, sessao)
    assert _resumo(pedidos) == [(evento.id, "resumo_semanal")]
    assert (pedidos[0].saida is not None) is classificado


def test_pendente_de_aviso_excepcional_vai_sem_saida(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, saida_valida())
    notificacao(sessao, evento, "aviso_excepcional", "enviando")
    pedidos, _ = _montar(ex, sessao)
    excepcionais = [p for p in pedidos if p.tipo == "aviso_excepcional"]
    assert [(p.evento.id, p.saida) for p in excepcionais] == [(evento.id, None)]


def test_pendente_e_decisao_nova_geram_um_pedido(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, saida_valida())
    notificacao(sessao, evento, "aviso", "enviando")
    pedidos, resultado = _montar(ex, sessao)
    assert _resumo(pedidos) == [(evento.id, "aviso")]
    assert resultado.destinos["aviso"] == 1


def test_pendentes_vem_antes_das_decisoes_novas(ex: ModuleType, sessao: Session) -> None:
    novo = inserir_evento(sessao, inicio=AGORA + timedelta(days=1))
    classificar_saida(sessao, novo, saida_valida())
    pendente = inserir_evento(sessao, inicio=AGORA + timedelta(days=2))
    classificar_saida(sessao, pendente, saida_valida())
    notificacao(sessao, pendente, "aviso", "enviando")
    pedidos, _ = _montar(ex, sessao)
    assert _resumo(pedidos) == [(pendente.id, "aviso"), (novo.id, "aviso")]


def test_resultado_invalido_no_banco_e_erro_de_decisao(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, {"e_ia": "talvez"})
    with pytest.raises(ex.ErroEtapa) as info:
        _montar(ex, sessao)
    assert (info.value.etapa, info.value.tipo) == ("decisao", "ValidationError")


def test_montar_pedidos_nao_escreve(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, saida_valida())
    sessao.commit()
    antes = linhas(sessao, "SELECT count(*) AS n FROM notificacoes")[0]["n"]
    _montar(ex, sessao)
    assert not (sessao.new or sessao.dirty or sessao.deleted)
    assert linhas(sessao, "SELECT count(*) AS n FROM notificacoes")[0]["n"] == antes


@pytest.mark.parametrize("estado", ["cancelado", "encerrado", "nao_encontrado"])
@pytest.mark.parametrize("tipo", ["aviso", "aviso_excepcional", "resumo_semanal"])
def test_pendente_de_evento_inativo(
    ex: ModuleType, sessao: Session, estado: str, tipo: str
) -> None:
    evento = inserir_evento(sessao, estado=estado)
    classificar_saida(sessao, evento, saida_valida())
    notificacao(sessao, evento, tipo, "enviando")
    pedidos, resultado = _montar(ex, sessao)
    assert pedidos == []
    assert resultado.pendentes_inativas == 1
    assert resultado.pendentes_pedidas == 0
    [linha] = linhas(sessao, "SELECT estado FROM notificacoes WHERE evento_id = :e", e=evento.id)
    assert linha["estado"] == "enviando"


@pytest.mark.parametrize(
    "resultado_llm",
    [
        saida_valida(e_ia=False),  # reclassificado como não IA
        saida_valida(idioma="en", trecho_idioma="in English"),  # virou resumo semanal
    ],
    ids=["nao-ia", "estrangeiro-presencial"],
)
def test_pendente_de_aviso_que_nao_e_mais_aviso(
    ex: ModuleType, sessao: Session, resultado_llm: dict[str, Any]
) -> None:
    evento = inserir_evento(sessao)
    classificar_saida(sessao, evento, resultado_llm)
    notificacao(sessao, evento, "aviso", "enviando")
    pedidos, resultado = _montar(ex, sessao)
    assert [p for p in pedidos if p.tipo == "aviso"] == []
    assert resultado.pendentes_sem_aviso == 1
    assert resultado.pendentes_pedidas == 0
    [linha] = linhas(sessao, "SELECT estado FROM notificacoes WHERE evento_id = :e", e=evento.id)
    assert linha["estado"] == "enviando"


def test_pendente_de_aviso_de_comunidade_continua_aviso(ex: ModuleType, sessao: Session) -> None:
    evento = inserir_evento(sessao, calendarios=["codex-sp"])
    classificar_saida(sessao, evento, saida_valida(e_ia=False))
    notificacao(sessao, evento, "aviso", "enviando")
    pedidos, resultado = _montar(ex, sessao)
    assert _resumo(pedidos) == [(evento.id, "aviso")]
    assert resultado.pendentes_sem_aviso == 0


def test_novos_contadores_comecam_em_zero(ex: ModuleType) -> None:
    resultado = ex.ResultadoExecucao(rodada=uuid.uuid4())
    assert resultado.pendentes_inativas == 0
    assert resultado.pendentes_sem_aviso == 0
