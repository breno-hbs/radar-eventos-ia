"""Classificação dos eventos pendentes com LLM (D13).

Uma chamada por evento, sem retries. Cada chamada vira uma linha em `classificacoes`
(`ok` ou `erro`); a função só faz `flush` — quem chama decide o commit.

O `motivo_erro` é um código fechado (`saida_*`/`api_*`) com, no máximo, o nome de um campo
do modelo: nunca texto do evento nem da resposta do LLM.

A chave (prompt, modelo, schema) e a tabela de preços são lidas dos módulos na hora da
chamada, para que trocar a versão mude a chave sem reimportar este módulo.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from radar.banco import repositorio
from radar.banco.modelos import Classificacao, Evento
from radar.banco.repositorio import MAX_ERROS_SAIDA
from radar.classificacao import modelos, precos, prompt
from radar.classificacao.cliente import ClienteLLM, ErroAPI, RespostaLLM
from radar.classificacao.modelos import SaidaClassificador
from radar.config import Configuracao

MAX_TOKENS_SAIDA = 1024
MAX_FALHAS_API_SEGUIDAS = 3
CATEGORIAS_QUE_INTERROMPEM = ("autenticacao", "rate_limit")
CAMPOS_TRECHO = (
    "trecho_modalidade",
    "trecho_idioma",
    "trecho_preco",
    "trecho_localizacao",
    "trecho_evidencia",
)

Interrupcao = Literal["autenticacao", "rate_limit", "falhas_api_seguidas", "limite_tokens"]

_ESPACOS = re.compile(r"\s+")
_RETICENCIAS = ("...", "…")


@dataclass
class ResumoClassificacao:
    chamadas: int = 0
    ok: list[int] = field(default_factory=list)
    erros: list[tuple[int, str]] = field(default_factory=list)
    interrompido_por: Interrupcao | None = None
    # Há eventos da fila que não foram tentados nesta execução.
    restantes: bool = False
    custo_total_usd: Decimal = Decimal("0")


def _normalizar(texto: str) -> str:
    return _ESPACOS.sub(" ", unicodedata.normalize("NFC", texto)).strip().casefold()


def _sem_reticencias_nas_pontas(trecho: str) -> str:
    anterior = None
    while anterior != trecho:
        anterior = trecho
        trecho = trecho.strip()
        for marca in _RETICENCIAS:
            trecho = trecho.removeprefix(marca).removesuffix(marca)
    return trecho


def validar_contra_evento(
    saida: SaidaClassificador, texto_fonte: str, localizacao_filtro: str
) -> str | None:
    """None se a saída é coerente com o evento; senão o motivo do erro (código fechado)."""
    if localizacao_filtro == "indefinida":
        if saida.localizacao == "nao_se_aplica":
            return "saida_localizacao_ausente"
    elif saida.localizacao != "nao_se_aplica":
        # O filtro já decidiu a localização; o LLM não pode contradizê-lo.
        return "saida_localizacao_indevida"
    fonte = _normalizar(texto_fonte)
    for campo in CAMPOS_TRECHO:
        trecho: str = getattr(saida, campo)
        if not trecho:
            continue
        procurado = _sem_reticencias_nas_pontas(_normalizar(trecho))
        if not procurado or procurado not in fonte:
            return f"saida_evidencia_nao_encontrada:{campo}"
    return None


def _motivo_schema(erro: ValidationError) -> str:
    """Só nomes de campos do modelo; chaves extras inventadas pelo LLM nunca entram."""
    for detalhe in erro.errors():
        local = detalhe.get("loc", ())
        if local and local[0] in SaidaClassificador.model_fields:
            return f"saida_schema_invalido:{local[0]}"
    return "saida_schema_invalido"


def _interpretar(
    resposta: RespostaLLM, texto_fonte: str, localizacao_filtro: str
) -> tuple[SaidaClassificador | None, str | None]:
    if resposta.stop_reason == "max_tokens":
        return None, "saida_truncada"
    if resposta.stop_reason != "end_turn":
        return None, "saida_parada_inesperada"
    try:
        dados = json.loads(resposta.texto)
    except (json.JSONDecodeError, ValueError):
        return None, "saida_json_invalido"
    if not isinstance(dados, dict):
        return None, "saida_schema_invalido"
    try:
        saida = SaidaClassificador.model_validate(dados, strict=True)
    except ValidationError as erro:
        return None, _motivo_schema(erro)
    motivo = validar_contra_evento(saida, texto_fonte, localizacao_filtro)
    if motivo is not None:
        return None, motivo
    return saida, None


def classificar_pendentes(
    sessao: Session,
    cliente: ClienteLLM,
    config: Configuracao,
    agora: datetime,
    rodada: uuid.UUID,
    relogio: Callable[[], float] = time.monotonic,
) -> ResumoClassificacao:
    """Classifica a fila (por início) até o limite de chamadas ou uma interrupção."""
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    versao_prompt = prompt.VERSAO_PROMPT
    modelo = modelos.MODELO
    versao_schema = modelos.VERSAO_SCHEMA
    if modelo not in precos.TABELA_PRECOS:
        raise ValueError(f"modelo sem preço na tabela: {modelo!r}")
    system = prompt.carregar_prompt()
    schema = modelos.SCHEMA_SAIDA
    limite = config.limite_chamadas_llm_por_execucao

    # Um a mais que o limite, só para saber se sobram pendentes.
    fila = repositorio.eventos_para_classificar(
        sessao,
        versao_prompt,
        modelo,
        versao_schema,
        limite=limite + 1,
        max_erros_saida=MAX_ERROS_SAIDA,
    )
    resumo = ResumoClassificacao()
    tokens_entrada = 0
    tokens_saida = 0
    falhas_api_seguidas = 0
    tentados = 0

    def registrar(evento: Evento, **campos: object) -> None:
        sessao.add(
            Classificacao(
                evento_id=evento.id,
                rodada=rodada,
                criada_em=agora,
                hash_conteudo=evento.hash_conteudo,
                versao_prompt=versao_prompt,
                modelo=modelo,
                versao_schema=versao_schema,
                **campos,
            )
        )
        sessao.flush()

    for evento in fila:
        if resumo.chamadas >= limite:
            break
        if (
            tokens_entrada >= config.limite_tokens_entrada_por_execucao
            or tokens_saida >= config.limite_tokens_saida_por_execucao
        ):
            resumo.interrompido_por = "limite_tokens"
            break

        localizacao_filtro = evento.localizacao
        mensagem, texto_fonte = prompt.montar_mensagem(evento, localizacao_filtro)
        tentados += 1
        resumo.chamadas += 1
        inicio = relogio()
        try:
            resposta = cliente.classificar(
                system=system, mensagem=mensagem, schema=schema, max_tokens=MAX_TOKENS_SAIDA
            )
        except ErroAPI as erro:
            latencia_ms = round((relogio() - inicio) * 1000)
            motivo = f"api_{erro.categoria}"
            registrar(
                evento,
                situacao="erro",
                resultado=None,
                motivo_erro=motivo,
                tokens_entrada=None,
                tokens_saida=None,
                latencia_ms=latencia_ms,
                custo_usd=None,
            )
            resumo.erros.append((evento.id, motivo))
            falhas_api_seguidas += 1
            if erro.categoria in CATEGORIAS_QUE_INTERROMPEM:
                resumo.interrompido_por = erro.categoria  # type: ignore[assignment]
                break
            if falhas_api_seguidas >= MAX_FALHAS_API_SEGUIDAS:
                resumo.interrompido_por = "falhas_api_seguidas"
                break
            continue

        latencia_ms = round((relogio() - inicio) * 1000)
        falhas_api_seguidas = 0
        tokens_entrada += resposta.tokens_entrada or 0
        tokens_saida += resposta.tokens_saida or 0
        custo: Decimal | None = None
        if resposta.tokens_entrada is not None or resposta.tokens_saida is not None:
            custo = precos.calcular_custo(
                modelo, resposta.tokens_entrada or 0, resposta.tokens_saida or 0
            )
            resumo.custo_total_usd += custo

        saida, motivo_erro = _interpretar(resposta, texto_fonte, localizacao_filtro)
        registrar(
            evento,
            situacao="ok" if saida is not None else "erro",
            resultado=None if saida is None else saida.model_dump(mode="json"),
            motivo_erro=motivo_erro,
            tokens_entrada=resposta.tokens_entrada,
            tokens_saida=resposta.tokens_saida,
            latencia_ms=latencia_ms,
            custo_usd=custo,
        )
        if saida is not None:
            resumo.ok.append(evento.id)
        else:
            resumo.erros.append((evento.id, motivo_erro or "saida_invalida"))

    resumo.restantes = len(fila) > tentados
    return resumo
