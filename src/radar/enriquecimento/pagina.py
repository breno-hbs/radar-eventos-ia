"""Interpretação da página do evento: JSON-LD schema.org e ofertas (D4, D18).

Puro, sem rede. Usa o `html.parser` da biblioteca padrão (sem BeautifulSoup, D18). Só os
blocos `<script type="application/ld+json">` são lidos, com limites de quantidade e tamanho;
o texto da página nunca sai daqui (só o estado, o valor e um motivo fechado).
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from radar.enriquecimento.modelos import ResultadoPreco

MAX_BLOCOS = 20
MAX_BYTES_BLOCO = 256 * 1024
TIPO_JSON_LD = "application/ld+json"
MOEDA_BRL = "BRL"
CENTAVO = Decimal("0.01")
# Teto de sanidade (D18): preço acima disso é tratado como inválido, nunca gravado.
MAX_PRECO = Decimal(1_000_000)
# Só dígitos ASCII: `\d` aceitaria dígitos Unicode (árabe-índicos, largura total).
_PRECO_TEXTO = re.compile(r"[0-9]+(\.[0-9]{1,2})?")


class _ExtratorJsonLd(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocos: list[str] = []
        self._dentro = False
        self._partes: list[str] = []
        self._tamanho = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        tipo = next((v for k, v in attrs if k == "type"), None) or ""
        self._dentro = tipo.strip().casefold() == TIPO_JSON_LD
        self._partes = []
        self._tamanho = 0

    def handle_data(self, data: str) -> None:
        if not self._dentro or self._tamanho > MAX_BYTES_BLOCO:
            return
        self._tamanho += len(data.encode("utf-8"))
        if self._tamanho <= MAX_BYTES_BLOCO:
            self._partes.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or not self._dentro:
            return
        self._dentro = False
        if self._tamanho <= MAX_BYTES_BLOCO and len(self.blocos) < MAX_BLOCOS:
            self.blocos.append("".join(self._partes))
        self._partes = []


def extrair_blocos_json_ld(html: str) -> list[str]:
    """Conteúdo dos `<script type="application/ld+json">`, no máximo 20; bloco grande é pulado."""
    extrator = _ExtratorJsonLd()
    try:
        extrator.feed(html)
        extrator.close()
    except Exception:
        # HTML que derruba o parser: fica o que já foi lido.
        pass
    return extrator.blocos[:MAX_BLOCOS]


def _recusar_constante(nome: str) -> Any:
    raise ValueError("constante não JSON")


def _ler_json(bloco: str) -> Any:
    """JSON estrito (sem NaN/Infinity); erro de sintaxe ou aninhamento → ValueError."""
    try:
        return json.loads(bloco, parse_constant=_recusar_constante)
    except (ValueError, RecursionError) as erro:
        raise ValueError("json_ld_invalido") from erro


def _e_evento(no: Any) -> bool:
    if not isinstance(no, dict):
        return False
    tipo = no.get("@type")
    tipos = tipo if isinstance(tipo, list) else [tipo]
    return any(isinstance(t, str) and t.endswith("Event") for t in tipos)


def _candidatos(dados: Any) -> list[Any]:
    if isinstance(dados, list):
        return dados
    if isinstance(dados, dict) and isinstance(dados.get("@graph"), list):
        return [dados, *dados["@graph"]]
    return [dados]


def _desconhecido(motivo: str) -> ResultadoPreco:
    return ResultadoPreco(estado_preco="preco_desconhecido", motivo=motivo)  # type: ignore[arg-type]


def interpretar_pagina(html: str) -> ResultadoPreco:
    """Preço pelo primeiro objeto de tipo terminado em "Event" nos blocos JSON-LD."""
    invalido = False
    for bloco in extrair_blocos_json_ld(html):
        try:
            dados = _ler_json(bloco)
        except ValueError:
            invalido = True
            continue
        for no in _candidatos(dados):
            if _e_evento(no):
                return estado_por_ofertas(no.get("offers"))
    return _desconhecido("json_ld_invalido" if invalido else "sem_json_ld")


def _preco(valor: Any) -> Decimal | None:
    """Preço entre 0 e R$ 1.000.000 com até 2 casas; qualquer outra coisa → None."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        decimal = Decimal(valor)
    elif isinstance(valor, float):
        if not math.isfinite(valor):
            return None
        decimal = Decimal(repr(valor))
    elif isinstance(valor, str):
        texto = valor.strip()
        if not _PRECO_TEXTO.fullmatch(texto):
            return None
        decimal = Decimal(texto)
    else:
        return None
    try:
        if decimal < 0 or decimal > MAX_PRECO or decimal != decimal.quantize(CENTAVO):
            return None
    except InvalidOperation:
        return None
    return decimal


def _moeda(valor: Any) -> str | None:
    return valor.strip().upper() if isinstance(valor, str) else None


def estado_por_ofertas(ofertas: object) -> ResultadoPreco:
    """Estado pelo valor cru de `offers` (D4 + decisão do autor, D18).

    Ofertas com o mesmo preço contam como uma. Preço 0 → gratuito em qualquer moeda; preço
    único > 0 com todas as ofertas em BRL → pago com valor; sem BRL → pago sem valor; preços
    diferentes → variável. Uma oferta inválida invalida tudo.
    """
    if isinstance(ofertas, dict):
        lista: list[Any] = [ofertas]
    elif isinstance(ofertas, list):
        lista = ofertas
    else:
        return _desconhecido("sem_offers")
    if not lista:
        return _desconhecido("sem_offers")

    precos: list[Decimal] = []
    moedas: list[str | None] = []
    for item in lista:
        if not isinstance(item, dict):
            return _desconhecido("oferta_invalida")
        preco = _preco(item.get("price"))
        if preco is None:
            return _desconhecido("oferta_invalida")
        precos.append(preco)
        moedas.append(_moeda(item.get("priceCurrency")))

    distintos = set(precos)
    if len(distintos) > 1:
        return ResultadoPreco(estado_preco="preco_variavel")
    [preco] = distintos
    if preco == 0:
        return ResultadoPreco(estado_preco="gratuito_confirmado")
    if all(moeda == MOEDA_BRL for moeda in moedas):
        return ResultadoPreco(
            estado_preco="pago_com_valor", valor_centavos=int(preco * 100), moeda=MOEDA_BRL
        )
    return ResultadoPreco(estado_preco="pago_sem_valor")
