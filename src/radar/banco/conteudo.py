"""Consolidação das observações de um evento e hash do conteúdo relevante (D12).

O mesmo evento (`fonte`, `id_fonte`) pode aparecer em vários calendários na mesma rodada,
cada um com sua descrição. A consolidação escolhe os escalares de forma estável (calendário
de menor slug) e guarda as descrições por calendário; o hash usa o mapa de descrições
ACUMULADO no banco, para que um calendário que falhou não faça o hash oscilar.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from radar.fontes.modelos import EventoNormalizado, StatusFonte

# Muda quando a normalização ou o conjunto de campos do hash mudar.
VERSAO_HASH = 1
CASAS_COORDENADA = 6

_ESPACOS = re.compile(r"\s+")


class EventoConsolidado(BaseModel):
    """Um evento visto em um ou mais calendários na rodada."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fonte: Literal["luma"]
    id_fonte: str
    titulo: str
    inicio: AwareDatetime
    fim: AwareDatetime | None
    dia_inteiro: bool
    local_texto: str | None
    local_url: str | None
    endereco_oculto: bool
    latitude: float | None
    longitude: float | None
    link_inscricao: str | None
    organizador: str | None
    status_fonte: StatusFonte
    calendarios: tuple[str, ...]
    descricoes: dict[str, str | None]


def consolidar_observacoes(observacoes: Sequence[EventoNormalizado]) -> EventoConsolidado:
    """Junta as observações de um mesmo evento, uma por calendário.

    Escalares vêm do calendário de menor slug; `status_fonte` é "cancelado" se qualquer
    calendário diz isso. Lista vazia, eventos diferentes ou calendário repetido → ValueError.
    """
    if not observacoes:
        raise ValueError("nenhuma observação para consolidar")
    chaves = {(o.fonte, o.id_fonte) for o in observacoes}
    if len(chaves) != 1:
        raise ValueError("observações de eventos diferentes")
    slugs = [o.calendario for o in observacoes]
    if len(set(slugs)) != len(slugs):
        raise ValueError("calendário repetido nas observações")

    ordenadas = sorted(observacoes, key=lambda o: o.calendario)
    base = ordenadas[0]
    cancelado = any(o.status_fonte == "cancelado" for o in ordenadas)
    return EventoConsolidado(
        fonte=base.fonte,
        id_fonte=base.id_fonte,
        titulo=base.titulo,
        inicio=base.inicio,
        fim=base.fim,
        dia_inteiro=base.dia_inteiro,
        local_texto=base.local_texto,
        local_url=base.local_url,
        endereco_oculto=base.endereco_oculto,
        latitude=base.latitude,
        longitude=base.longitude,
        link_inscricao=base.link_inscricao,
        organizador=base.organizador,
        status_fonte="cancelado" if cancelado else base.status_fonte,
        calendarios=tuple(o.calendario for o in ordenadas),
        descricoes={o.calendario: o.descricao for o in ordenadas},
    )


def _texto(valor: str | None) -> str | None:
    """NFC, espaços em branco (inclusive quebras de linha) colapsados; vazio vira None."""
    if valor is None:
        return None
    normalizado = _ESPACOS.sub(" ", unicodedata.normalize("NFC", valor)).strip()
    return normalizado or None


def _horario(valor: datetime | None) -> str | None:
    return None if valor is None else valor.astimezone(UTC).isoformat()


def _coordenada(valor: float | None) -> str | None:
    return None if valor is None else f"{round(valor, CASAS_COORDENADA):.{CASAS_COORDENADA}f}"


def descricoes_distintas(descricoes: Mapping[str, str | None]) -> list[str]:
    """Conjunto ordenado das descrições normalizadas, sem slug e sem vazias.

    Usado no hash e na mensagem do classificador (F1-06), para os dois verem o mesmo texto.
    """
    return sorted({t for t in (_texto(d) for d in descricoes.values()) if t is not None})


def hash_conteudo(evento: EventoConsolidado, descricoes: Mapping[str, str | None]) -> str:
    """SHA-256 (hex) do conteúdo relevante normalizado.

    `descricoes` é o mapa acumulado {slug: descrição}; entra no hash só o CONJUNTO das
    descrições normalizadas distintas (sem slugs, sem vazias), então um calendário novo com
    descrição equivalente não muda o hash. `evento.descricoes` não é usado.
    Ficam fora: calendarios, status_fonte, organizador, fonte e id_fonte.
    """
    conteudo = {
        "versao": VERSAO_HASH,
        "titulo": _texto(evento.titulo),
        "inicio": _horario(evento.inicio),
        "fim": _horario(evento.fim),
        "dia_inteiro": evento.dia_inteiro,
        "local_texto": _texto(evento.local_texto),
        "local_url": _texto(evento.local_url),
        "endereco_oculto": evento.endereco_oculto,
        "latitude": _coordenada(evento.latitude),
        "longitude": _coordenada(evento.longitude),
        "link_inscricao": _texto(evento.link_inscricao),
        "descricoes": descricoes_distintas(descricoes),
    }
    canonico = json.dumps(conteudo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()
