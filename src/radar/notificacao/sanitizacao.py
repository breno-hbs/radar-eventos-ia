"""Sanitização do que é exibido no Telegram (D19).

As mensagens são texto puro (sem parse_mode): nada do evento vira markup. Ainda assim, todo
texto perde caracteres de controle (Cc) e de formatação invisível (Cf, ex.: U+202E, que
inverte a direção do texto) e vira uma linha só; URLs só são exibidas inteiras e se forem
http/https sem usuário/senha.
"""

from __future__ import annotations

import unicodedata
from urllib.parse import urlsplit

MAX_URL = 500
RETICENCIAS = "…"
_ESQUEMAS = ("http", "https")


def tamanho_telegram(texto: str) -> int:
    """Tamanho como o Telegram conta: unidades UTF-16."""
    return len(texto.encode("utf-16-le")) // 2


def _sem_invisiveis(texto: str) -> str:
    return "".join(c for c in texto if unicodedata.category(c) != "Cf")


def url_exibivel(url: str | None) -> str | None:
    """A URL como será exibida, ou None se não for segura para mostrar inteira."""
    if not url:
        return None
    limpa = _sem_invisiveis(url)
    if not limpa or len(limpa) > MAX_URL:
        return None
    if any(c.isspace() or unicodedata.category(c) == "Cc" or c == "\\" for c in limpa):
        return None
    try:
        partes = urlsplit(limpa)
        host = partes.hostname
    except ValueError:
        return None
    if partes.scheme.lower() not in _ESQUEMAS or "@" in partes.netloc or not host:
        return None
    return limpa


def _uma_linha(texto: str) -> str:
    saida = []
    for c in texto:
        if c.isspace():
            saida.append(" ")
        elif unicodedata.category(c) not in ("Cc", "Cf"):
            saida.append(c)
    return " ".join("".join(saida).split())


def texto_exibivel(texto: str | None, maximo: int) -> str | None:
    """Uma linha sem controles nem invisíveis, cortada em `maximo` unidades UTF-16 com "…"."""
    if texto is None:
        return None
    limpo = _uma_linha(texto)
    if not limpo:
        return None
    if tamanho_telegram(limpo) <= maximo:
        return limpo
    orcamento = maximo - tamanho_telegram(RETICENCIAS)
    partes: list[str] = []
    usado = 0
    for c in limpo:
        tamanho = tamanho_telegram(c)
        if usado + tamanho > orcamento:
            break
        partes.append(c)
        usado += tamanho
    return "".join(partes).rstrip() + RETICENCIAS
