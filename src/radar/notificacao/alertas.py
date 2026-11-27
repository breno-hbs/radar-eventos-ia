"""Alertas sem evento (D22): formato e envio idempotente por (tipo, alvo, dia de SP).

Mesmo padrão da D19: 'enviando' + commit numa sessão própria → envio → 'enviado' + commit.
A chave inclui a data de São Paulo, então cada condição gera no máximo um alerta por dia e
alertas de dias anteriores nunca são reenviados. O texto é puro e só tem nomes fixos, slug,
código e contagem — nada de conteúdo de evento nem URL.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.banco.modelos import EnvioSemEvento
from radar.notificacao.envio import exigir_transacao_limpa, sessao_propria
from radar.notificacao.telegram import EnviadorMensagens, ErroTelegram

TipoAlerta = Literal[
    "falha_extracao",
    "fonte_vazia",
    "inatividade",
    "robots_bloqueado",
    "enriquecimento_sistematico",
    "classificador_interrompido",
]
FUSO_DIA = ZoneInfo("America/Sao_Paulo")
PREFIXO = "Alerta do radar"
CATEGORIAS_QUE_INTERROMPEM = ("autenticacao", "proibido", "rate_limit")
MAX_FALHAS_SEGUIDAS = 3

_TITULOS: dict[str, str] = {
    "falha_extracao": "falha de extração",
    "fonte_vazia": "fonte sem eventos",
    "inatividade": "repositório inativo",
    "robots_bloqueado": "robots.txt indisponível",
    "enriquecimento_sistematico": "erros sistemáticos no preço",
    "classificador_interrompido": "classificação interrompida",
}


class Alerta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tipo: TipoAlerta
    alvo: str
    codigo: str | None = None
    quantidade: int | None = None


@dataclass
class ResumoAlertas:
    enviados: list[Alerta] = field(default_factory=list)
    ja_enviados: list[Alerta] = field(default_factory=list)
    falhas: list[tuple[Alerta, str]] = field(default_factory=list)
    nao_enviados: list[Alerta] = field(default_factory=list)
    interrompido_por: str | None = None


def _com_fuso(agora: datetime) -> None:
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")


def chave_alerta(alerta: Alerta, agora: datetime) -> str:
    _com_fuso(agora)
    dia = agora.astimezone(FUSO_DIA).date().isoformat()
    return f"{alerta.tipo}:{alerta.alvo}:{dia}"


def _detalhe(alerta: Alerta) -> list[str]:
    q = alerta.quantidade
    if alerta.tipo == "falha_extracao":
        if alerta.codigo == "eventos_invalidos":
            return [f"Calendário {alerta.alvo}: {q} eventos inválidos (eventos_invalidos)."]
        return [f"Calendário {alerta.alvo}: {alerta.codigo or 'falha'}."]
    if alerta.tipo == "fonte_vazia":
        vezes = f"{q} execuções seguidas" if q is not None else "várias execuções seguidas"
        return [f"Calendário {alerta.alvo} sem eventos em {vezes}, depois de já ter tido."]
    if alerta.tipo == "inatividade":
        dias = f"há {q} dias" if q is not None else "há muito tempo"
        return [
            f"Último commit na main {dias}.",
            "O GitHub desativa workflows agendados após 60 dias sem atividade.",
            "Reativação: siga o procedimento do README.",
        ]
    if alerta.tipo == "robots_bloqueado":
        return [f"robots.txt de {alerta.alvo} ilegível: preços não foram lidos nesta execução."]
    if alerta.tipo == "enriquecimento_sistematico":
        return [f"{q} páginas de {alerta.alvo} com erro e nenhum preço conclusivo."]
    return [f"Serviço {alerta.alvo}: {alerta.codigo or 'interrompido'}."]


def formatar_alerta(alerta: Alerta) -> str:
    return "\n".join([f"{PREFIXO}: {_TITULOS[alerta.tipo]}", *_detalhe(alerta)])


def enviar_alertas(
    sessao: Session,
    cliente: EnviadorMensagens,
    alertas: Sequence[Alerta],
    agora: datetime,
) -> ResumoAlertas:
    """Envia cada alerta no máximo uma vez por dia de SP (D22)."""
    _com_fuso(agora)
    exigir_transacao_limpa(sessao)
    resumo = ResumoAlertas()
    unicos: dict[str, Alerta] = {}
    for alerta in alertas:
        unicos.setdefault(chave_alerta(alerta, agora), alerta)
    if not unicos:
        return resumo

    with sessao_propria(sessao) as propria:
        existentes = {
            linha.chave: linha
            for linha in propria.scalars(
                select(EnvioSemEvento).where(EnvioSemEvento.chave.in_(list(unicos)))
            )
        }
        falhas_seguidas = 0
        for chave, alerta in unicos.items():
            linha = existentes.get(chave)
            if linha is not None and linha.estado == "enviado":
                resumo.ja_enviados.append(alerta)
                continue
            if resumo.interrompido_por is not None:
                resumo.nao_enviados.append(alerta)
                continue
            if linha is None:
                linha = EnvioSemEvento(
                    chave=chave, tipo=alerta.tipo, estado="enviando", criado_em=agora
                )
                propria.add(linha)
            propria.commit()
            try:
                cliente.enviar_texto(formatar_alerta(alerta))
            except ErroTelegram as erro:
                resumo.falhas.append((alerta, erro.categoria))
                falhas_seguidas += 1
                if erro.categoria in CATEGORIAS_QUE_INTERROMPEM:
                    resumo.interrompido_por = erro.categoria
                elif falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
                    resumo.interrompido_por = "falhas_seguidas"
                continue
            linha.estado = "enviado"
            linha.enviado_em = agora
            propria.commit()
            resumo.enviados.append(alerta)
            falhas_seguidas = 0
    return resumo
