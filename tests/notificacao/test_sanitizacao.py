"""Testes do F1-09 — sanitização do que é exibido no Telegram.

Assinaturas fixadas (radar.notificacao.sanitizacao):
- `url_exibivel(url: str | None) -> str | None`: remove caracteres Cf; None se sobrar espaço,
  Cc ou "\\", se urlsplit falhar, se houver "@" no netloc (userinfo), scheme ≠ http/https,
  host vazio ou len > 500.
- `texto_exibivel(texto: str | None, maximo: int) -> str | None`: espaços em branco
  (inclusive quebras e tabs) viram um espaço, Cc e Cf são removidos, strip; vazio → None;
  acima de `maximo` unidades UTF-16 corta e termina em "…" (o total, com "…", cabe em
  `maximo`), sem quebrar par substituto.
- `tamanho_telegram(texto: str) -> int`: unidades UTF-16.
"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest


@pytest.fixture
def san() -> ModuleType:
    return importlib.import_module("radar.notificacao.sanitizacao")


# --- url_exibivel --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["https://luma.com/x", "http://exemplo.com/a?b=1#c", "https://us02web.zoom.us/j/1"],
)
def test_url_valida_sai_igual(san: ModuleType, url: str) -> None:
    assert san.url_exibivel(url) == url


def test_url_remove_formatacao_invisivel(san: ModuleType) -> None:
    assert san.url_exibivel("https://luma.com/‮abc​") == "https://luma.com/abc"


def test_url_de_500_caracteres_e_aceita(san: ModuleType) -> None:
    url = "https://luma.com/" + "a" * (500 - len("https://luma.com/"))
    assert san.url_exibivel(url) == url


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        " https://luma.com/x",
        "https://luma.com/x y",
        "https://luma.com/x\n",
        "https://luma.com/\x07x",
        "https://luma.com\\x",
        "https://user:senha@luma.com/x",
        "https://@luma.com/x",
        "javascript:alert(1)",
        "ftp://luma.com/x",
        "https:///so-caminho",
        "https://[::1",
        "luma.com/x",
        "https://luma.com/" + "a" * 500,
    ],
)
def test_url_recusada(san: ModuleType, url: str | None) -> None:
    assert san.url_exibivel(url) is None


# --- texto_exibivel ------------------------------------------------------------------------


def test_texto_curto_sai_igual(san: ModuleType) -> None:
    assert san.texto_exibivel("Meetup de IA", 200) == "Meetup de IA"


def test_texto_uma_linha_so(san: ModuleType) -> None:
    assert san.texto_exibivel("  linha um\nlinha\tdois\r\n ", 200) == "linha um linha dois"


def test_texto_remove_controle_e_formatacao(san: ModuleType) -> None:
    assert san.texto_exibivel("a‮b\x07c​d", 200) == "abcd"


@pytest.mark.parametrize("texto", [None, "", "   ", "‮\x07", "\n\t"])
def test_texto_vazio_vira_none(san: ModuleType, texto: str | None) -> None:
    assert san.texto_exibivel(texto, 200) is None


def test_texto_longo_e_cortado_com_reticencias(san: ModuleType) -> None:
    cortado = san.texto_exibivel("a" * 10, 5)
    assert cortado == "aaaa…"
    assert san.tamanho_telegram(cortado) == 5


def test_corte_nao_quebra_par_substituto(san: ModuleType) -> None:
    cortado = san.texto_exibivel("😀" * 10, 6)
    assert cortado == "😀😀…"
    cortado.encode("utf-16-le")  # válido
    assert san.tamanho_telegram(cortado) <= 6


def test_texto_no_limite_nao_e_cortado(san: ModuleType) -> None:
    assert san.texto_exibivel("😀😀😀", 6) == "😀😀😀"


# --- tamanho_telegram ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "tamanho"), [("", 0), ("abc", 3), ("ã", 1), ("😀", 2), ("a😀b", 4), ("…", 1)]
)
def test_tamanho_em_utf16(san: ModuleType, texto: str, tamanho: int) -> None:
    assert san.tamanho_telegram(texto) == tamanho
