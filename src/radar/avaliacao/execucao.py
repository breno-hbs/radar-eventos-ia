"""Execução da avaliação: classifica um conjunto rotulado e grava o relatório (D14).

Tudo o que pode falhar sem gastar (rótulos, fixtures, independência dos conjuntos, limite de
chamadas, chave) é conferido ANTES da primeira chamada. Qualquer erro de API ou o limite de
tokens interrompe com `RuntimeError` e não grava relatório: um relatório parcial pareceria
completo, e um erro de API não diz nada sobre a qualidade do classificador.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from radar.avaliacao.dados import (
    RotuloAvaliacao,
    carregar_eventos,
    carregar_rotulos,
    validar_conjuntos,
)
from radar.avaliacao.metricas import Metricas, calcular_metricas
from radar.avaliacao.relatorio import gerar_relatorio
from radar.classificacao import modelos, precos, prompt
from radar.classificacao.classificador import ResultadoEvento, classificar_evento
from radar.classificacao.cliente import ClienteLLM, criar_cliente_anthropic
from radar.config import Configuracao

Conjunto = Literal["teste", "dev"]
CONJUNTOS: tuple[Conjunto, ...] = ("teste", "dev")
FUSO = "America/Sao_Paulo"


def _gravar(saida: Path, texto: str) -> None:
    """Grava de uma vez (arquivo temporário + rename): nunca deixa relatório pela metade."""
    saida.parent.mkdir(parents=True, exist_ok=True)
    descritor, temporario = tempfile.mkstemp(dir=saida.parent, suffix=".tmp")
    try:
        with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
            arquivo.write(texto)
        os.replace(temporario, saida)
    except BaseException:
        Path(temporario).unlink(missing_ok=True)
        raise


def executar(
    conjunto: Conjunto,
    saida: Path,
    *,
    config: Configuracao,
    cliente: ClienteLLM | None,
    dir_avaliacao: Path,
    dir_fixtures: Path,
    agora: datetime,
) -> Metricas:
    """Avalia `conjunto` e grava o relatório em `saida`. Devolve as métricas."""
    if conjunto not in CONJUNTOS:
        raise ValueError(f"conjunto desconhecido: {conjunto!r}")
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")

    # 1. Dados e independência dos conjuntos (sem gastar nada).
    itens_teste = carregar_eventos(carregar_rotulos(dir_avaliacao / "teste.jsonl"), dir_fixtures)
    itens_dev = carregar_eventos(carregar_rotulos(dir_avaliacao / "dev.jsonl"), dir_fixtures)
    validar_conjuntos(itens_teste, itens_dev)
    itens = itens_teste if conjunto == "teste" else itens_dev
    if not itens:
        raise ValueError(f"conjunto {conjunto} vazio")
    if len(itens) > config.limite_chamadas_llm_por_execucao:
        raise ValueError(
            f"conjunto {conjunto} tem {len(itens)} eventos, acima do limite de "
            f"{config.limite_chamadas_llm_por_execucao} chamadas por execução"
        )

    # 2. Versões e cliente.
    versao_prompt = prompt.VERSAO_PROMPT
    modelo = modelos.MODELO
    versao_schema = modelos.VERSAO_SCHEMA
    if modelo not in precos.TABELA_PRECOS:
        raise ValueError(f"modelo sem preço na tabela: {modelo!r}")
    prompt.carregar_prompt()  # prompt alterado sem nova versão falha antes de chamar
    if cliente is None:
        cliente = criar_cliente_anthropic(config)

    # 3. Uma chamada por evento, com os mesmos tetos do radar.
    resultados: list[tuple[RotuloAvaliacao, str, ResultadoEvento]] = []
    tokens_entrada = 0
    tokens_saida = 0
    custo_total = Decimal("0")
    for rotulo, evento, filtro in itens:
        if (
            tokens_entrada >= config.limite_tokens_entrada_por_execucao
            or tokens_saida >= config.limite_tokens_saida_por_execucao
        ):
            raise RuntimeError(
                f"avaliação interrompida (limite_tokens) após {len(resultados)} de "
                f"{len(itens)} eventos; relatório não gravado"
            )
        resultado = classificar_evento(evento, filtro, cliente)
        if resultado.erro_api is not None:
            raise RuntimeError(
                f"avaliação interrompida (erro de API: {resultado.erro_api.categoria}) no evento "
                f"{len(resultados) + 1} de {len(itens)}; relatório não gravado"
            )
        tokens_entrada += resultado.tokens_entrada or 0
        tokens_saida += resultado.tokens_saida or 0
        if resultado.custo_usd is not None:
            custo_total += resultado.custo_usd
        resultados.append((rotulo, evento.titulo, resultado))

    # 4. Métricas e relatório.
    metricas = calcular_metricas(resultados)
    texto = gerar_relatorio(
        metricas,
        conjunto=conjunto,
        versao_prompt=versao_prompt,
        modelo=modelo,
        versao_schema=versao_schema,
        custo_total=custo_total,
        data=agora.astimezone(ZoneInfo(FUSO)).date(),
    )
    _gravar(saida, texto)
    return metricas
