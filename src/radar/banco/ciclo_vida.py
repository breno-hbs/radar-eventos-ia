"""Ciclo de vida do evento (spec §4.8, D12): função pura, sem banco.

Regras, na ordem:
1. `encerrado` é terminal: só zera o contador se o evento for visto.
2. Terminou → `encerrado`, de qualquer estado (a data passada vence o cancelamento); visto
   zera o contador, não visto não mexe nele.
3. Visto (contador zera): cancelado na fonte → `cancelado`; hash mudou → `alterado` (evento
   novo nunca é `alterado`); senão `ativo` (inclusive quem estava `cancelado` e a fonte
   deixou de cancelar).
4. Não visto: `cancelado` continua `cancelado` (o contador segue a regra de ausência);
   ausência contável soma 1 e, ao atingir o limite, vira `nao_encontrado`; ausência não
   contável não muda nada.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class EstadoEvento(StrEnum):
    ATIVO = "ativo"
    ALTERADO = "alterado"
    CANCELADO = "cancelado"
    ENCERRADO = "encerrado"
    NAO_ENCONTRADO = "nao_encontrado"


@dataclass(frozen=True, kw_only=True)
class Observacao:
    """O que a rodada diz sobre um evento já conhecido (ou novo)."""

    visto: bool
    hash_mudou: bool
    cancelado_na_fonte: bool
    terminou: bool
    # Não visto e todos os calendários do evento lidos com sucesso (ok ou vazio).
    ausencia_contavel: bool

    def __post_init__(self) -> None:
        if not self.visto and (self.hash_mudou or self.cancelado_na_fonte):
            raise ValueError("evento não visto não tem hash nem status novos")
        if self.visto and self.ausencia_contavel:
            raise ValueError("evento visto não tem ausência")


@dataclass(frozen=True, kw_only=True)
class Decisao:
    estado: EstadoEvento
    ausencias: int


_E = EstadoEvento
_TODOS = frozenset(EstadoEvento)

TRANSICOES_PERMITIDAS: Mapping[EstadoEvento | None, frozenset[EstadoEvento]] = MappingProxyType(
    {
        None: frozenset({_E.ATIVO, _E.CANCELADO, _E.ENCERRADO}),
        _E.ATIVO: _TODOS,
        _E.ALTERADO: _TODOS,
        _E.CANCELADO: frozenset({_E.CANCELADO, _E.ATIVO, _E.ALTERADO, _E.ENCERRADO}),
        _E.ENCERRADO: frozenset({_E.ENCERRADO}),
        _E.NAO_ENCONTRADO: _TODOS,
    }
)


def transicionar(
    atual: EstadoEvento | None, ausencias: int, obs: Observacao, limite: int
) -> Decisao:
    """Próximo estado e contador de ausências consecutivas. Entrada incoerente → ValueError."""
    if limite < 1:
        raise ValueError("limite de ausências precisa ser >= 1")
    if ausencias < 0:
        raise ValueError("ausências não podem ser negativas")
    if atual is None and not obs.visto:
        raise ValueError("evento novo precisa ter sido visto")

    if atual is _E.ENCERRADO:
        return Decisao(estado=_E.ENCERRADO, ausencias=0 if obs.visto else ausencias)

    if obs.terminou:
        return Decisao(estado=_E.ENCERRADO, ausencias=0 if obs.visto else ausencias)

    if obs.visto:
        if obs.cancelado_na_fonte:
            estado = _E.CANCELADO
        elif obs.hash_mudou and atual is not None:
            estado = _E.ALTERADO
        else:
            estado = _E.ATIVO
        return Decisao(estado=estado, ausencias=0)

    assert atual is not None
    contador = ausencias + 1 if obs.ausencia_contavel else ausencias
    if atual is _E.CANCELADO:
        return Decisao(estado=_E.CANCELADO, ausencias=contador)
    if obs.ausencia_contavel and contador >= limite:
        return Decisao(estado=_E.NAO_ENCONTRADO, ausencias=contador)
    return Decisao(estado=atual, ausencias=contador)
