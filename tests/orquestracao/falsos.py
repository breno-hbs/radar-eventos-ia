"""Dublês do F1-10b: rede da Luma (fixtures reais), Telegram (grava corpos) e LLM falso.

Nada sai da máquina: tudo passa por `httpx.MockTransport` ou por objetos em memória.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml
from pydantic import SecretStr

RAIZ = Path(__file__).resolve().parents[2]
FONTES = RAIZ / "config" / "fontes.yaml"
FIXTURES_LUMA = RAIZ / "tests" / "fixtures" / "luma"
AGORA = datetime(2026, 11, 25, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
TOKEN = "123456:SEGREDO-token-do-bot"
CHAT = 424242424
ABRE = "<evento_nao_confiavel>"
FECHA = "</evento_nao_confiavel>"


def calendarios() -> list[Any]:
    modulo = importlib.import_module("radar.fontes.calendarios")
    return modulo.carregar_calendarios(FONTES)


def _slug_por_id() -> dict[str, str]:
    dados = yaml.safe_load(FONTES.read_text(encoding="utf-8"))
    return {c["id"]: c["slug"] for c in dados["calendarios_luma"]}


# --- Luma ----------------------------------------------------------------------------------


@dataclass
class RedeLuma:
    """api.lu.ma (feeds das fixtures reais) e luma.com (páginas); robots 404 nos dois."""

    status_por_slug: dict[str, int] = field(default_factory=dict)
    requisicoes: list[httpx.Request] = field(default_factory=list)

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        self.requisicoes.append(requisicao)
        url = requisicao.url
        if url.path == "/robots.txt":
            return httpx.Response(404)
        if url.host == "api.lu.ma" and url.path == "/ics/get":
            slug = _slug_por_id().get(url.params.get("id", ""))
            if slug is None:
                return httpx.Response(404)
            status = self.status_por_slug.get(slug, 200)
            if status != 200:
                return httpx.Response(status)
            texto = (FIXTURES_LUMA / f"{slug}.ics").read_text(encoding="utf-8")
            return httpx.Response(200, text=texto, headers={"Content-Type": "text/calendar"})
        if url.host == "luma.com":
            texto = (FIXTURES_LUMA / "pagina_1.html").read_text(encoding="utf-8")
            return httpx.Response(200, text=texto, headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), follow_redirects=False)

    @property
    def paginas(self) -> list[httpx.Request]:
        return [r for r in self.requisicoes if r.url.host == "luma.com"]


# --- Telegram ------------------------------------------------------------------------------


@dataclass
class RedeTelegram:
    status: int = 200
    corpos: list[dict[str, Any]] = field(default_factory=list)
    respostas: list[int] = field(default_factory=list)  # status por chamada (depois `status`)

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        self.corpos.append(json.loads(requisicao.content))
        status = self.respostas.pop(0) if self.respostas else self.status
        if status == 200:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        corpo: dict[str, Any] = {"ok": False, "error_code": status, "description": "erro"}
        if status == 429:
            corpo["parameters"] = {"retry_after": 5}
        return httpx.Response(status, json=corpo)

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def cliente(self) -> Any:
        telegram = importlib.import_module("radar.notificacao.telegram")
        return telegram.ClienteTelegram(SecretStr(TOKEN), CHAT, self.http())


# --- LLM -----------------------------------------------------------------------------------


@dataclass
class LLMFalso:
    """Lê o JSON do delimitador e devolve uma saída válida com trecho = título.

    `e_ia(titulo)` decide e_ia (padrão: sempre True); `erro` é levantado em toda chamada.
    """

    e_ia: Callable[[str], bool] = field(default=lambda _titulo: True)
    erro: Exception | None = None
    chamadas: list[str] = field(default_factory=list)

    def classificar(
        self, system: str, mensagem: str, schema: dict[str, Any], max_tokens: int
    ) -> Any:
        cliente = importlib.import_module("radar.classificacao.cliente")
        if self.erro is not None:
            raise self.erro
        dentro = mensagem.split(ABRE, 1)[1].split(FECHA, 1)[0]
        dados = json.loads(dentro)
        titulo = str(dados["titulo"])
        self.chamadas.append(titulo)
        filtro = re.search(r"localizacao_filtro: (\w+)", mensagem)
        indefinida = filtro is not None and filtro.group(1) == "indefinida"
        saida = {
            "e_ia": self.e_ia(titulo),
            "categoria": "IA",
            "topicos": [],
            "tipo_evento": "meetup",
            "modalidade": "desconhecida",
            "trecho_modalidade": "",
            "idioma": "desconhecido",
            "tem_traducao": False,
            "trecho_idioma": "",
            "estado_preco": "preco_desconhecido",
            "trecho_preco": "",
            "localizacao": "desconhecida" if indefinida else "nao_se_aplica",
            "trecho_localizacao": "",
            "resumo": "Resumo sintético.",
            "trecho_evidencia": titulo[:80].strip(" …"),
        }
        return cliente.RespostaLLM(
            texto=json.dumps(saida, ensure_ascii=False),
            stop_reason="end_turn",
            tokens_entrada=100,
            tokens_saida=50,
        )
