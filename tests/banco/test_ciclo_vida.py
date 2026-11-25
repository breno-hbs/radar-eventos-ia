"""Testes do F1-05 — ciclo de vida do evento (spec §4.8; backlog F1-05, aceite "testes de
cada transição de estado").

Assinaturas fixadas (radar.banco.ciclo_vida, módulo puro, sem banco):
- `EstadoEvento(StrEnum)`: ATIVO="ativo", ALTERADO="alterado", CANCELADO="cancelado",
  ENCERRADO="encerrado", NAO_ENCONTRADO="nao_encontrado".
- `Observacao` (imutável; campos só por nome): visto, hash_mudou, cancelado_na_fonte,
  terminou, ausencia_contavel — todos bool.
- `Decisao`: estado (EstadoEvento), ausencias (int).
- `TRANSICOES_PERMITIDAS: Mapping[EstadoEvento | None, frozenset[EstadoEvento]]` — para cada
  estado de origem (None = evento novo), os destinos possíveis, incluindo ficar no mesmo.
- `transicionar(atual: EstadoEvento | None, ausencias: int, obs: Observacao, limite: int)
  -> Decisao`.
- Incoerência → ValueError (na construção da Observacao ou no transicionar): não visto com
  hash_mudou ou cancelado_na_fonte; visto com ausencia_contavel; evento novo não visto;
  ausencias < 0; limite < 1.
"""

from __future__ import annotations

import importlib
import itertools
from collections.abc import Iterator
from types import ModuleType

import pytest

LIMITE = 3
ESTADOS = ("ativo", "alterado", "cancelado", "encerrado", "nao_encontrado")


@pytest.fixture
def cv() -> ModuleType:
    return importlib.import_module("radar.banco.ciclo_vida")


def _obs(
    cv: ModuleType,
    *,
    visto: bool,
    hash_mudou: bool = False,
    cancelado: bool = False,
    terminou: bool = False,
    contavel: bool = False,
) -> object:
    return cv.Observacao(
        visto=visto,
        hash_mudou=hash_mudou,
        cancelado_na_fonte=cancelado,
        terminou=terminou,
        ausencia_contavel=contavel,
    )


def _estado(cv: ModuleType, valor: str | None) -> object:
    return None if valor is None else cv.EstadoEvento(valor)


# --- Tipos ------------------------------------------------------------------------------


def test_estados_do_evento(cv: ModuleType) -> None:
    assert {e.value for e in cv.EstadoEvento} == set(ESTADOS)
    assert cv.EstadoEvento.NAO_ENCONTRADO == "nao_encontrado"
    assert cv.EstadoEvento.ATIVO == "ativo"


def test_observacao_e_imutavel(cv: ModuleType) -> None:
    obs = _obs(cv, visto=True)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        obs.visto = False  # type: ignore[attr-defined]


# --- Tabela de transições ------------------------------------------------------------------

# (atual, ausencias, observação, esperado, ausencias_esperadas); limite = 3.
V = {"visto": True}
N = {"visto": False}
CASOS: list[tuple[str | None, int, dict[str, bool], str, int]] = [
    # Evento novo (precisa ter sido visto).
    (None, 0, V, "ativo", 0),
    (None, 0, {**V, "cancelado": True}, "cancelado", 0),
    (None, 0, {**V, "terminou": True}, "encerrado", 0),
    # Data passada vence tudo, inclusive o cancelamento.
    (None, 0, {**V, "cancelado": True, "terminou": True}, "encerrado", 0),
    # Visto: zera ausências.
    ("ativo", 2, V, "ativo", 0),
    ("ativo", 0, {**V, "hash_mudou": True}, "alterado", 0),
    ("alterado", 0, V, "ativo", 0),
    ("alterado", 1, {**V, "hash_mudou": True}, "alterado", 0),
    ("ativo", 0, {**V, "cancelado": True}, "cancelado", 0),
    ("alterado", 1, {**V, "cancelado": True, "hash_mudou": True}, "cancelado", 0),
    ("cancelado", 0, {**V, "cancelado": True}, "cancelado", 0),
    ("cancelado", 2, {**V, "cancelado": True, "terminou": True}, "encerrado", 0),
    ("alterado", 0, {**V, "cancelado": True, "terminou": True}, "encerrado", 0),
    # Fonte deixou de dizer cancelado: volta a ativo/alterado se ainda não terminou.
    ("cancelado", 0, V, "ativo", 0),
    ("cancelado", 0, {**V, "hash_mudou": True}, "alterado", 0),
    ("cancelado", 0, {**V, "terminou": True}, "encerrado", 0),
    ("ativo", 1, {**V, "terminou": True}, "encerrado", 0),
    ("alterado", 0, {**V, "terminou": True, "hash_mudou": True}, "encerrado", 0),
    # Reaparecimento.
    ("nao_encontrado", 3, V, "ativo", 0),
    ("nao_encontrado", 5, {**V, "hash_mudou": True}, "alterado", 0),
    ("nao_encontrado", 3, {**V, "cancelado": True}, "cancelado", 0),
    ("nao_encontrado", 3, {**V, "terminou": True}, "encerrado", 0),
    # Encerrado é terminal; só zera o contador se visto.
    ("encerrado", 2, V, "encerrado", 0),
    ("encerrado", 0, {**V, "cancelado": True, "hash_mudou": True}, "encerrado", 0),
    ("encerrado", 1, {**N, "contavel": True}, "encerrado", 1),
    ("encerrado", 1, N, "encerrado", 1),
    # Não visto, ausência contável.
    ("ativo", 0, {**N, "contavel": True}, "ativo", 1),
    ("ativo", 1, {**N, "contavel": True}, "ativo", 2),
    ("ativo", 2, {**N, "contavel": True}, "nao_encontrado", 3),
    ("alterado", 2, {**N, "contavel": True}, "nao_encontrado", 3),
    ("ativo", 5, {**N, "contavel": True}, "nao_encontrado", 6),
    ("nao_encontrado", 3, {**N, "contavel": True}, "nao_encontrado", 4),
    ("cancelado", 5, {**N, "contavel": True}, "cancelado", 6),
    # Não visto, ausência não contável (calendário com falha): nada muda.
    ("ativo", 0, N, "ativo", 0),
    ("ativo", 2, N, "ativo", 2),
    ("alterado", 2, N, "alterado", 2),
    ("nao_encontrado", 4, N, "nao_encontrado", 4),
    ("cancelado", 1, N, "cancelado", 1),
    # Não visto e terminou: encerra (inclusive cancelado), sem mexer no contador.
    ("ativo", 1, {**N, "contavel": True, "terminou": True}, "encerrado", 1),
    ("ativo", 0, {**N, "terminou": True}, "encerrado", 0),
    ("alterado", 2, {**N, "contavel": True, "terminou": True}, "encerrado", 2),
    ("nao_encontrado", 4, {**N, "contavel": True, "terminou": True}, "encerrado", 4),
    ("cancelado", 0, {**N, "contavel": True, "terminou": True}, "encerrado", 0),
    ("cancelado", 2, {**N, "terminou": True}, "encerrado", 2),
]


@pytest.mark.parametrize(
    ("atual", "ausencias", "flags", "esperado", "ausencias_esperadas"),
    CASOS,
    ids=[f"{c[0]}-{c[1]}-{'-'.join(k for k, v in c[2].items() if v)}" for c in CASOS],
)
def test_transicao(
    cv: ModuleType,
    atual: str | None,
    ausencias: int,
    flags: dict[str, bool],
    esperado: str,
    ausencias_esperadas: int,
) -> None:
    decisao = cv.transicionar(_estado(cv, atual), ausencias, _obs(cv, **flags), LIMITE)
    assert (decisao.estado, decisao.ausencias) == (cv.EstadoEvento(esperado), ausencias_esperadas)
    assert isinstance(decisao.estado, cv.EstadoEvento)


def test_limite_um_marca_nao_encontrado_na_primeira_ausencia(cv: ModuleType) -> None:
    decisao = cv.transicionar(cv.EstadoEvento.ATIVO, 0, _obs(cv, visto=False, contavel=True), 1)
    assert (decisao.estado, decisao.ausencias) == (cv.EstadoEvento.NAO_ENCONTRADO, 1)


def test_sequencia_ausencias_ate_nao_encontrado_e_volta(cv: ModuleType) -> None:
    estado, ausencias = cv.EstadoEvento.ATIVO, 0
    historico = []
    for _ in range(3):
        decisao = cv.transicionar(estado, ausencias, _obs(cv, visto=False, contavel=True), 3)
        estado, ausencias = decisao.estado, decisao.ausencias
        historico.append((estado.value, ausencias))
    assert historico == [("ativo", 1), ("ativo", 2), ("nao_encontrado", 3)]
    decisao = cv.transicionar(estado, ausencias, _obs(cv, visto=True), 3)
    assert (decisao.estado, decisao.ausencias) == (cv.EstadoEvento.ATIVO, 0)


# --- Observações incoerentes ---------------------------------------------------------------


@pytest.mark.parametrize(
    "flags",
    [
        {"visto": False, "hash_mudou": True},
        {"visto": False, "cancelado": True},
        {"visto": True, "contavel": True},
    ],
)
def test_observacao_incoerente_e_erro(cv: ModuleType, flags: dict[str, bool]) -> None:
    with pytest.raises(ValueError):
        cv.transicionar(cv.EstadoEvento.ATIVO, 0, _obs(cv, **flags), LIMITE)


@pytest.mark.parametrize("contavel", [False, True])
def test_evento_novo_nao_visto_e_erro(cv: ModuleType, contavel: bool) -> None:
    with pytest.raises(ValueError):
        cv.transicionar(None, 0, _obs(cv, visto=False, contavel=contavel), LIMITE)


def test_ausencias_negativas_e_erro(cv: ModuleType) -> None:
    with pytest.raises(ValueError):
        cv.transicionar(cv.EstadoEvento.ATIVO, -1, _obs(cv, visto=True), LIMITE)


@pytest.mark.parametrize("limite", [0, -1])
def test_limite_menor_que_um_e_erro(cv: ModuleType, limite: int) -> None:
    with pytest.raises(ValueError):
        cv.transicionar(cv.EstadoEvento.ATIVO, 0, _obs(cv, visto=False, contavel=True), limite)


# --- Transições permitidas ------------------------------------------------------------------


def test_transicoes_permitidas_cobre_todas_as_origens(cv: ModuleType) -> None:
    origens = set(cv.TRANSICOES_PERMITIDAS)
    assert origens == {None, *cv.EstadoEvento}


def test_transicoes_proibidas_nao_estao_na_tabela(cv: ModuleType) -> None:
    e = cv.EstadoEvento
    permitidas = cv.TRANSICOES_PERMITIDAS
    assert set(permitidas[e.ENCERRADO]) == {e.ENCERRADO}
    assert e.ENCERRADO in permitidas[e.CANCELADO]  # data passada vence o cancelamento
    assert e.NAO_ENCONTRADO not in permitidas[e.CANCELADO]
    assert set(permitidas[None]) == {e.ATIVO, e.CANCELADO, e.ENCERRADO}


NOMES_FLAGS = ("visto", "hash_mudou", "cancelado", "terminou", "contavel")


def _todas_as_entradas(cv: ModuleType) -> Iterator[tuple[object, int, dict[str, bool], int]]:
    origens = [None, *cv.EstadoEvento]
    combinacoes = list(itertools.product([False, True], repeat=len(NOMES_FLAGS)))
    for atual, ausencias, valores, limite in itertools.product(
        origens, range(5), combinacoes, (1, 2, 3)
    ):
        yield atual, ausencias, dict(zip(NOMES_FLAGS, valores, strict=True)), limite


def test_toda_saida_esta_em_transicoes_permitidas(cv: ModuleType) -> None:
    """Exaustivo: toda combinação coerente leva a um destino permitido; as incoerentes dão
    ValueError (nunca outra exceção)."""
    coerentes = 0
    for atual, ausencias, flags, limite in _todas_as_entradas(cv):
        try:
            decisao = cv.transicionar(atual, ausencias, _obs(cv, **flags), limite)
        except ValueError:
            continue
        coerentes += 1
        contexto = (atual, ausencias, flags, limite)
        assert decisao.estado in cv.TRANSICOES_PERMITIDAS[atual], contexto
        assert decisao.ausencias >= 0, contexto
        if flags["terminou"]:
            assert decisao.estado == cv.EstadoEvento.ENCERRADO, contexto
        if flags["visto"]:
            assert decisao.ausencias == 0, contexto
        else:
            assert decisao.ausencias in (ausencias, ausencias + 1), contexto
    assert coerentes > 0


def test_combinacoes_incoerentes_sempre_dao_erro(cv: ModuleType) -> None:
    for atual, ausencias, flags, limite in _todas_as_entradas(cv):
        incoerente = (
            (not flags["visto"] and (flags["hash_mudou"] or flags["cancelado"]))
            or (flags["visto"] and flags["contavel"])
            or (atual is None and not flags["visto"])
        )
        if not incoerente:
            continue
        with pytest.raises(ValueError):
            cv.transicionar(atual, ausencias, _obs(cv, **flags), limite)
