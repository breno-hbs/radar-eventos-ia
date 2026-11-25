"""Dados sintéticos do F1-07: feed iCal mínimo, rótulos JSONL, resultados e cliente falso."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from classificacao.fabricas import saida_valida

CALENDARIO = "cal-sintetico"
TRECHO_RESERVADO = "Texto reservado XYZ987"
DESCRICAO_SINTETICA = (
    f"Meetup sobre modelos de linguagem. Palestras em português. {TRECHO_RESERVADO}."
)


def vevento(id_fonte: str, titulo: str, descricao: str = DESCRICAO_SINTETICA) -> str:
    return "\n".join(
        [
            "BEGIN:VEVENT",
            "DTSTART:20261210T220000Z",
            "DTEND:20261211T000000Z",
            f"UID:{id_fonte}@events.lu.ma",
            f"SUMMARY:{titulo}",
            f"DESCRIPTION:{descricao}",
            "LOCATION:Av. Paulista\\, 1000 - São Paulo - SP",
            "GEO:-23.561234;-46.656789",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ]
    )


def escrever_ics(diretorio: Path, calendario: str, eventos: Iterable[str]) -> Path:
    caminho = diretorio / f"{calendario}.ics"
    corpo = "\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", *eventos, "END:VCALENDAR", ""])
    caminho.write_text(corpo, encoding="utf-8")
    return caminho


def rotulo(
    id_fonte: str, e_ia: bool = True, idioma: str = "pt", calendario: str = CALENDARIO
) -> dict[str, Any]:
    return {"calendario": calendario, "id_fonte": id_fonte, "e_ia": e_ia, "idioma": idioma}


def escrever_jsonl(caminho: Path, linhas: Sequence[dict[str, Any] | str]) -> Path:
    texto = "".join(
        (linha if isinstance(linha, str) else json.dumps(linha, ensure_ascii=False)) + "\n"
        for linha in linhas
    )
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def saida_sintetica(**campos: Any) -> dict[str, Any]:
    """Saída válida para os eventos de `vevento()` (filtro "sp" pelo GEO)."""
    base = saida_valida(
        modalidade="desconhecida",
        trecho_modalidade="",
        estado_preco="preco_desconhecido",
        trecho_preco="",
        localizacao="nao_se_aplica",
        trecho_localizacao="",
    )
    base.update(campos)
    return base


@dataclass
class Chamada:
    system: str
    mensagem: str
    schema: dict[str, Any]
    max_tokens: int


@dataclass
class ClienteFalso:
    """Devolve as respostas na ordem (exceção é levantada); depois repete `padrao`."""

    padrao: Any
    fila: list[Any] = field(default_factory=list)
    chamadas: list[Chamada] = field(default_factory=list)

    def classificar(
        self, system: str, mensagem: str, schema: dict[str, Any], max_tokens: int
    ) -> Any:
        self.chamadas.append(Chamada(system, mensagem, schema, max_tokens))
        item = self.fila.pop(0) if self.fila else self.padrao
        if isinstance(item, Exception):
            raise item
        return item
