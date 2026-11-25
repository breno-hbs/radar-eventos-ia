"""Rótulos de avaliação e eventos das fixtures (D7, D14).

Cada linha de `avaliacao/<conjunto>.jsonl` aponta para um evento de
`tests/fixtures/luma/<calendario>.ics`. Os eventos são montados como `Evento` ORM em
memória (sem banco), do mesmo jeito que o repositório grava um evento novo, para que a
mensagem enviada ao LLM seja a mesma do radar.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from radar.banco.conteudo import consolidar_observacoes, hash_conteudo
from radar.banco.modelos import Evento
from radar.filtros.geografico import classificar_localizacao
from radar.fontes.luma import ler_ical
from radar.fontes.modelos import EventoNormalizado

# Casos usados para escrever o prompt: só podem estar no dev (D7).
TERMOS_SO_DEV = ("vector search", "grok bot")

ItemAvaliacao = tuple["RotuloAvaliacao", Evento, str]


class RotuloAvaliacao(BaseModel):
    """Uma linha do JSONL: o evento e a verdade rotulada pelo autor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    calendario: str
    id_fonte: str
    e_ia: bool
    idioma: Literal["pt", "en", "bilingue", "outro", "desconhecido"]


def carregar_rotulos(caminho: Path) -> list[RotuloAvaliacao]:
    """Rótulos na ordem do arquivo; linhas em branco são ignoradas.

    Erro aponta só o número da linha (1-based), nunca o conteúdo dela.
    """
    if not caminho.is_file():
        raise ValueError(f"arquivo de rótulos ausente: {caminho.name} (em {caminho.parent})")
    rotulos: list[RotuloAvaliacao] = []
    vistos: set[str] = set()
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=1):
        if not linha.strip():
            continue
        try:
            dados = json.loads(linha)
        except json.JSONDecodeError:
            raise ValueError(f"{caminho.name}, linha {numero}: JSON inválido") from None
        if not isinstance(dados, dict):
            raise ValueError(f"{caminho.name}, linha {numero}: esperado um objeto JSON")
        try:
            item = RotuloAvaliacao.model_validate(dados, strict=True)
        except ValidationError:
            raise ValueError(f"{caminho.name}, linha {numero}: rótulo inválido") from None
        if item.id_fonte in vistos:
            raise ValueError(f"{caminho.name}, linha {numero}: id_fonte repetido")
        vistos.add(item.id_fonte)
        rotulos.append(item)
    return rotulos


def _ler_feed(dir_fixtures: Path, calendario: str) -> dict[str, EventoNormalizado]:
    caminho = dir_fixtures / f"{calendario}.ics"
    if not caminho.is_file():
        raise ValueError(f"fixture ausente para o calendário {calendario!r}")
    resultado = ler_ical(caminho.read_text(encoding="utf-8"), calendario)
    if resultado.situacao == "falha":
        raise ValueError(f"fixture do calendário {calendario!r} não pôde ser lida")
    return {evento.id_fonte: evento for evento in resultado.eventos}


def _evento_orm(observacao: EventoNormalizado) -> tuple[Evento, str]:
    """`Evento` em memória como o repositório grava um evento novo, e o filtro geográfico."""
    localizacao = classificar_localizacao(observacao)
    if localizacao.situacao == "fora":
        raise ValueError(f"evento {observacao.id_fonte} não chega ao classificador (fora de SP)")
    consolidado = consolidar_observacoes([observacao])
    evento = Evento(
        fonte=consolidado.fonte,
        id_fonte=consolidado.id_fonte,
        calendarios=list(consolidado.calendarios),
        titulo=consolidado.titulo,
        inicio=consolidado.inicio,
        fim=consolidado.fim,
        dia_inteiro=consolidado.dia_inteiro,
        local_texto=consolidado.local_texto,
        local_url=consolidado.local_url,
        link_inscricao=consolidado.link_inscricao,
        organizador=consolidado.organizador,
        endereco_oculto=consolidado.endereco_oculto,
        latitude=consolidado.latitude,
        longitude=consolidado.longitude,
        descricoes=dict(consolidado.descricoes),
        status_fonte=consolidado.status_fonte,
        localizacao=localizacao.situacao,
        evidencia_localizacao=localizacao.evidencia,
        hash_conteudo=hash_conteudo(consolidado, consolidado.descricoes),
        estado="ativo",
        ausencias_consecutivas=0,
        rodadas_escalares_mantidos=0,
    )
    return evento, localizacao.situacao


def carregar_eventos(rotulos: Sequence[RotuloAvaliacao], dir_fixtures: Path) -> list[ItemAvaliacao]:
    """(rótulo, evento, localizacao_filtro) na ordem dos rótulos. Id fora do feed → ValueError."""
    feeds: dict[str, dict[str, EventoNormalizado]] = {}
    itens: list[ItemAvaliacao] = []
    for rotulo in rotulos:
        if rotulo.calendario not in feeds:
            feeds[rotulo.calendario] = _ler_feed(dir_fixtures, rotulo.calendario)
        observacao = feeds[rotulo.calendario].get(rotulo.id_fonte)
        if observacao is None:
            raise ValueError(
                f"evento {rotulo.id_fonte} não está na fixture do calendário {rotulo.calendario!r}"
            )
        evento, filtro = _evento_orm(observacao)
        itens.append((rotulo, evento, filtro))
    return itens


def validar_conjuntos(teste: Sequence[ItemAvaliacao], dev: Sequence[ItemAvaliacao]) -> None:
    """Conjuntos independentes (D7): nenhum id nos dois e casos do prompt só no dev."""
    ids_dev = {rotulo.id_fonte for rotulo, _, _ in dev}
    repetidos = sorted(rotulo.id_fonte for rotulo, _, _ in teste if rotulo.id_fonte in ids_dev)
    if repetidos:
        raise ValueError(f"eventos nos dois conjuntos: {', '.join(repetidos)}")
    for rotulo, evento, _ in teste:
        titulo = evento.titulo.casefold()
        if any(termo in titulo for termo in TERMOS_SO_DEV):
            raise ValueError(f"evento {rotulo.id_fonte} foi usado no prompt e só pode ficar no dev")
