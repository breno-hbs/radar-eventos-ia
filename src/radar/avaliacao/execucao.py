"""Execução da avaliação: classifica um conjunto rotulado e grava o relatório (D14).

Tudo o que pode falhar sem gastar (rótulos, fixtures, independência dos conjuntos, limite de
chamadas, chave) é conferido ANTES da primeira chamada. Qualquer erro de API ou o limite de
tokens interrompe com `RuntimeError` e não grava relatório: um relatório parcial pareceria
completo, e um erro de API não diz nada sobre a qualidade do classificador.

Com `dir_diagnostico` (D15), cada erro de saída `saida_*` grava `<dir>/<id_fonte>.json` com a
resposta bruta do LLM, na hora (sobrevive a uma interrupção). Esse diretório vira artifact do
Actions com retenção curta; nunca vai ao banco nem ao repositório.
"""

from __future__ import annotations

import json
import os
import re
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
from radar.fontes.calendarios import carregar_calendarios

Conjunto = Literal["teste", "dev"]
CONJUNTOS: tuple[Conjunto, ...] = ("teste", "dev")
FUSO = "America/Sao_Paulo"
_NOME_SEGURO = re.compile(r"[A-Za-z0-9_-]+")


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


def _comunidades(arquivo_fontes: Path) -> frozenset[str]:
    """Slugs com `comunidade_ia: true`; qualquer problema no arquivo vira ValueError."""
    if not arquivo_fontes.is_file():
        raise ValueError(f"arquivo de fontes ausente: {arquivo_fontes}")
    try:
        calendarios = carregar_calendarios(arquivo_fontes)
    except ValueError:
        raise ValueError(f"arquivo de fontes inválido: {arquivo_fontes}") from None
    except Exception as erro:  # erro do YAML
        raise ValueError(
            f"arquivo de fontes inválido: {arquivo_fontes} ({type(erro).__name__})"
        ) from None
    return frozenset(c.slug for c in calendarios if c.comunidade_ia)


def _gravar_diagnostico(diretorio: Path, id_fonte: str, resultado: ResultadoEvento) -> None:
    if not _NOME_SEGURO.fullmatch(id_fonte):
        raise ValueError(f"id_fonte impróprio para nome de arquivo: {id_fonte!r}")
    dados = {
        "id_fonte": id_fonte,
        "motivo_erro": resultado.motivo_erro,
        "stop_reason": resultado.stop_reason,
        "tokens_entrada": resultado.tokens_entrada,
        "tokens_saida": resultado.tokens_saida,
        "texto_resposta": resultado.texto_resposta,
    }
    _gravar(diretorio / f"{id_fonte}.json", json.dumps(dados, ensure_ascii=False, indent=2))


def executar(
    conjunto: Conjunto,
    saida: Path,
    *,
    config: Configuracao,
    cliente: ClienteLLM | None,
    dir_avaliacao: Path,
    dir_fixtures: Path,
    arquivo_fontes: Path,
    agora: datetime,
    dir_diagnostico: Path | None = None,
) -> Metricas:
    """Avalia `conjunto` e grava o relatório em `saida`. Devolve as métricas."""
    if conjunto not in CONJUNTOS:
        raise ValueError(f"conjunto desconhecido: {conjunto!r}")
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")

    # 1. Dados e independência dos conjuntos (sem gastar nada).
    rotulos_teste = carregar_rotulos(dir_avaliacao / "teste.jsonl")
    rotulos_dev = carregar_rotulos(dir_avaliacao / "dev.jsonl")
    comunidades = _comunidades(arquivo_fontes)
    itens_teste = carregar_eventos(rotulos_teste, dir_fixtures, comunidades_ia=comunidades)
    itens_dev = carregar_eventos(rotulos_dev, dir_fixtures, comunidades_ia=comunidades)
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
    for rotulo, evento, filtro, comunidade in itens:
        if (
            tokens_entrada >= config.limite_tokens_entrada_por_execucao
            or tokens_saida >= config.limite_tokens_saida_por_execucao
        ):
            raise RuntimeError(
                f"avaliação interrompida (limite_tokens) após {len(resultados)} de "
                f"{len(itens)} eventos; relatório não gravado"
            )
        resultado = classificar_evento(evento, filtro, cliente, comunidade_ia=comunidade)
        if (
            dir_diagnostico is not None
            and resultado.motivo_erro is not None
            and resultado.motivo_erro.startswith("saida_")
        ):
            _gravar_diagnostico(dir_diagnostico, evento.id_fonte, resultado)
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
