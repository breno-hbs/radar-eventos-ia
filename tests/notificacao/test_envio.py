"""Testes do F1-09 — envio idempotente dos avisos (Postgres de teste + enviador falso).

Assinaturas fixadas (radar.notificacao.envio):
- `TipoNotificacao = Literal["aviso", "aviso_excepcional", "resumo_semanal"]`;
- `PedidoNotificacao(tipo, evento, saida, enriquecimento)` frozen; aviso com saida None →
  ValueError (aviso_excepcional e resumo_semanal aceitam None);
- `ResumoNotificacao`: enviados: list[tuple[int, str]] (evento_id, tipo), ja_enviados: idem,
  ignorados_fora: list[int], falhas: list[tuple[int, str, str]] (evento_id, tipo, categoria),
  interrompido_por: categoria do ErroTelegram | "falhas_seguidas" | None,
  pendentes: list[tuple[int, str]] (não tentados por causa da interrupção);
- `notificar(sessao, cliente: EnviadorMensagens, pedidos, agora, fuso: ZoneInfo)`:
  sessão com alterações pendentes sem flush (new/dirty/deleted) → ValueError antes de
  qualquer envio; as notificações são gravadas e commitadas numa sessão PRÓPRIA (mesmo bind);
  `commit` da sessão do chamador nunca é chamado. Com bind = Engine (produção), a transação
  do chamador não pode ter escrita pendente (`pg_current_xact_id_if_assigned()` não nulo →
  ValueError antes de qualquer envio); com bind = Connection (fixture `sessao`) não há essa
  verificação;
  'fora' ignorado sem linha; reserva 'enviando' + commit antes de enviar; depois 'enviado',
  enviada_em=agora, commit; 'enviado' existente pula sem requisição; 'enviando' anterior é
  reenviado; falha deixa 'enviando'; autenticacao/proibido/rate_limit interrompem na hora;
  as demais após 3 seguidas (sucesso zera); pedido repetido na mesma execução não repete; no
  resumo semanal a unidade é a parte de `formatar_resumo_semanal`.
- `notificacoes_pendentes(sessao) -> list[tuple[int, str]]`: (evento_id, tipo) em 'enviando',
  ordem evento_id, tipo.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType
from typing import Any

import httpx
import pytest
from banco.conftest import linhas
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from radar.config import Configuracao

from .fabricas import AGORA, SP, evento, saida

TOKEN = "123456:SEGREDO-do-bot-AbCdEf"
CHAT = 555000111


@pytest.fixture
def envio() -> ModuleType:
    return importlib.import_module("radar.notificacao.envio")


@pytest.fixture
def tg() -> ModuleType:
    return importlib.import_module("radar.notificacao.telegram")


@pytest.fixture
def msg() -> ModuleType:
    return importlib.import_module("radar.notificacao.mensagens")


@dataclass
class EnviadorFalso:
    """Grava os textos; itens da fila que são exceção são levantados na vez deles."""

    fila: list[Any] = field(default_factory=list)
    textos: list[str] = field(default_factory=list)

    def enviar_texto(self, texto: str) -> None:
        self.textos.append(texto)
        if self.fila:
            item = self.fila.pop(0)
            if isinstance(item, Exception):
                raise item


def _erro(tg: ModuleType, categoria: str, status: int | None = None) -> Exception:
    return tg.ErroTelegram(categoria=categoria, status_http=status, retry_after=None)


def _inserir(sessao: Session, **campos: Any) -> Any:
    linha = evento(**campos)
    sessao.add(linha)
    sessao.commit()  # savepoint do teste: sessão limpa para `notificar`
    return linha


def _pedido(envio: ModuleType, ev: Any, tipo: str = "aviso", s: Any = ...) -> Any:
    return envio.PedidoNotificacao(
        tipo=tipo, evento=ev, saida=saida() if s is ... else s, enriquecimento=None
    )


def _rodar(
    envio: ModuleType, sessao: Session, cliente: Any, pedidos: list[Any], agora: datetime = AGORA
) -> Any:
    return envio.notificar(sessao, cliente, pedidos, agora, SP)


def _notificacoes(sessao: Session) -> list[dict[str, Any]]:
    return linhas(sessao, "SELECT * FROM notificacoes ORDER BY id")


def _estado(sessao: Session, evento_id: int, tipo: str = "aviso") -> str | None:
    achadas = linhas(
        sessao,
        "SELECT estado FROM notificacoes WHERE evento_id = :e AND tipo = :t",
        e=evento_id,
        t=tipo,
    )
    return achadas[0]["estado"] if achadas else None


# --- Pedido --------------------------------------------------------------------------------


def test_aviso_sem_saida_e_erro(envio: ModuleType) -> None:
    with pytest.raises(ValueError):
        _pedido(envio, evento(), "aviso", None)


@pytest.mark.parametrize("tipo", ["aviso_excepcional", "resumo_semanal"])
def test_outros_tipos_aceitam_sem_saida(envio: ModuleType, tipo: str) -> None:
    assert _pedido(envio, evento(), tipo, None).saida is None


# --- Caminho feliz -------------------------------------------------------------------------


def test_aviso_enviado_e_marcado(envio: ModuleType, msg: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso()
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert cliente.textos == [msg.formatar_aviso(ev, saida(), None, SP)]
    assert resumo.enviados == [(ev.id, "aviso")]
    assert resumo.interrompido_por is None
    [linha] = _notificacoes(sessao)
    assert (linha["evento_id"], linha["tipo"], linha["estado"]) == (ev.id, "aviso", "enviado")
    assert linha["enviada_em"] == AGORA


def test_aviso_excepcional_usa_o_formato_proprio(
    envio: ModuleType, msg: ModuleType, sessao: Session
) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso()
    _rodar(envio, sessao, cliente, [_pedido(envio, ev, "aviso_excepcional", None)])
    assert cliente.textos == [msg.formatar_aviso_excepcional(ev, SP)]


def test_rodar_duas_vezes_nao_duplica(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso()
    _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert len(cliente.textos) == 1
    assert resumo.ja_enviados == [(ev.id, "aviso")]
    assert resumo.enviados == []
    assert len(_notificacoes(sessao)) == 1


def test_pedido_repetido_na_mesma_execucao(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso()
    _rodar(envio, sessao, cliente, [_pedido(envio, ev), _pedido(envio, ev)])
    assert len(cliente.textos) == 1


def test_aviso_e_resumo_do_mesmo_evento(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso()
    _rodar(envio, sessao, cliente, [_pedido(envio, ev), _pedido(envio, ev, "resumo_semanal")])
    assert len(cliente.textos) == 2
    tipos = sorted(linha["tipo"] for linha in _notificacoes(sessao))
    assert tipos == ["aviso", "resumo_semanal"]


def test_evento_fora_e_ignorado(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao, localizacao="fora", evidencia_localizacao="texto")
    cliente = EnviadorFalso()
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert cliente.textos == []
    assert resumo.ignorados_fora == [ev.id]
    assert _notificacoes(sessao) == []


# --- Sessão própria: notificar nunca commita o trabalho do chamador -----------------------


@pytest.fixture
def sessao_real(engine: Engine) -> Iterator[tuple[Session, list[int]]]:
    """Sessão ligada ao engine (transação real, sem o savepoint da fixture `sessao`).

    Os eventos criados ficam em `criados` e são apagados no fim (cascata nas notificações).
    """
    real = Session(bind=engine)
    criados: list[int] = []
    try:
        yield real, criados
    finally:
        real.rollback()
        real.close()
        with engine.begin() as conexao:
            conexao.execute(
                text("DELETE FROM eventos WHERE id = ANY(:ids) OR titulo LIKE 'Do chamador%'"),
                {"ids": criados},
            )


def _consultar(engine: Engine, sql: str, **parametros: Any) -> list[dict[str, Any]]:
    with engine.connect() as conexao:
        return [dict(linha) for linha in conexao.execute(text(sql), parametros).mappings()]


def _proibir_commit(real: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def proibido() -> None:
        raise AssertionError("notificar não pode commitar a sessão do chamador")

    monkeypatch.setattr(real, "commit", proibido)


def _evento_commitado(real: Session, criados: list[int], **campos: Any) -> Any:
    ev = evento(**campos)
    real.add(ev)
    real.commit()
    criados.append(ev.id)
    return ev


def test_escrita_pendente_do_chamador_insert_e_erro(
    envio: ModuleType,
    engine: Engine,
    sessao_real: tuple[Session, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real, criados = sessao_real
    ev = _evento_commitado(real, criados)
    # trabalho do chamador, já em flush, ainda não commitado (bind = Engine)
    real.add(evento(titulo="Do chamador, não commitar"))
    real.flush()
    _proibir_commit(real, monkeypatch)
    cliente = EnviadorFalso()

    with pytest.raises(ValueError):
        envio.notificar(real, cliente, [_pedido(envio, ev)], AGORA, SP)

    assert cliente.textos == []
    assert _consultar(engine, "SELECT id FROM notificacoes WHERE evento_id = :e", e=ev.id) == []
    real.rollback()
    assert _consultar(engine, "SELECT id FROM eventos WHERE titulo LIKE 'Do chamador%'") == []


def test_escrita_pendente_do_chamador_update_e_erro(
    envio: ModuleType,
    engine: Engine,
    sessao_real: tuple[Session, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real, criados = sessao_real
    ev = _evento_commitado(real, criados, titulo="Original")
    ev.titulo = "Do chamador, alterado sem commit"
    real.flush()
    _proibir_commit(real, monkeypatch)
    cliente = EnviadorFalso()

    with pytest.raises(ValueError):
        envio.notificar(real, cliente, [_pedido(envio, ev)], AGORA, SP)

    assert cliente.textos == []
    assert _consultar(engine, "SELECT id FROM notificacoes WHERE evento_id = :e", e=ev.id) == []
    real.rollback()
    [linha] = _consultar(engine, "SELECT titulo FROM eventos WHERE id = :e", e=ev.id)
    assert linha["titulo"] == "Original"


def test_sessao_real_so_com_leituras_funciona(
    envio: ModuleType,
    engine: Engine,
    sessao_real: tuple[Session, list[int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from radar.banco.modelos import Evento

    real, criados = sessao_real
    ev = _evento_commitado(real, criados)
    # só leituras na transação do chamador
    assert real.scalar(select(Evento.titulo).where(Evento.id == ev.id)) is not None
    real.execute(text("SELECT 1"))
    _proibir_commit(real, monkeypatch)
    cliente = EnviadorFalso()

    resumo = envio.notificar(real, cliente, [_pedido(envio, ev)], AGORA, SP)

    assert resumo.enviados == [(ev.id, "aviso")]
    assert len(cliente.textos) == 1
    real.rollback()  # notificações gravadas pela sessão própria persistem
    [linha] = _consultar(
        engine, "SELECT estado, enviada_em FROM notificacoes WHERE evento_id = :e", e=ev.id
    )
    assert linha["estado"] == "enviado"
    assert linha["enviada_em"] == AGORA


def test_reserva_visivel_para_outra_conexao_antes_do_envio(
    envio: ModuleType, engine: Engine, sessao_real: tuple[Session, list[int]]
) -> None:
    real, criados = sessao_real
    ev = evento()
    real.add(ev)
    real.commit()
    criados.append(ev.id)
    vistos: list[list[dict[str, Any]]] = []

    class Espiao(EnviadorFalso):
        def enviar_texto(self, texto: str) -> None:
            vistos.append(
                _consultar(engine, "SELECT estado FROM notificacoes WHERE evento_id = :e", e=ev.id)
            )
            super().enviar_texto(texto)

    envio.notificar(real, Espiao(), [_pedido(envio, ev)], AGORA, SP)
    assert vistos == [[{"estado": "enviando"}]]


# --- Pendências ----------------------------------------------------------------------------


def test_notificacoes_pendentes(envio: ModuleType, tg: ModuleType, sessao: Session) -> None:
    um = _inserir(sessao)
    dois = _inserir(sessao)
    cliente = EnviadorFalso(fila=[_erro(tg, "servidor", 500), None, None])
    _rodar(envio, sessao, cliente, [_pedido(envio, um), _pedido(envio, dois)])
    assert envio.notificacoes_pendentes(sessao) == [(um.id, "aviso")]
    _rodar(envio, sessao, cliente, [_pedido(envio, um), _pedido(envio, dois)])
    assert envio.notificacoes_pendentes(sessao) == []


def test_notificacoes_pendentes_ordenadas(
    envio: ModuleType, tg: ModuleType, sessao: Session
) -> None:
    um = _inserir(sessao)
    dois = _inserir(sessao)
    cliente = EnviadorFalso(fila=[_erro(tg, "servidor", 500) for _ in range(2)])
    _rodar(
        envio,
        sessao,
        cliente,
        [_pedido(envio, dois, "aviso_excepcional", None), _pedido(envio, um)],
    )
    assert envio.notificacoes_pendentes(sessao) == [
        (um.id, "aviso"),
        (dois.id, "aviso_excepcional"),
    ]


# --- Falhas --------------------------------------------------------------------------------


def test_falha_fica_enviando_e_reenvia_depois(
    envio: ModuleType, tg: ModuleType, sessao: Session
) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso(fila=[_erro(tg, "servidor", 500)])
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert resumo.falhas == [(ev.id, "aviso", "servidor")]
    assert resumo.enviados == []
    assert _estado(sessao, ev.id) == "enviando"
    seguinte = _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert seguinte.enviados == [(ev.id, "aviso")]
    assert len(cliente.textos) == 2
    assert _estado(sessao, ev.id) == "enviado"


def test_queda_apos_entrega_reenvia(envio: ModuleType, sessao: Session) -> None:
    from radar.banco.modelos import Notificacao

    ev = _inserir(sessao)
    # execução anterior reservou, entregou e caiu antes de marcar 'enviado'
    sessao.add(Notificacao(evento_id=ev.id, tipo="aviso", estado="enviando", criada_em=AGORA))
    sessao.commit()
    cliente = EnviadorFalso()
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert len(cliente.textos) == 1  # duplicata aceita
    assert resumo.enviados == [(ev.id, "aviso")]
    assert _estado(sessao, ev.id) == "enviado"
    assert len(_notificacoes(sessao)) == 1


@pytest.mark.parametrize(
    ("categoria", "status"), [("rate_limit", 429), ("autenticacao", 401), ("proibido", 403)]
)
def test_erros_que_interrompem_na_hora(
    envio: ModuleType, tg: ModuleType, sessao: Session, categoria: str, status: int
) -> None:
    eventos = [_inserir(sessao) for _ in range(3)]
    cliente = EnviadorFalso(fila=[_erro(tg, categoria, status)])
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, e) for e in eventos])
    assert len(cliente.textos) == 1
    assert resumo.interrompido_por == categoria
    assert resumo.falhas == [(eventos[0].id, "aviso", categoria)]
    assert resumo.pendentes == [(eventos[1].id, "aviso"), (eventos[2].id, "aviso")]
    assert _estado(sessao, eventos[1].id) is None


def test_tres_falhas_seguidas_interrompem(
    envio: ModuleType, tg: ModuleType, sessao: Session
) -> None:
    eventos = [_inserir(sessao) for _ in range(5)]
    cliente = EnviadorFalso(fila=[_erro(tg, "requisicao", 400) for _ in range(3)])
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, e) for e in eventos])
    assert len(cliente.textos) == 3
    assert resumo.interrompido_por == "falhas_seguidas"
    assert [p[0] for p in resumo.pendentes] == [eventos[3].id, eventos[4].id]


def test_sucesso_zera_a_sequencia(envio: ModuleType, tg: ModuleType, sessao: Session) -> None:
    eventos = [_inserir(sessao) for _ in range(5)]
    falha = _erro(tg, "requisicao", 400)
    cliente = EnviadorFalso(fila=[falha, None, falha, falha, None])
    resumo = _rodar(envio, sessao, cliente, [_pedido(envio, e) for e in eventos])
    assert len(cliente.textos) == 5
    assert resumo.interrompido_por is None
    assert len(resumo.enviados) == 2
    assert len(resumo.falhas) == 3


# --- Pré-condições -------------------------------------------------------------------------


def test_sessao_com_alteracoes_pendentes_e_erro(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    sessao.add(evento())  # novo, não gravado
    cliente = EnviadorFalso()
    with pytest.raises(ValueError):
        _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert cliente.textos == []


def test_sessao_com_objeto_alterado_e_erro(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    ev.titulo = "Mudou sem gravar"
    cliente = EnviadorFalso()
    with pytest.raises(ValueError):
        _rodar(envio, sessao, cliente, [_pedido(envio, ev)])
    assert cliente.textos == []


def test_agora_sem_fuso_e_erro(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso()
    with pytest.raises(ValueError):
        _rodar(envio, sessao, cliente, [_pedido(envio, ev)], agora=datetime(2026, 11, 25, 12))
    assert cliente.textos == []


# --- Resumo semanal ------------------------------------------------------------------------


def _eventos_do_resumo(sessao: Session, quantidade: int) -> list[Any]:
    return [
        _inserir(sessao, titulo=f"Evento {i:03d} " + "T" * 180, local_texto="L" * 190)
        for i in range(quantidade)
    ]


def test_resumo_dividido_com_falha_na_segunda_parte(
    envio: ModuleType, msg: ModuleType, tg: ModuleType, sessao: Session
) -> None:
    eventos = _eventos_do_resumo(sessao, 40)
    pedidos = [_pedido(envio, e, "resumo_semanal") for e in eventos]
    partes = msg.formatar_resumo_semanal([(e, saida(), None) for e in eventos], SP)
    assert len(partes) >= 3
    cliente = EnviadorFalso(fila=[None, _erro(tg, "servidor", 500)])
    resumo = _rodar(envio, sessao, cliente, pedidos)

    assert cliente.textos == [texto for _, texto in partes]
    for indice, (ids, _) in enumerate(partes):
        esperado = "enviando" if indice == 1 else "enviado"
        assert {_estado(sessao, i, "resumo_semanal") for i in ids} == {esperado}
    assert {f[0] for f in resumo.falhas} == set(partes[1][0])

    # próxima execução: só os eventos da parte que falhou
    cliente2 = EnviadorFalso()
    _rodar(envio, sessao, cliente2, pedidos)
    eventos_parte2 = [e for e in eventos if e.id in set(partes[1][0])]
    esperado2 = msg.formatar_resumo_semanal([(e, saida(), None) for e in eventos_parte2], SP)
    assert cliente2.textos == [texto for _, texto in esperado2]
    assert {_estado(sessao, e.id, "resumo_semanal") for e in eventos} == {"enviado"}


def test_evento_entra_em_um_unico_resumo(envio: ModuleType, sessao: Session) -> None:
    ev = _inserir(sessao)
    cliente = EnviadorFalso()
    _rodar(envio, sessao, cliente, [_pedido(envio, ev, "resumo_semanal")])
    _rodar(envio, sessao, cliente, [_pedido(envio, ev, "resumo_semanal")])
    assert len(cliente.textos) == 1


# --- Com o cliente real (MockTransport) ----------------------------------------------------


def test_chat_id_so_da_configuracao(envio: ModuleType, tg: ModuleType, sessao: Session) -> None:
    corpos: list[dict[str, Any]] = []

    def tratador(requisicao: httpx.Request) -> httpx.Response:
        corpos.append(json.loads(requisicao.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    config = Configuracao(telegram_token=TOKEN, telegram_chat_id=CHAT)
    http = httpx.Client(transport=httpx.MockTransport(tratador))
    cliente = tg.criar_cliente_telegram(config, http)
    eventos = [_inserir(sessao, titulo="chat_id 111222333 no título") for _ in range(2)]
    pedidos = [_pedido(envio, e) for e in eventos]
    pedidos.append(_pedido(envio, eventos[0], "resumo_semanal"))
    _rodar(envio, sessao, cliente, pedidos)
    assert len(corpos) == 3
    assert {c["chat_id"] for c in corpos} == {CHAT}
