"""Testes do F1-07 — rótulos de avaliação, eventos das fixtures e independência dos conjuntos.

Assinaturas fixadas (radar.avaliacao.dados):
- `RotuloAvaliacao` (Pydantic, extra="forbid", frozen): calendario: str, id_fonte: str,
  e_ia: bool, idioma: Literal["pt", "en", "bilingue", "outro", "desconhecido"];
- `carregar_rotulos(caminho: Path) -> list[RotuloAvaliacao]`, na ordem do arquivo. Linha
  inválida ou id_fonte repetido → ValueError com "linha N" (1-based) na mensagem e sem o
  conteúdo da linha;
- `carregar_eventos(rotulos, dir_fixtures: Path) -> list[tuple[RotuloAvaliacao, Evento,
  str]]` (rótulo, `Evento` ORM em memória, localizacao_filtro), na ordem dos rótulos; lê
  `<dir>/<calendario>.ics` com `ler_ical`; id ausente no feed → ValueError;
- `validar_conjuntos(teste, dev) -> None`, ambos no formato devolvido por
  `carregar_eventos` (o título vem do evento): ValueError se um id_fonte está nos dois ou se
  um evento do TESTE tem título com "Vector Search" ou "Grok Bot" (D7).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from .fabricas import (
    CALENDARIO,
    DESCRICAO_SINTETICA,
    escrever_ics,
    escrever_jsonl,
    rotulo,
    vevento,
)

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES_LUMA = RAIZ / "tests" / "fixtures" / "luma"
ID_CODEX = "evt-PPnuy0tzwk8MVct"
ID_VECTOR_SEARCH = "evt-AXkUVWCxURYLxoT"  # "MUG São Paulo ... Vector Search na Prática"
ID_GROK = "evt-14lTTiq5UPNPpai"  # "Grok Bot Workshop São Paulo"


@pytest.fixture
def dados() -> ModuleType:
    return importlib.import_module("radar.avaliacao.dados")


def _rotulo(dados: ModuleType, **campos: Any) -> Any:
    return dados.RotuloAvaliacao(**{**rotulo("evt-Sint1"), **campos})


# --- RotuloAvaliacao -----------------------------------------------------------------------


def test_rotulo_valido(dados: ModuleType) -> None:
    item = _rotulo(dados, e_ia=False, idioma="bilingue")
    assert item.calendario == CALENDARIO
    assert item.id_fonte == "evt-Sint1"
    assert item.e_ia is False
    assert item.idioma == "bilingue"


def test_rotulo_imutavel(dados: ModuleType) -> None:
    item = _rotulo(dados)
    with pytest.raises(ValidationError):
        item.e_ia = False


@pytest.mark.parametrize("idioma", ["pt", "en", "bilingue", "outro", "desconhecido"])
def test_idiomas_aceitos(dados: ModuleType, idioma: str) -> None:
    assert _rotulo(dados, idioma=idioma).idioma == idioma


@pytest.mark.parametrize(
    "campos", [{"idioma": "es"}, {"nota": "x"}, {"e_ia": "talvez"}, {"id_fonte": None}]
)
def test_rotulo_invalido(dados: ModuleType, campos: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _rotulo(dados, **campos)


# --- carregar_rotulos ----------------------------------------------------------------------


def test_carregar_rotulos(dados: ModuleType, tmp_path: Path) -> None:
    caminho = escrever_jsonl(
        tmp_path / "teste.jsonl",
        [rotulo("evt-Um1", True, "pt"), rotulo("evt-Dois2", False, "en")],
    )
    rotulos = dados.carregar_rotulos(caminho)
    assert [r.id_fonte for r in rotulos] == ["evt-Um1", "evt-Dois2"]
    assert all(isinstance(r, dados.RotuloAvaliacao) for r in rotulos)
    assert rotulos[1].e_ia is False
    assert rotulos[1].idioma == "en"


@pytest.mark.parametrize(
    "ruim",
    [
        "isto não é json SEGREDO-LINHA",
        '{"calendario": "c", "id_fonte": "SEGREDO-LINHA", "e_ia": true}',
        '{"calendario": "c", "id_fonte": "SEGREDO-LINHA", "e_ia": true, "idioma": "xx"}',
        '{"calendario": "c", "id_fonte": "evt-X", "e_ia": true, "idioma": "pt", '
        '"SEGREDO-LINHA": 1}',
        '["SEGREDO-LINHA"]',
    ],
)
def test_linha_invalida_tem_numero_e_sem_conteudo(
    dados: ModuleType, tmp_path: Path, ruim: str
) -> None:
    caminho = escrever_jsonl(
        tmp_path / "teste.jsonl", [rotulo("evt-Um1"), rotulo("evt-Dois2"), ruim]
    )
    with pytest.raises(ValueError) as info:
        dados.carregar_rotulos(caminho)
    assert "linha 3" in str(info.value)
    assert "SEGREDO-LINHA" not in str(info.value)


def test_id_repetido_e_erro(dados: ModuleType, tmp_path: Path) -> None:
    caminho = escrever_jsonl(
        tmp_path / "teste.jsonl",
        [rotulo("evt-Um1"), rotulo("evt-Dois2"), rotulo("evt-Um1", e_ia=False)],
    )
    with pytest.raises(ValueError) as info:
        dados.carregar_rotulos(caminho)
    assert "linha 3" in str(info.value)


# --- carregar_eventos ----------------------------------------------------------------------


def test_carregar_eventos_da_fixture_real(dados: ModuleType) -> None:
    from radar.banco.modelos import Evento

    rotulos = [_rotulo(dados, calendario="codex-sp", id_fonte=ID_CODEX)]
    [(item, evento, filtro)] = dados.carregar_eventos(rotulos, FIXTURES_LUMA)
    assert item == rotulos[0]
    assert isinstance(evento, Evento)
    assert evento.id_fonte == ID_CODEX
    assert evento.titulo.startswith("Codex Community Meetup")
    assert filtro == "sp"
    assert set(evento.descricoes) == {"codex-sp"}
    assert evento.inicio.tzinfo is not None


def test_evento_carregado_serve_para_montar_mensagem(dados: ModuleType, tmp_path: Path) -> None:
    prompt = importlib.import_module("radar.classificacao.prompt")
    escrever_ics(tmp_path, CALENDARIO, [vevento("evt-Sint1", "Meetup Sintético")])
    [(_, evento, filtro)] = dados.carregar_eventos([_rotulo(dados)], tmp_path)
    assert evento.descricoes == {CALENDARIO: DESCRICAO_SINTETICA}
    mensagem, texto_fonte = prompt.montar_mensagem(evento, filtro)
    assert "Meetup Sintético" in mensagem
    assert "modelos de linguagem" in texto_fonte


def test_carregar_eventos_mantem_ordem_dos_rotulos(dados: ModuleType, tmp_path: Path) -> None:
    escrever_ics(
        tmp_path, CALENDARIO, [vevento("evt-Sint1", "Primeiro"), vevento("evt-Sint2", "Segundo")]
    )
    rotulos = [_rotulo(dados, id_fonte="evt-Sint2"), _rotulo(dados, id_fonte="evt-Sint1")]
    itens = dados.carregar_eventos(rotulos, tmp_path)
    assert [evento.titulo for _, evento, _ in itens] == ["Segundo", "Primeiro"]


def test_id_ausente_no_feed_e_erro(dados: ModuleType, tmp_path: Path) -> None:
    escrever_ics(tmp_path, CALENDARIO, [vevento("evt-Sint1", "Primeiro")])
    with pytest.raises(ValueError):
        dados.carregar_eventos([_rotulo(dados, id_fonte="evt-NaoExiste9")], tmp_path)


# --- validar_conjuntos ---------------------------------------------------------------------


def _itens(dados: ModuleType, *pares: tuple[str, str]) -> list[Any]:
    rotulos = [_rotulo(dados, calendario=cal, id_fonte=id_fonte) for cal, id_fonte in pares]
    return dados.carregar_eventos(rotulos, FIXTURES_LUMA)


def test_conjuntos_independentes_passam(dados: ModuleType) -> None:
    teste = _itens(dados, ("codex-sp", ID_CODEX))
    dev = _itens(dados, ("mug-sp", ID_VECTOR_SEARCH), ("cidade-sp", ID_GROK))
    assert dados.validar_conjuntos(teste, dev) is None


def test_id_nos_dois_conjuntos_e_erro(dados: ModuleType) -> None:
    teste = _itens(dados, ("codex-sp", ID_CODEX))
    dev = _itens(dados, ("codex-sp", ID_CODEX))
    with pytest.raises(ValueError):
        dados.validar_conjuntos(teste, dev)


@pytest.mark.parametrize(
    ("calendario", "id_fonte"), [("mug-sp", ID_VECTOR_SEARCH), ("cidade-sp", ID_GROK)]
)
def test_casos_da_d7_no_teste_sao_erro(dados: ModuleType, calendario: str, id_fonte: str) -> None:
    teste = _itens(dados, ("codex-sp", ID_CODEX), (calendario, id_fonte))
    with pytest.raises(ValueError):
        dados.validar_conjuntos(teste, [])
