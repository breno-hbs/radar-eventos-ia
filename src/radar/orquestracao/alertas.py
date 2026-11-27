"""Detecção dos alertas de uma execução (D20, seção F1-10c; D22). Sem envio nem escrita."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from radar.banco.leituras import calendarios_vazios_seguidos
from radar.classificacao.classificador import ResumoClassificacao
from radar.enriquecimento.preco import ResumoEnriquecimento
from radar.fontes.calendarios import CalendarioLuma
from radar.fontes.modelos import ResultadoCalendario
from radar.notificacao.alertas import Alerta

if TYPE_CHECKING:
    from radar.orquestracao.executar import ResultadoExecucao

VAZIAS_SEGUIDAS = 6
DIAS_INATIVIDADE = 50
MOTIVOS_SISTEMATICOS = frozenset(
    {
        "status_http",
        "rede",
        "redirecionamento",
        "sem_json_ld",
        "json_ld_invalido",
        "bloqueado_robots",
    }
)
MIN_ERROS_SISTEMATICOS = 3
FONTE = "luma"
ALVO_ENRIQUECIMENTO = "luma.com"
ALVO_CLASSIFICADOR = "anthropic"
ALVO_REPOSITORIO = "repositorio"


def alertas_coleta(resultados: Sequence[ResultadoCalendario]) -> list[Alerta]:
    """Falha de extração por calendário; eventos inválidos contam como falha (D22)."""
    alertas: list[Alerta] = []
    for r in resultados:
        if r.falha is not None:
            codigo = r.falha.motivo
            if r.falha.status_http is not None:
                codigo = f"{codigo} (HTTP {r.falha.status_http})"
            alertas.append(Alerta(tipo="falha_extracao", alvo=r.calendario, codigo=codigo))
        elif r.eventos_invalidos:
            alertas.append(
                Alerta(
                    tipo="falha_extracao",
                    alvo=r.calendario,
                    codigo="eventos_invalidos",
                    quantidade=len(r.eventos_invalidos),
                )
            )
    return alertas


def alertas_fonte_vazia(slugs: Sequence[str]) -> list[Alerta]:
    return [Alerta(tipo="fonte_vazia", alvo=slug, quantidade=VAZIAS_SEGUIDAS) for slug in slugs]


def alertas_enriquecimento(resumo: ResumoEnriquecimento | None) -> list[Alerta]:
    if resumo is None:
        return []
    if resumo.robots_bloqueado:
        return [Alerta(tipo="robots_bloqueado", alvo=ALVO_ENRIQUECIMENTO)]
    sistematicos = sum(1 for _, motivo in resumo.erros if motivo in MOTIVOS_SISTEMATICOS)
    conclusivos = sum(1 for _, estado in resumo.gravados if estado != "preco_desconhecido")
    if sistematicos >= MIN_ERROS_SISTEMATICOS and conclusivos == 0:
        return [
            Alerta(
                tipo="enriquecimento_sistematico",
                alvo=ALVO_ENRIQUECIMENTO,
                quantidade=sistematicos,
            )
        ]
    return []


# limite_tokens não alerta: é o orçamento por execução funcionando (D22).
INTERRUPCOES_DO_CLASSIFICADOR = frozenset({"autenticacao", "rate_limit", "falhas_api_seguidas"})


def alertas_classificacao(resumo: ResumoClassificacao | None) -> list[Alerta]:
    if resumo is None or resumo.interrompido_por not in INTERRUPCOES_DO_CLASSIFICADOR:
        return []
    return [
        Alerta(
            tipo="classificador_interrompido",
            alvo=ALVO_CLASSIFICADOR,
            codigo=resumo.interrompido_por,
        )
    ]


def alerta_inatividade(ultimo_commit_em: datetime | None, agora: datetime) -> Alerta | None:
    """Alerta a partir de 50 dias sem commit na main (o GitHub desliga aos 60)."""
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    if ultimo_commit_em is None:
        return None
    if ultimo_commit_em.tzinfo is None or ultimo_commit_em.utcoffset() is None:
        raise ValueError("ultimo_commit_em precisa ter fuso horário")
    passado = agora - ultimo_commit_em
    if passado < timedelta(days=DIAS_INATIVIDADE):
        return None
    return Alerta(tipo="inatividade", alvo=ALVO_REPOSITORIO, quantidade=passado.days)


def detectar_alertas(
    sessao: Session,
    resultado: ResultadoExecucao,
    calendarios: Sequence[CalendarioLuma],
    agora: datetime,
    ultimo_commit_em: datetime | None,
) -> list[Alerta]:
    """Todos os alertas da execução, na ordem coleta → fontes vazias → enriquecimento →
    classificação → inatividade. Só leitura."""
    vazias = calendarios_vazios_seguidos(
        sessao, FONTE, [c.slug for c in calendarios], VAZIAS_SEGUIDAS
    )
    alertas = [
        *alertas_coleta(resultado.resultados),
        *alertas_fonte_vazia(vazias),
        *alertas_enriquecimento(resultado.enriquecimento),
        *alertas_classificacao(resultado.classificacao),
    ]
    inatividade = alerta_inatividade(ultimo_commit_em, agora)
    if inatividade is not None:
        alertas.append(inatividade)
    return alertas
