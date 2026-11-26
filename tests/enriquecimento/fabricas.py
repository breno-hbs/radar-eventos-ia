"""Páginas sintéticas no formato do JSON-LD real da Luma (tests/fixtures/luma/pagina_1.html)."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
FIXTURES = RAIZ / "tests" / "fixtures" / "enriquecimento"
PAGINA_REAL = RAIZ / "tests" / "fixtures" / "luma" / "pagina_1.html"

ORGANIZACAO = {
    "@context": "https://schema.org",
    "@type": "Organization",
    "name": "Luma",
    "url": "https://luma.com",
}
ISCA = {"offers": [{"price": 999, "priceCurrency": "BRL"}]}
SEM = object()


def _base() -> dict[str, Any]:
    texto = PAGINA_REAL.read_text(encoding="utf-8")
    bloco = re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', texto, re.S)
    assert bloco is not None
    return json.loads(bloco.group(1))


def oferta(price: Any = 0, moeda: Any = "BRL") -> dict[str, Any]:
    item: dict[str, Any] = {
        "@type": "Offer",
        "name": "Ingresso",
        "availability": "https://schema.org/InStock",
        "url": "https://luma.com/encontro-sintetico",
    }
    if price is not SEM:
        item["price"] = price
    if moeda is not SEM:
        item["priceCurrency"] = moeda
    return item


def evento_ld(offers: Any = SEM, tipo: Any = "Event", nome: str = "Encontro sintético") -> dict:
    dados = copy.deepcopy(_base())
    dados["@type"] = tipo
    dados["name"] = nome
    dados["url"] = "https://luma.com/encontro-sintetico"
    if offers is SEM:
        dados.pop("offers", None)
    else:
        dados["offers"] = offers
    return dados


def bloco(dados: Any) -> str:
    # "</" escapado como "<\\/" (JSON válido), como faz quem embute JSON-LD em HTML
    texto = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    return '<script type="application/ld+json">' + texto + "</script>"


def pagina(*blocos: str, isca: bool = True) -> str:
    """Documento mínimo: JSON-LD Organization, os blocos dados e uma isca application/json."""
    partes = [
        "<!DOCTYPE html>",
        '<html lang="pt-BR"><head><meta charset="utf-8"><title>Encontro</title>',
        bloco(ORGANIZACAO),
        *blocos,
    ]
    if isca:
        partes.append('<script type="application/json">' + json.dumps(ISCA) + "</script>")
    partes.append("</head><body><h1>Encontro</h1></body></html>")
    return "\n".join(partes)


def pagina_com(offers: Any = SEM, **campos: Any) -> str:
    return pagina(bloco(evento_ld(offers, **campos)))


def fixture(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")
