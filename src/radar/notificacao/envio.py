"""Envio idempotente das notificações (D19).

O F1-10 decide quais eventos e qual tipo; aqui só se formata, envia e registra. Cada unidade
(uma mensagem; no resumo semanal, uma parte com vários eventos) segue:
'enviando' + commit → envio → 'enviado' + commit. É a exceção documentada à convenção de só
fazer `flush`: o registro precisa sobreviver a uma queda logo depois da entrega. Os commits
acontecem numa sessão PRÓPRIA, ligada ao mesmo bind da sessão do chamador, que nunca é
commitada; a sessão do chamador só fornece os eventos para formatar. `notificar` exige a
transação do chamador sem escritas pendentes (verificado por pg_current_xact_id_if_assigned
quando há conexão separada); o commit é do chamador, antes de notificar. Motivo: com outra
conexão, escrita não commitada causa ForeignKeyViolation ou espera infinita de lock.
'enviando' de uma execução anterior é reenviado quando pedido de novo
(`notificacoes_pendentes`): duplicata rara aceita, perda nunca.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from radar.banco.modelos import Enriquecimento, Evento, Notificacao
from radar.classificacao.modelos import SaidaClassificador
from radar.notificacao.mensagens import (
    formatar_aviso,
    formatar_aviso_excepcional,
    formatar_resumo_semanal,
)
from radar.notificacao.telegram import EnviadorMensagens, ErroTelegram

TipoNotificacao = Literal["aviso", "aviso_excepcional", "resumo_semanal"]
CATEGORIAS_QUE_INTERROMPEM = ("autenticacao", "proibido", "rate_limit")
MAX_FALHAS_SEGUIDAS = 3


@dataclass(frozen=True)
class PedidoNotificacao:
    tipo: TipoNotificacao
    evento: Evento
    saida: SaidaClassificador | None
    enriquecimento: Enriquecimento | None

    def __post_init__(self) -> None:
        if self.tipo not in get_args(TipoNotificacao):
            raise ValueError(f"tipo de notificação desconhecido: {self.tipo!r}")
        if self.tipo == "aviso" and self.saida is None:
            raise ValueError("aviso exige a saída do classificador")


@dataclass
class ResumoNotificacao:
    enviados: list[tuple[int, str]] = field(default_factory=list)
    ja_enviados: list[tuple[int, str]] = field(default_factory=list)
    ignorados_fora: list[int] = field(default_factory=list)
    falhas: list[tuple[int, str, str]] = field(default_factory=list)
    interrompido_por: str | None = None
    pendentes: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class _Unidade:
    tipo: str
    ids: list[int]
    texto: str


def _unidades(pedidos: Sequence[PedidoNotificacao], fuso: ZoneInfo) -> list[_Unidade]:
    """Mensagens na ordem dos pedidos; as partes do resumo entram onde aparece o primeiro."""
    unidades: list[_Unidade] = []
    resumo = [p for p in pedidos if p.tipo == "resumo_semanal"]
    resumo_inserido = False
    for pedido in pedidos:
        if pedido.tipo == "resumo_semanal":
            if not resumo_inserido:
                itens = [(p.evento, p.saida, p.enriquecimento) for p in resumo]
                for ids, texto in formatar_resumo_semanal(itens, fuso):
                    unidades.append(_Unidade("resumo_semanal", ids, texto))
                resumo_inserido = True
            continue
        if pedido.tipo == "aviso":
            assert pedido.saida is not None  # garantido por PedidoNotificacao
            texto = formatar_aviso(pedido.evento, pedido.saida, pedido.enriquecimento, fuso)
        else:
            texto = formatar_aviso_excepcional(pedido.evento, fuso)
        unidades.append(_Unidade(pedido.tipo, [pedido.evento.id], texto))
    return unidades


def _enviar(
    propria: Session,
    cliente: EnviadorMensagens,
    unicos: list[PedidoNotificacao],
    ids: list[int],
    agora: datetime,
    fuso: ZoneInfo,
    resumo: ResumoNotificacao,
) -> None:
    """Reserva, envia e marca, commitando só na sessão própria."""
    existentes: dict[tuple[int, str], Notificacao] = {}
    if ids:
        consulta = select(Notificacao).where(Notificacao.evento_id.in_(ids))
        for linha in propria.scalars(consulta):
            existentes[(linha.evento_id, linha.tipo)] = linha

    a_enviar: list[PedidoNotificacao] = []
    for pedido in unicos:
        chave = (pedido.evento.id, pedido.tipo)
        linha = existentes.get(chave)
        if linha is not None and linha.estado == "enviado":
            resumo.ja_enviados.append(chave)
        else:
            a_enviar.append(pedido)

    # Formata tudo antes de enviar (puro, com os eventos da sessão do chamador).
    unidades = _unidades(a_enviar, fuso)
    falhas_seguidas = 0
    for unidade in unidades:
        chaves = [(evento_id, unidade.tipo) for evento_id in unidade.ids]
        if resumo.interrompido_por is not None:
            resumo.pendentes.extend(chaves)
            continue

        reservas: list[Notificacao] = []
        for evento_id, tipo in chaves:
            linha = existentes.get((evento_id, tipo))
            if linha is None:
                linha = Notificacao(
                    evento_id=evento_id, tipo=tipo, estado="enviando", criada_em=agora
                )
                propria.add(linha)
                existentes[(evento_id, tipo)] = linha
            reservas.append(linha)
        propria.commit()

        try:
            cliente.enviar_texto(unidade.texto)
        except ErroTelegram as erro:
            resumo.falhas.extend((e, t, erro.categoria) for e, t in chaves)
            falhas_seguidas += 1
            if erro.categoria in CATEGORIAS_QUE_INTERROMPEM:
                resumo.interrompido_por = erro.categoria
            elif falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
                resumo.interrompido_por = "falhas_seguidas"
            continue

        for linha in reservas:
            linha.estado = "enviado"
            linha.enviada_em = agora
        propria.commit()
        resumo.enviados.extend(chaves)
        falhas_seguidas = 0


def notificacoes_pendentes(sessao: Session) -> list[tuple[int, str]]:
    """(evento_id, tipo) em 'enviando' — reservados e não confirmados —, para o F1-10 pedir
    de novo em toda execução."""
    consulta = (
        select(Notificacao.evento_id, Notificacao.tipo)
        .where(Notificacao.estado == "enviando")
        .order_by(Notificacao.evento_id, Notificacao.tipo)
    )
    return [(evento_id, tipo) for evento_id, tipo in sessao.execute(consulta)]


def notificar(
    sessao: Session,
    cliente: EnviadorMensagens,
    pedidos: Sequence[PedidoNotificacao],
    agora: datetime,
    fuso: ZoneInfo,
) -> ResumoNotificacao:
    """Envia os pedidos, sem repetir o que já foi entregue (único por evento + tipo)."""
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    if sessao.new or sessao.dirty or sessao.deleted:
        raise ValueError("sessão com alterações pendentes: faça commit antes de notificar")
    # Só com conexão separada (bind = Engine): na mesma conexão a sessão própria enxerga tudo.
    if isinstance(sessao.get_bind(), Engine):
        xid = sessao.execute(text("SELECT pg_current_xact_id_if_assigned()")).scalar()
        if xid is not None:
            raise ValueError(
                "transação do chamador com escritas pendentes: faça commit antes de notificar"
            )

    resumo = ResumoNotificacao()
    vistos: set[tuple[int, str]] = set()
    unicos: list[PedidoNotificacao] = []
    for pedido in pedidos:
        chave = (pedido.evento.id, pedido.tipo)
        if chave in vistos:
            continue
        vistos.add(chave)
        if pedido.evento.localizacao == "fora":
            if pedido.evento.id not in resumo.ignorados_fora:
                resumo.ignorados_fora.append(pedido.evento.id)
            continue
        unicos.append(pedido)

    ids = sorted({p.evento.id for p in unicos})
    with Session(
        bind=sessao.get_bind(),
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    ) as propria:
        _enviar(propria, cliente, unicos, ids, agora, fuso, resumo)
    return resumo
