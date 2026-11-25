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


@dataclass(frozen=True)
class ResultadoEvento:
    """Resultado de uma chamada para um evento (sem banco).

    Erro de API não é levantado: vira `erro_api` e `motivo_erro="api_<categoria>"`, sem tokens
    nem custo. Erro de saída traz `motivo_erro`, tokens e custo.

    `texto_resposta` é o texto bruto do LLM: só em memória (diagnóstico da avaliação, D15),
    nunca gravado no banco nem em log. `e_ia_por_comunidade` = o LLM disse não IA e a
    comunidade do calendário virou o resultado para IA.
    """

    saida: SaidaClassificador | None
    motivo_erro: str | None
    tokens_entrada: int | None
    tokens_saida: int | None
    custo_usd: Decimal | None
    erro_api: ErroAPI | None
    texto_resposta: str | None = field(default=None, repr=False)
    stop_reason: str | None = None
    e_ia_por_comunidade: bool = False


def classificar_evento(
    evento: Evento, localizacao_filtro: str, cliente: ClienteLLM, *, comunidade_ia: bool = False
) -> ResultadoEvento:
    """Uma chamada ao LLM para `evento`, conferindo a localização contra `localizacao_filtro`.

    Usada pelo radar (`classificar_pendentes`) e pela avaliação (F1-07); não toca no banco.
    Com `comunidade_ia` (D15), uma saída VÁLIDA passa a `e_ia=True`; erro continua erro. Só a
    avaliação usa esse parâmetro; no radar a regra é aplicada na leitura (F1-10).
    """
    modelo = modelos.MODELO
    mensagem, texto_fonte = prompt.montar_mensagem(evento, localizacao_filtro)
    try:
        resposta = cliente.classificar(
            system=prompt.carregar_prompt(),
            mensagem=mensagem,
            schema=modelos.SCHEMA_SAIDA,
            max_tokens=MAX_TOKENS_SAIDA,
        )
    except ErroAPI as erro:
        return ResultadoEvento(
            saida=None,
            motivo_erro=f"api_{erro.categoria}",
            tokens_entrada=None,
            tokens_saida=None,
            custo_usd=None,
            erro_api=erro,
        )
    custo: Decimal | None = None
    if resposta.tokens_entrada is not None or resposta.tokens_saida is not None:
        custo = precos.calcular_custo(
            modelo, resposta.tokens_entrada or 0, resposta.tokens_saida or 0
        )
    saida, motivo_erro = _interpretar(resposta, texto_fonte, localizacao_filtro)
    por_comunidade = False
    if saida is not None and comunidade_ia and not saida.e_ia:
        saida = saida.model_copy(update={"e_ia": True})
        por_comunidade = True
    return ResultadoEvento(
        saida=saida,
        motivo_erro=motivo_erro,
        tokens_entrada=resposta.tokens_entrada,
        tokens_saida=resposta.tokens_saida,
        custo_usd=custo,
        erro_api=None,
        texto_resposta=resposta.texto,
        stop_reason=resposta.stop_reason,
        e_ia_por_comunidade=por_comunidade,
    )


def classificar_pendentes(
    sessao: Session,
    cliente: ClienteLLM,
    config: Configuracao,
    agora: datetime,
    rodada: uuid.UUID,
    relogio: Callable[[], float] = time.monotonic,
) -> ResumoClassificacao:
    """Classifica a fila (por início) até o limite de chamadas ou uma interrupção.

    Grava a saída do LLM sem alteração: a regra de comunidade de IA (D15) não é gravada, é
    aplicada na leitura/decisão do aviso (F1-10). O texto bruto do LLM nunca é gravado.
    """
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    versao_prompt = prompt.VERSAO_PROMPT
    modelo = modelos.MODELO
    versao_schema = modelos.VERSAO_SCHEMA
    if modelo not in precos.TABELA_PRECOS:
        raise ValueError(f"modelo sem preço na tabela: {modelo!r}")
    prompt.carregar_prompt()  # prompt alterado sem nova versão falha antes de consultar a fila
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

    for evento in fila:
        if resumo.chamadas >= limite:
            break
        if (
            tokens_entrada >= config.limite_tokens_entrada_por_execucao
            or tokens_saida >= config.limite_tokens_saida_por_execucao
        ):
            resumo.interrompido_por = "limite_tokens"
            break

        tentados += 1
        resumo.chamadas += 1
        inicio = relogio()
        resultado = classificar_evento(evento, evento.localizacao, cliente)
        latencia_ms = round((relogio() - inicio) * 1000)

        sessao.add(
            Classificacao(
                evento_id=evento.id,
                rodada=rodada,
                criada_em=agora,
                hash_conteudo=evento.hash_conteudo,
                versao_prompt=versao_prompt,
                modelo=modelo,
                versao_schema=versao_schema,
                situacao="ok" if resultado.saida is not None else "erro",
                resultado=None
                if resultado.saida is None
                else resultado.saida.model_dump(mode="json"),
                motivo_erro=resultado.motivo_erro,
                tokens_entrada=resultado.tokens_entrada,
                tokens_saida=resultado.tokens_saida,
                latencia_ms=latencia_ms,
                custo_usd=resultado.custo_usd,
            )
        )
        sessao.flush()
        tokens_entrada += resultado.tokens_entrada or 0
        tokens_saida += resultado.tokens_saida or 0
        if resultado.custo_usd is not None:
            resumo.custo_total_usd += resultado.custo_usd

        if resultado.saida is not None:
            resumo.ok.append(evento.id)
        else:
            resumo.erros.append((evento.id, resultado.motivo_erro or "saida_invalida"))

        if resultado.erro_api is None:
            falhas_api_seguidas = 0
            continue
        falhas_api_seguidas += 1
        categoria = resultado.erro_api.categoria
        if categoria in CATEGORIAS_QUE_INTERROMPEM:
            resumo.interrompido_por = categoria  # type: ignore[assignment]
            break
        if falhas_api_seguidas >= MAX_FALHAS_API_SEGUIDAS:
            resumo.interrompido_por = "falhas_api_seguidas"
            break

    resumo.restantes = len(fila) > tentados
    return resumo
