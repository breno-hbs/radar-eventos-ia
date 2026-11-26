"""Uma execução do radar (D21): coleta → filtros → banco → classificação → enriquecimento →
decisão → notificação.

Commits entre as etapas (banco, classificação e enriquecimento), para que uma falha adiante
não desfaça o que já foi feito e a próxima execução retome dali. Exceção numa etapa → rollback
da etapa e `ErroEtapa(etapa, tipo)` sem a mensagem original (pode trazer dado de terceiros ou
segredo). Nada aqui registra título, URL, token, chat_id nem a URL do banco: o resumo da
execução (`linhas_resumo`) só tem contagens, códigos, slugs e o uuid da rodada.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from radar.banco import leituras
from radar.banco.modelos import Enriquecimento, EnvioSemEvento, Evento
from radar.banco.repositorio import (
    ResumoRodada,
    eventos_com_falha_classificacao,
    registrar_rodada,
)
from radar.classificacao import modelos, prompt
from radar.classificacao.classificador import ResumoClassificacao, classificar_pendentes
from radar.classificacao.cliente import ClienteLLM
from radar.classificacao.modelos import SaidaClassificador
from radar.config import Configuracao
from radar.enriquecimento.preco import ResumoEnriquecimento, enriquecer_precos
from radar.filtros.geografico import filtrar_por_localizacao
from radar.filtros.horizonte import calcular_termino, filtrar_por_horizonte
from radar.fontes.calendarios import CalendarioLuma
from radar.fontes.luma import coletar_luma
from radar.fontes.modelos import ResultadoCalendario
from radar.notificacao.alertas import Alerta, ResumoAlertas, enviar_alertas
from radar.notificacao.envio import (
    PedidoNotificacao,
    ResumoNotificacao,
    notificacoes_pendentes,
    notificar,
)
from radar.notificacao.telegram import EnviadorMensagens
from radar.orquestracao.alertas import detectar_alertas
from radar.orquestracao.destino import decidir_destino, slugs_comunidade_ia

Etapa = Literal[
    "coleta",
    "filtros",
    "banco",
    "classificacao",
    "enriquecimento",
    "decisao",
    "notificacao",
    "alertas",
]
FUSO_RESUMO = ZoneInfo("America/Sao_Paulo")
INTERRUPCOES_GRAVES = ("autenticacao", "proibido")
# Notificação interrompida por estes motivos: alertas ficam para a próxima execução (D22).
INTERRUPCOES_SEM_ALERTA = ("autenticacao", "proibido", "rate_limit")


class ErroEtapa(Exception):
    """Falha numa etapa; só o nome da etapa e o tipo da exceção original."""

    def __init__(self, etapa: str, tipo: str) -> None:
        self.etapa = etapa
        self.tipo = tipo
        super().__init__(f"erro na etapa {etapa}: {tipo}")


@dataclass
class ResultadoExecucao:
    rodada: uuid.UUID
    resultados: list[ResultadoCalendario] = field(default_factory=list)
    descartados_geo: int = 0
    descartados_horizonte: int = 0
    rodada_banco: ResumoRodada | None = None
    classificacao: ResumoClassificacao | None = None
    enriquecimento: ResumoEnriquecimento | None = None
    destinos: Counter[str] = field(default_factory=Counter)
    motivos: Counter[str] = field(default_factory=Counter)
    notificacao: ResumoNotificacao | None = None
    pendentes_pedidas: int = 0
    pendentes_sem_classificacao: int = 0
    pendentes_terminadas: int = 0
    pendentes_inativas: int = 0
    pendentes_sem_aviso: int = 0
    resumo_semanal_adiado: int = 0
    resumo_agora: bool = False
    resumo_semanal_pedidos: int = 0
    ja_em_resumo: int = 0
    marco_gravado: bool = False
    alertas: list[Alerta] = field(default_factory=list)
    alertas_envio: ResumoAlertas | None = None

    @property
    def codigo_saida(self) -> int:
        """3 se o Telegram recusou o bot (autenticação/proibido), nos eventos ou nos
        alertas; senão 0 (D21, D22)."""
        for resumo in (self.notificacao, self.alertas_envio):
            if resumo is not None and resumo.interrompido_por in INTERRUPCOES_GRAVES:
                return 3
        return 0


def proximo_resumo_provisorio(agora: datetime) -> datetime:
    """Próxima segunda-feira 00:00 em São Paulo, estritamente depois de `agora` (até o
    F1-10c ter o marco semanal)."""
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    local = agora.astimezone(FUSO_RESUMO)
    dias = (7 - local.weekday()) % 7 or 7
    return datetime.combine(local.date() + timedelta(days=dias), time(0), tzinfo=FUSO_RESUMO)


def semana_iso_sp(agora: datetime) -> tuple[int, int]:
    """(ano ISO, semana ISO) de `agora` em São Paulo."""
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    ano, semana, _ = agora.astimezone(FUSO_RESUMO).isocalendar()
    return ano, semana


def chave_marco_resumo(agora: datetime) -> str:
    ano, semana = semana_iso_sp(agora)
    return f"resumo_semanal:{ano}-W{semana:02d}"


def calcular_proximo_resumo(agora: datetime, *, resumo_agora: bool) -> datetime:
    """Próximo resumo para a decisão (D22): `agora` se o resumo sai nesta execução; senão a
    terça 00:00 de SP seguinte à próxima segunda 00:00 (cobre a segunda inteira, já que o
    resumo pode sair em qualquer execução da segunda)."""
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    if resumo_agora:
        return agora
    segunda = proximo_resumo_provisorio(agora)
    return datetime.combine(segunda.date() + timedelta(days=1), time(0), tzinfo=FUSO_RESUMO)


@contextmanager
def _etapa(nome: Etapa, sessao: Session | None = None) -> Iterator[None]:
    try:
        yield
    except ErroEtapa:
        if sessao is not None:
            sessao.rollback()
        raise
    except Exception as erro:
        if sessao is not None:
            sessao.rollback()
        tipo = type(erro).__name__
        raise ErroEtapa(nome, tipo) from None


def _comitado[T](sessao: Session, nome: Etapa, passo: Callable[[], T]) -> T:
    with _etapa(nome, sessao):
        valor = passo()
        sessao.commit()
    return valor


def _saidas(brutos: dict[int, dict]) -> dict[int, SaidaClassificador]:
    return {i: SaidaClassificador.model_validate(dados) for i, dados in brutos.items()}


def montar_pedidos(
    sessao: Session,
    *,
    slugs_comunidade: frozenset[str],
    agora: datetime,
    proximo_resumo: datetime,
    resultado: ResultadoExecucao,
    resumo_agora: bool = False,
) -> list[PedidoNotificacao]:
    """Pendentes ('enviando') primeiro, depois as decisões novas. Só leitura.

    Com `resumo_agora`, o destino resumo semanal gera pedido para quem ainda não foi avisado
    nem incluído num resumo (`resumo_semanal_pedidos`, só pedidos novos); senão é adiado."""
    with _etapa("decisao"):
        return _montar(sessao, slugs_comunidade, agora, proximo_resumo, resultado, resumo_agora)


def _montar(
    sessao: Session,
    slugs: frozenset[str],
    agora: datetime,
    proximo_resumo: datetime,
    resultado: ResultadoExecucao,
    resumo_agora: bool,
) -> list[PedidoNotificacao]:
    chave = {
        "versao_prompt": prompt.VERSAO_PROMPT,
        "modelo": modelos.MODELO,
        "versao_schema": modelos.VERSAO_SCHEMA,
    }
    pendentes = notificacoes_pendentes(sessao)
    eventos = leituras.eventos_para_decidir(sessao)
    por_id: dict[int, Evento] = leituras.eventos_por_id(sessao, [i for i, _ in pendentes])
    por_id.update({e.id: e for e in eventos})
    ids = sorted(por_id)
    saidas = _saidas(leituras.classificacoes_ok_atuais(sessao, ids, **chave))
    enriquecimentos: dict[int, Enriquecimento] = leituras.enriquecimentos_atuais(sessao, ids)
    enviadas = leituras.notificacoes_enviadas(sessao, ids)
    em_falha = {e.id for e in eventos_com_falha_classificacao(sessao, **chave)}

    pedidos: list[PedidoNotificacao] = []
    pedidas: set[tuple[int, str]] = set()
    em_resumo: set[int] = set()

    def ja_em_resumo(evento_id: int) -> bool:
        """Evento que já saiu num resumo semanal, ou cujo resumo pendente já foi pedido nesta
        execução, não recebe aviso (D22)."""
        chave_resumo = (evento_id, "resumo_semanal")
        if chave_resumo not in enviadas and chave_resumo not in pedidas:
            return False
        if evento_id not in em_resumo:
            em_resumo.add(evento_id)
            resultado.ja_em_resumo += 1
        return True

    def pedir(evento: Evento, tipo: str, saida: SaidaClassificador | None) -> None:
        pedidos.append(
            PedidoNotificacao(
                tipo=tipo,  # type: ignore[arg-type]
                evento=evento,
                saida=saida,
                enriquecimento=enriquecimentos.get(evento.id),
            )
        )
        pedidas.add((evento.id, tipo))

    # resumos pendentes primeiro: um aviso pendente do mesmo evento é bloqueado por eles
    pendentes = sorted(pendentes, key=lambda par: par[1] != "resumo_semanal")
    for evento_id, tipo in pendentes:
        evento = por_id.get(evento_id)
        if evento is None:
            continue
        if evento.estado not in leituras.ESTADOS_ATIVOS:
            resultado.pendentes_inativas += 1
            continue
        if calcular_termino(evento.inicio, evento.fim, evento.dia_inteiro) <= agora:
            resultado.pendentes_terminadas += 1
            continue
        saida = saidas.get(evento_id)
        if tipo == "aviso" and saida is None:
            # sem classificação ok atual: fica 'enviando' até haver uma
            resultado.pendentes_sem_classificacao += 1
            continue
        if tipo == "aviso":
            atual = decidir_destino(
                evento,
                saida,
                em_falha_classificacao=False,
                slugs_comunidade_ia=slugs,
                agora=agora,
                proximo_resumo=proximo_resumo,
            )
            if atual.destino != "aviso":
                # a decisão mudou (ex.: reclassificado): fica 'enviando', sem reenvio
                resultado.pendentes_sem_aviso += 1
                continue
            if ja_em_resumo(evento_id):
                continue
        pedir(evento, tipo, None if tipo == "aviso_excepcional" else saida)
        resultado.pendentes_pedidas += 1

    for evento in eventos:
        saida = saidas.get(evento.id)
        decisao = decidir_destino(
            evento,
            saida,
            em_falha_classificacao=saida is None and evento.id in em_falha,
            slugs_comunidade_ia=slugs,
            agora=agora,
            proximo_resumo=proximo_resumo,
        )
        resultado.destinos[decisao.destino] += 1
        resultado.motivos[decisao.motivo] += 1
        if decisao.destino == "resumo_semanal":
            if not resumo_agora:
                resultado.resumo_semanal_adiado += 1
                continue
            ja_saiu = any(
                (evento.id, tipo) in enviadas
                for tipo in ("aviso", "aviso_excepcional", "resumo_semanal")
            )
            if not ja_saiu and (evento.id, "resumo_semanal") not in pedidas:
                pedir(evento, "resumo_semanal", saida)
                resultado.resumo_semanal_pedidos += 1
            continue
        if decisao.destino not in ("aviso", "aviso_excepcional"):
            continue
        chave_envio = (evento.id, decisao.destino)
        if chave_envio in enviadas or chave_envio in pedidas:
            continue
        if decisao.destino == "aviso" and ja_em_resumo(evento.id):
            continue
        pedir(evento, decisao.destino, saida if decisao.destino == "aviso" else None)
    return pedidos


def executar(
    sessao: Session,
    *,
    config: Configuracao,
    calendarios: Sequence[CalendarioLuma],
    http: httpx.Client,
    llm: ClienteLLM,
    telegram: EnviadorMensagens,
    agora: datetime,
    rodada: uuid.UUID,
    proximo_resumo: datetime | None = None,
    ultimo_commit_em: datetime | None = None,
) -> ResultadoExecucao:
    """Uma execução completa. Um único cliente HTTP serve à coleta e ao enriquecimento.

    Depois dos eventos: marco do resumo semanal (se o resumo saiu sem interrupção) e, por
    último, os alertas (D22)."""
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    fuso = ZoneInfo(config.fuso_horario)
    slugs = slugs_comunidade_ia(calendarios)
    resultado = ResultadoExecucao(rodada=rodada)

    with _etapa("coleta"):
        resultado.resultados = coletar_luma(http, calendarios)
    with _etapa("filtros"):
        observacoes = [e for r in resultado.resultados for e in r.eventos]
        geografico = filtrar_por_localizacao(observacoes)
        horizonte = filtrar_por_horizonte(geografico.aprovados, agora)
        resultado.descartados_geo = len(geografico.descartados)
        resultado.descartados_horizonte = len(horizonte.descartados)

    resultado.rodada_banco = _comitado(
        sessao,
        "banco",
        lambda: registrar_rodada(
            sessao,
            rodada,
            resultado.resultados,
            horizonte.aprovados,
            agora,
            config.limite_ausencias_nao_encontrado,
        ),
    )
    resultado.classificacao = _comitado(
        sessao, "classificacao", lambda: classificar_pendentes(sessao, llm, config, agora, rodada)
    )
    resultado.enriquecimento = _comitado(
        sessao,
        "enriquecimento",
        lambda: enriquecer_precos(sessao, http, config, agora, rodada, slugs),
    )

    chave_marco = chave_marco_resumo(agora)
    with _etapa("decisao"):
        resultado.resumo_agora = not leituras.marco_resumo_existe(sessao, chave_marco)
    proximo = proximo_resumo or calcular_proximo_resumo(agora, resumo_agora=resultado.resumo_agora)
    pedidos = montar_pedidos(
        sessao,
        slugs_comunidade=slugs,
        agora=agora,
        proximo_resumo=proximo,
        resultado=resultado,
        resumo_agora=resultado.resumo_agora,
    )
    with _etapa("notificacao", sessao):
        notificacao = notificar(sessao, telegram, pedidos, agora, fuso)
        resultado.notificacao = notificacao
        if resultado.resumo_agora and notificacao.interrompido_por is None:
            # marco também com resumo vazio; commitado antes dos alertas
            sessao.add(
                EnvioSemEvento(
                    chave=chave_marco,
                    tipo="resumo_semanal",
                    estado="enviado",
                    criado_em=agora,
                    enviado_em=agora,
                )
            )
            sessao.commit()
            resultado.marco_gravado = True

    with _etapa("alertas", sessao):
        resultado.alertas = detectar_alertas(
            sessao, resultado, calendarios, agora, ultimo_commit_em
        )
        if notificacao.interrompido_por in INTERRUPCOES_SEM_ALERTA:
            resultado.alertas_envio = ResumoAlertas(nao_enviados=list(resultado.alertas))
        else:
            resultado.alertas_envio = enviar_alertas(sessao, telegram, resultado.alertas, agora)
    return resultado


def _linhas_contador(prefixo: str, contador: Counter[str]) -> list[str]:
    return [f"{prefixo}_{chave}={valor}" for chave, valor in sorted(contador.items())]


def linhas_resumo(resultado: ResultadoExecucao) -> list[str]:
    """Resumo `chave=valor` só com contagens, códigos, slugs e o uuid da rodada."""
    linhas = [f"rodada={resultado.rodada}"]
    situacoes = Counter(r.situacao for r in resultado.resultados)
    linhas += _linhas_contador("calendarios", situacoes)
    for r in resultado.resultados:
        if r.falha is not None:
            linhas.append(f"falha_{r.calendario}={r.falha.motivo}")
    linhas.append(f"eventos_coletados={sum(len(r.eventos) for r in resultado.resultados)}")
    linhas.append(f"descartados_geo={resultado.descartados_geo}")
    linhas.append(f"descartados_horizonte={resultado.descartados_horizonte}")
    banco = resultado.rodada_banco
    if banco is not None:
        for nome in ("novos", "alterados", "cancelados", "encerrados", "nao_encontrados"):
            linhas.append(f"eventos_{nome}={len(getattr(banco, nome))}")
    c = resultado.classificacao
    if c is not None:
        linhas += [
            f"classificacao_chamadas={c.chamadas}",
            f"classificacao_ok={len(c.ok)}",
            f"classificacao_erros={len(c.erros)}",
            f"classificacao_interrompida={c.interrompido_por or 'nao'}",
            f"classificacao_restantes={'sim' if c.restantes else 'nao'}",
            f"classificacao_custo_estimado_usd={c.custo_total_usd}",
        ]
        linhas += _linhas_contador("classificacao_erro", Counter(m for _, m in c.erros))
    e = resultado.enriquecimento
    if e is not None:
        linhas += [
            f"enriquecimento_paginas={e.paginas_buscadas}",
            f"enriquecimento_gravados={len(e.gravados)}",
            f"enriquecimento_erros={len(e.erros)}",
            f"enriquecimento_robots_bloqueado={'sim' if e.robots_bloqueado else 'nao'}",
        ]
    linhas += _linhas_contador("destino", resultado.destinos)
    linhas += _linhas_contador("motivo", resultado.motivos)
    linhas += [
        f"pendentes_pedidas={resultado.pendentes_pedidas}",
        f"pendentes_sem_classificacao={resultado.pendentes_sem_classificacao}",
        f"pendentes_terminadas={resultado.pendentes_terminadas}",
        f"pendentes_inativas={resultado.pendentes_inativas}",
        f"pendentes_sem_aviso={resultado.pendentes_sem_aviso}",
        f"resumo_semanal_adiado={resultado.resumo_semanal_adiado}",
        f"resumo_semanal_agora={'sim' if resultado.resumo_agora else 'nao'}",
        f"resumo_semanal_pedidos={resultado.resumo_semanal_pedidos}",
        f"ja_em_resumo={resultado.ja_em_resumo}",
        f"resumo_semanal_marco={'sim' if resultado.marco_gravado else 'nao'}",
        f"alertas_detectados={len(resultado.alertas)}",
    ]
    linhas += _linhas_contador("alerta", Counter(a.tipo for a in resultado.alertas))
    a = resultado.alertas_envio
    linhas += [
        f"alertas_enviados={0 if a is None else len(a.enviados)}",
        f"alertas_ja_enviados={0 if a is None else len(a.ja_enviados)}",
        f"alertas_falhas={0 if a is None else len(a.falhas)}",
        f"alertas_nao_enviados={0 if a is None else len(a.nao_enviados)}",
        f"alertas_interrompidos={(a.interrompido_por if a else None) or 'nao'}",
    ]
    n = resultado.notificacao
    if n is not None:
        linhas += [
            f"notificacao_enviadas={len(n.enviados)}",
            f"notificacao_ja_enviadas={len(n.ja_enviados)}",
            f"notificacao_falhas={len(n.falhas)}",
            f"notificacao_interrompida={n.interrompido_por or 'nao'}",
        ]
    linhas.append(f"codigo_saida={resultado.codigo_saida}")
    return linhas
