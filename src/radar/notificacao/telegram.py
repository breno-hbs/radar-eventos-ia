"""Cliente mínimo da Bot API do Telegram com httpx (D19).

Só `sendMessage`, em texto puro e sem prévia de link. O token fica em `SecretStr`, a URL é
montada na hora da chamada e nunca aparece em repr, erro ou log: os erros são levantados FORA
do bloco `except` (sem `__context__` com a URL) e um filtro nos loggers do httpx/httpcore
troca `/bot<token>/` por `/bot***/`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Literal, Protocol

import httpx
from pydantic import SecretStr

from radar.config import Configuracao

API = "https://api.telegram.org"

CategoriaErroTelegram = Literal[
    "autenticacao",
    "proibido",
    "rate_limit",
    "requisicao",
    "servidor",
    "timeout",
    "conexao",
    "resposta_invalida",
]

_TOKEN_NA_URL = re.compile(r"/bot[^/\s]+/")
_LOGGERS = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)


class _FiltroToken(logging.Filter):
    """Mascara o token em qualquer mensagem de log que traga a URL da Bot API."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            mensagem = record.getMessage()
        except Exception:
            return True
        mascarada = _TOKEN_NA_URL.sub("/bot***/", mensagem)
        if mascarada != mensagem:
            record.msg = mascarada
            record.args = None
        return True


def _instalar_filtro() -> None:
    for nome in _LOGGERS:
        logger = logging.getLogger(nome)
        if not any(isinstance(f, _FiltroToken) for f in logger.filters):
            logger.addFilter(_FiltroToken())


_instalar_filtro()


class ErroTelegram(Exception):
    """Falha ao enviar; nunca carrega texto da resposta, URL nem token."""

    def __init__(
        self,
        *,
        categoria: CategoriaErroTelegram,
        status_http: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.categoria = categoria
        self.status_http = status_http
        self.retry_after = retry_after
        detalhe = f" (HTTP {status_http})" if status_http is not None else ""
        super().__init__(f"erro no Telegram: {categoria}{detalhe}")


class ErroConfiguracaoTelegram(Exception):
    """Token ou chat_id ausentes."""


class EnviadorMensagens(Protocol):
    def enviar_texto(self, texto: str) -> None: ...


def _categoria(status: int) -> CategoriaErroTelegram:
    if status in (401, 404):
        return "autenticacao"
    if status == 403:
        return "proibido"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "servidor"
    if 400 <= status < 500:
        return "requisicao"
    return "resposta_invalida"


def _retry_after(dados: Any) -> int | None:
    if not isinstance(dados, dict):
        return None
    parametros = dados.get("parameters")
    if not isinstance(parametros, dict):
        return None
    valor = parametros.get("retry_after")
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


class ClienteTelegram:
    """Envia texto para um único chat (o do autor)."""

    def __init__(self, token: SecretStr, chat_id: int, http: httpx.Client) -> None:
        self._token = token
        self._chat_id = chat_id
        self._http = http

    def __repr__(self) -> str:
        return "ClienteTelegram()"

    __str__ = __repr__

    def enviar_texto(self, texto: str) -> None:
        corpo = {
            "chat_id": self._chat_id,
            "text": texto,
            "link_preview_options": {"is_disabled": True},
        }
        erro: ErroTelegram | None = None
        resposta: httpx.Response | None = None
        try:
            resposta = self._http.post(
                f"{API}/bot{self._token.get_secret_value()}/sendMessage", json=corpo
            )
        except httpx.TimeoutException:
            erro = ErroTelegram(categoria="timeout")
        except httpx.HTTPError:
            erro = ErroTelegram(categoria="conexao")
        except httpx.InvalidURL:
            # não é HTTPError no httpx; a mensagem original traz a URL (com o token)
            erro = ErroTelegram(categoria="requisicao")
        # Levantado fora do `except`: sem __context__ com a URL (e o token).
        if erro is not None or resposta is None:
            raise erro or ErroTelegram(categoria="conexao")

        dados: Any = None
        try:
            dados = resposta.json()
        except ValueError:
            dados = None
        status = resposta.status_code
        if 200 <= status < 300:
            if isinstance(dados, dict) and dados.get("ok") is True:
                return
            raise ErroTelegram(categoria="resposta_invalida", status_http=status)
        categoria = _categoria(status)
        retry = _retry_after(dados) if categoria == "rate_limit" else None
        raise ErroTelegram(categoria=categoria, status_http=status, retry_after=retry)


def criar_cliente_telegram(config: Configuracao, http: httpx.Client) -> ClienteTelegram:
    """Cliente com o token e o chat da configuração; incompleta → ErroConfiguracaoTelegram."""
    if config.telegram_token is None or config.telegram_chat_id is None:
        raise ErroConfiguracaoTelegram("TELEGRAM_TOKEN e TELEGRAM_CHAT_ID precisam estar definidos")
    token = config.telegram_token.get_secret_value()
    if any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf") for c in token):
        # Espaço/controle no token quebraria a URL; a mensagem nunca traz o token.
        raise ErroConfiguracaoTelegram("TELEGRAM_TOKEN com espaço ou caractere de controle")
    return ClienteTelegram(config.telegram_token, config.telegram_chat_id, http)
