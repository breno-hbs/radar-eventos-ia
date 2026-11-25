"""Testes do ticket F1-03 — lista de calendários da Luma em `config/fontes.yaml`.

Cobrem `radar.fontes.calendarios` (`CalendarioLuma` e `carregar_calendarios`). As variações
inválidas são escritas em `tmp_path` a partir do próprio `config/fontes.yaml`, para não
depender do formato exato do arquivo (lista no topo ou sob uma chave).
"""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

RAIZ = Path(__file__).resolve().parents[2]
CONFIG = RAIZ / "config" / "fontes.yaml"

ESPERADOS = {
    "ai-collective": ("calendar", "cal-E74MDlDKBaeAwXK"),
    "codex-sp": ("calendar", "cal-sAAnGigXnTQWpw6"),
    "vibe-space": ("calendar", "cal-s9CExmUfS7kMu50"),
    "womakerscode": ("calendar", "cal-K9IcbN14WTbkzOs"),
    "mug-sp": ("calendar", "cal-HtbK5aYvR5B2WAu"),
    "cidade-sp": ("discover", "discplace-AQZnCu9wl4LmOIp"),
}


@pytest.fixture
def calendarios_mod() -> ModuleType:
    return importlib.import_module("radar.fontes.calendarios")


def _dados_reais() -> Any:
    assert CONFIG.is_file(), "config/fontes.yaml não existe"
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _entradas(dados: Any) -> list[dict[str, Any]]:
    """Lista de entradas de calendário, esteja ela no topo ou sob uma chave."""
    if isinstance(dados, list):
        return dados
    assert isinstance(dados, dict)
    listas = [v for v in dados.values() if isinstance(v, list)]
    assert len(listas) == 1, "esperava uma única lista de calendários em config/fontes.yaml"
    return listas[0]


def _gravar_variacao(tmp_path: Path, mudar: Any) -> Path:
    dados = copy.deepcopy(_dados_reais())
    mudar(_entradas(dados))
    caminho = tmp_path / "fontes.yaml"
    caminho.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    return caminho


# --- Arquivo real ------------------------------------------------------------------------


def test_config_real_carrega_os_seis_feeds(calendarios_mod: ModuleType) -> None:
    calendarios = calendarios_mod.carregar_calendarios(CONFIG)

    assert len(calendarios) == 6
    assert all(isinstance(c, calendarios_mod.CalendarioLuma) for c in calendarios)
    assert {c.slug: (c.entidade, c.id) for c in calendarios} == ESPERADOS
    for calendario in calendarios:
        assert isinstance(calendario.nome, str)
        assert calendario.nome.strip(), calendario.slug


def test_config_real_nao_guarda_url_pronta() -> None:
    for entrada in _entradas(_dados_reais()):
        assert "url" not in entrada


def test_config_real_gera_as_urls_do_ical(calendarios_mod: ModuleType) -> None:
    luma = importlib.import_module("radar.fontes.luma")
    calendarios = calendarios_mod.carregar_calendarios(CONFIG)
    urls = {c.slug: luma.url_ical(c) for c in calendarios}

    for slug, (entidade, ident) in ESPERADOS.items():
        assert urls[slug] == f"https://api.lu.ma/ics/get?entity={entidade}&id={ident}"


# --- Variações inválidas -----------------------------------------------------------------


def test_slug_duplicado_e_erro(calendarios_mod: ModuleType, tmp_path: Path) -> None:
    def mudar(entradas: list[dict[str, Any]]) -> None:
        entradas[1]["slug"] = entradas[0]["slug"]

    with pytest.raises(ValueError):
        calendarios_mod.carregar_calendarios(_gravar_variacao(tmp_path, mudar))


@pytest.mark.parametrize(
    "ident",
    ["cal_E74MDlDKBaeAwXK", "evt-E74MDlDKBaeAwXK", "cal-", "cal-E74 MDl", "E74MDlDKBaeAwXK"],
)
def test_id_fora_do_padrao_e_erro(calendarios_mod: ModuleType, tmp_path: Path, ident: str) -> None:
    def mudar(entradas: list[dict[str, Any]]) -> None:
        entradas[0]["id"] = ident

    with pytest.raises(ValueError):
        calendarios_mod.carregar_calendarios(_gravar_variacao(tmp_path, mudar))


def test_entidade_desconhecida_e_erro(calendarios_mod: ModuleType, tmp_path: Path) -> None:
    def mudar(entradas: list[dict[str, Any]]) -> None:
        entradas[0]["entidade"] = "user"

    with pytest.raises(ValueError):
        calendarios_mod.carregar_calendarios(_gravar_variacao(tmp_path, mudar))


def test_chave_desconhecida_e_erro(calendarios_mod: ModuleType, tmp_path: Path) -> None:
    def mudar(entradas: list[dict[str, Any]]) -> None:
        entradas[0]["cor"] = "azul"

    with pytest.raises(ValueError):
        calendarios_mod.carregar_calendarios(_gravar_variacao(tmp_path, mudar))


def test_campo_url_e_erro(calendarios_mod: ModuleType, tmp_path: Path) -> None:
    def mudar(entradas: list[dict[str, Any]]) -> None:
        entradas[0]["url"] = "https://api.lu.ma/ics/get?entity=calendar&id=cal-E74MDlDKBaeAwXK"

    with pytest.raises(ValueError):
        calendarios_mod.carregar_calendarios(_gravar_variacao(tmp_path, mudar))


@pytest.mark.parametrize("campo", ["slug", "nome", "entidade", "id"])
def test_campo_obrigatorio_ausente_e_erro(
    calendarios_mod: ModuleType, tmp_path: Path, campo: str
) -> None:
    def mudar(entradas: list[dict[str, Any]]) -> None:
        del entradas[0][campo]

    with pytest.raises(ValueError):
        calendarios_mod.carregar_calendarios(_gravar_variacao(tmp_path, mudar))


def test_yaml_com_tag_python_e_recusado(calendarios_mod: ModuleType, tmp_path: Path) -> None:
    # yaml.safe_load não constrói objetos Python arbitrários.
    caminho = tmp_path / "fontes.yaml"
    caminho.write_text("!!python/object/apply:os.system ['true']\n", encoding="utf-8")

    with pytest.raises((yaml.YAMLError, ValueError)):
        calendarios_mod.carregar_calendarios(caminho)


# --- Modelo ------------------------------------------------------------------------------


def test_calendario_luma_valido(calendarios_mod: ModuleType) -> None:
    calendario = calendarios_mod.CalendarioLuma(
        slug="mug-sp", nome="MUG São Paulo", entidade="calendar", id="cal-HtbK5aYvR5B2WAu"
    )

    assert calendario.id == "cal-HtbK5aYvR5B2WAu"


def test_calendario_luma_rejeita_campo_extra(calendarios_mod: ModuleType) -> None:
    with pytest.raises(ValidationError):
        calendarios_mod.CalendarioLuma(
            slug="mug-sp",
            nome="MUG São Paulo",
            entidade="calendar",
            id="cal-HtbK5aYvR5B2WAu",
            url="https://api.lu.ma/ics/get",
        )
