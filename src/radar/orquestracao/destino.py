"""Decisão de destino de um evento (D20): função pura, sem banco nem rede.

A decisão é recalculada a cada execução e nunca gravada: um evento que hoje "aguarda" (além
do horizonte do seu tipo) vira aviso quando entra no horizonte. Regras, nesta ordem:

1. pré-condições: `agora`/`proximo_resumo` com fuso; falha de classificação sem saída;
2. estado diferente de ativo/alterado → descartar (`estado_inativo`);
3. filtro geográfico `fora` → descartar (`fora_de_sp`);
4. já terminou (`calcular_termino` ≤ agora) → descartar (`terminou`);
5. falha de classificação: horizonte de 30 dias; antes do próximo resumo → aviso excepcional,
   senão resumo semanal;
6. sem classificação ok → aguardar;
7. não IA → descartar, salvo calendário de comunidade de IA (D15; só com classificação ok);
8. filtro `indefinida` e LLM disse `fora` → descartar;
9. além do horizonte do tipo (conferência/hackathon 90 dias, demais 30; limite inclusivo) →
   aguardar;
10. idioma (D13): pt, bilíngue ou com tradução → aviso; desconhecido → aviso com etiqueta;
    estrangeiro confirmado → descartado se exclusivamente online, senão resumo semanal.

Preço nunca muda o destino (só aparece na mensagem).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from radar.banco.modelos import Evento
from radar.classificacao.modelos import Modalidade, SaidaClassificador, TipoEvento
from radar.filtros.horizonte import calcular_termino
from radar.fontes.calendarios import CalendarioLuma

Destino = Literal["aviso", "aviso_excepcional", "resumo_semanal", "aguardar", "descartar"]
MotivoDestino = Literal[
    "estado_inativo",
    "fora_de_sp",
    "terminou",
    "sem_classificacao",
    "nao_ia",
    "localizacao_llm_fora",
    "alem_do_horizonte_do_tipo",
    "idioma_ok",
    "idioma_nao_confirmado",
    "estrangeiro_presencial",
    "estrangeiro_online",
    "falha_classificacao",
    "falha_antes_do_resumo",
]

HORIZONTE_DIAS_LONGO = 90
HORIZONTE_DIAS_PADRAO = 30
TIPOS_LONGOS: frozenset[str] = frozenset({"conferencia", "hackathon"})
ESTADOS_ATIVOS = ("ativo", "alterado")
FILTRO_INDEFINIDO = "indefinida"


class Decisao(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    destino: Destino
    motivo: MotivoDestino
    # O LLM disse não IA e o calendário de comunidade de IA mudou a decisão (D15).
    e_ia_por_comunidade: bool = False


def horizonte_do_tipo(tipo: TipoEvento | None) -> int:
    return HORIZONTE_DIAS_LONGO if tipo in TIPOS_LONGOS else HORIZONTE_DIAS_PADRAO


def e_comunidade_ia(calendarios: Iterable[str], slugs: frozenset[str]) -> bool:
    return any(slug in slugs for slug in calendarios)


def slugs_comunidade_ia(calendarios: Sequence[CalendarioLuma]) -> frozenset[str]:
    return frozenset(c.slug for c in calendarios if c.comunidade_ia)


def localizacao_efetiva(filtro: str, saida: SaidaClassificador | None) -> str:
    """O filtro geográfico vale quando decidiu; com `indefinida`, vale o que o LLM afirmou."""
    if filtro != FILTRO_INDEFINIDO or saida is None:
        return filtro
    if saida.localizacao in ("sp", "online", "fora"):
        return saida.localizacao
    return FILTRO_INDEFINIDO


def exclusivamente_online(local_efetivo: str, modalidade: Modalidade | str) -> bool:
    """Só com evidência: local online sem modalidade presencial/híbrida, ou local indefinido
    com modalidade online declarada. Na dúvida, não é exclusivamente online."""
    if local_efetivo == "online":
        return modalidade in ("online", "desconhecida")
    if local_efetivo == FILTRO_INDEFINIDO:
        return modalidade == "online"
    return False


def _com_fuso(momento: datetime) -> bool:
    return momento.tzinfo is not None and momento.utcoffset() is not None


def _alem(evento: Evento, agora: datetime, dias: int) -> bool:
    return evento.inicio > agora + timedelta(days=dias)


def decidir_destino(
    evento: Evento,
    saida: SaidaClassificador | None,
    *,
    em_falha_classificacao: bool,
    slugs_comunidade_ia: frozenset[str],
    agora: datetime,
    proximo_resumo: datetime,
) -> Decisao:
    if not _com_fuso(agora) or not _com_fuso(proximo_resumo):
        raise ValueError("agora e proximo_resumo precisam ter fuso horário")
    if em_falha_classificacao and saida is not None:
        raise ValueError("evento em falha de classificação não tem saída ok")

    if evento.estado not in ESTADOS_ATIVOS:
        return Decisao(destino="descartar", motivo="estado_inativo")
    if evento.localizacao == "fora":
        return Decisao(destino="descartar", motivo="fora_de_sp")
    if calcular_termino(evento.inicio, evento.fim, evento.dia_inteiro) <= agora:
        return Decisao(destino="descartar", motivo="terminou")

    if em_falha_classificacao:
        if _alem(evento, agora, HORIZONTE_DIAS_PADRAO):
            return Decisao(destino="aguardar", motivo="alem_do_horizonte_do_tipo")
        if evento.inicio < proximo_resumo:
            return Decisao(destino="aviso_excepcional", motivo="falha_antes_do_resumo")
        return Decisao(destino="resumo_semanal", motivo="falha_classificacao")

    if saida is None:
        return Decisao(destino="aguardar", motivo="sem_classificacao")

    comunidade = e_comunidade_ia(evento.calendarios or (), slugs_comunidade_ia)
    if not saida.e_ia and not comunidade:
        return Decisao(destino="descartar", motivo="nao_ia")
    flag = not saida.e_ia  # só chega aqui com não IA se for comunidade

    if evento.localizacao == FILTRO_INDEFINIDO and saida.localizacao == "fora":
        return Decisao(destino="descartar", motivo="localizacao_llm_fora", e_ia_por_comunidade=flag)

    if _alem(evento, agora, horizonte_do_tipo(saida.tipo_evento)):
        return Decisao(
            destino="aguardar", motivo="alem_do_horizonte_do_tipo", e_ia_por_comunidade=flag
        )

    if saida.tem_traducao or saida.idioma in ("pt", "bilingue"):
        return Decisao(destino="aviso", motivo="idioma_ok", e_ia_por_comunidade=flag)
    if saida.idioma == "desconhecido":
        return Decisao(destino="aviso", motivo="idioma_nao_confirmado", e_ia_por_comunidade=flag)
    local = localizacao_efetiva(evento.localizacao, saida)
    if exclusivamente_online(local, saida.modalidade):
        return Decisao(destino="descartar", motivo="estrangeiro_online", e_ia_por_comunidade=flag)
    return Decisao(
        destino="resumo_semanal", motivo="estrangeiro_presencial", e_ia_por_comunidade=flag
    )
