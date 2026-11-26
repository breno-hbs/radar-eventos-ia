"""Testes do F1-09 — cliente da Bot API do Telegram (httpx.MockTransport, sem rede).

Assinaturas fixadas (radar.notificacao.telegram):
- `CategoriaErroTelegram = Literal["autenticacao", "proibido", "rate_limit", "requisicao",
  "servidor", "timeout", "conexao", "resposta_invalida"]`;
- `ErroTelegram(Exception)` construído por palavra-chave (categoria, status_http: int | None,
  retry_after: int | None); sem texto da resposta, sem URL, sem token;
- `ErroConfiguracaoTelegram(Exception)`;
- `EnviadorMensagens` (Protocol): `enviar_texto(texto: str) -> None`;
- `ClienteTelegram(token: SecretStr, chat_id: int, http: httpx.Client)`: `enviar_texto` faz
  POST https://api.telegram.org/bot<token>/sendMessage com JSON exatamente
  {"chat_id", "text", "link_preview_options": {"is_disabled": true}} (sem parse_mode);
  repr sem token nem chat_id;
- `criar_cliente_telegram(config, http) -> ClienteTelegram`: sem token ou chat_id →
  ErroConfiguracaoTelegram, sem requisição.
- Todo erro é levantado sem `__cause__` e sem `__context__` que contenha o token; um filtro
  nos loggers httpx/httpcore troca `/bot<token>/` por `/bot***/`.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from types import ModuleType
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from radar.config import Configuracao

TOKEN = "123456:SEGREDO-do-bot-AbCdEf"
CHAT = 987654321
OK = {"ok": True, "result": {"message_id": 1}}


@pytest.fixture
def tg() -> ModuleType:
    return importlib.import_module("radar.notificacao.telegram")


class Transporte:
    def __init__(self, tratador: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requisicoes: list[httpx.Request] = []
        self._tratador = tratador

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        self.requisicoes.append(requisicao)
        return self._tratador(requisicao)


def _http(transporte: Transporte) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(transporte))


def _cliente(tg: ModuleType, tratador: Callable[[httpx.Request], httpx.Response]) -> tuple:
    transporte = Transporte(tratador)
    return tg.ClienteTelegram(SecretStr(TOKEN), CHAT, _http(transporte)), transporte


def _sem_token(erro: BaseException) -> None:
    for texto in (str(erro), repr(erro), str(erro.args)):
        assert TOKEN not in texto
        assert "SEGREDO" not in texto
        assert "api.telegram.org" not in texto
    assert erro.__cause__ is None
    contexto = erro.__context__
    while contexto is not None:
        assert TOKEN not in repr(contexto) and TOKEN not in str(contexto)
        contexto = contexto.__context__


# --- Envio ---------------------------------------------------------------------------------


def test_envia_mensagem(tg: ModuleType) -> None:
    cliente, transporte = _cliente(tg, lambda _: httpx.Response(200, json=OK))
    assert cliente.enviar_texto("Olá <b>mundo</b> & cia") is None
    [requisicao] = transporte.requisicoes
    assert requisicao.method == "POST"
    assert str(requisicao.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(requisicao.content) == {
        "chat_id": CHAT,
        "text": "Olá <b>mundo</b> & cia",
        "link_preview_options": {"is_disabled": True},
    }


def test_repr_sem_token_nem_chat(tg: ModuleType) -> None:
    cliente, _ = _cliente(tg, lambda _: httpx.Response(200, json=OK))
    for texto in (repr(cliente), str(cliente)):
        assert TOKEN not in texto
        assert "SEGREDO" not in texto
        assert str(CHAT) not in texto


# --- Erros ---------------------------------------------------------------------------------


def _erro_api(status: int, **extra: Any) -> httpx.Response:
    corpo = {"ok": False, "error_code": status, "description": f"Erro SEGREDO-DESC {TOKEN}"}
    corpo.update(extra)
    return httpx.Response(status, json=corpo)


@pytest.mark.parametrize(
    ("status", "categoria"),
    [
        (401, "autenticacao"),
        (404, "autenticacao"),
        (403, "proibido"),
        (400, "requisicao"),
        (409, "requisicao"),
        (500, "servidor"),
        (502, "servidor"),
    ],
)
def test_erros_http(tg: ModuleType, status: int, categoria: str) -> None:
    cliente, transporte = _cliente(tg, lambda _: _erro_api(status))
    with pytest.raises(tg.ErroTelegram) as info:
        cliente.enviar_texto("oi")
    assert info.value.categoria == categoria
    assert info.value.status_http == status
    assert info.value.retry_after is None
    assert "SEGREDO-DESC" not in str(info.value)
    _sem_token(info.value)
    assert len(transporte.requisicoes) == 1


def test_rate_limit_com_retry_after(tg: ModuleType) -> None:
    cliente, _ = _cliente(tg, lambda _: _erro_api(429, parameters={"retry_after": 17}))
    with pytest.raises(tg.ErroTelegram) as info:
        cliente.enviar_texto("oi")
    assert (info.value.categoria, info.value.status_http, info.value.retry_after) == (
        "rate_limit",
        429,
        17,
    )


def test_rate_limit_sem_retry_after(tg: ModuleType) -> None:
    cliente, _ = _cliente(tg, lambda _: _erro_api(429))
    with pytest.raises(tg.ErroTelegram) as info:
        cliente.enviar_texto("oi")
    assert info.value.categoria == "rate_limit"
    assert info.value.retry_after is None


@pytest.mark.parametrize(
    ("excecao", "categoria"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectTimeout, "timeout"),
        (httpx.ConnectError, "conexao"),
        (httpx.RemoteProtocolError, "conexao"),
    ],
)
def test_erros_de_rede(tg: ModuleType, excecao: type[Exception], categoria: str) -> None:
    def tratador(requisicao: httpx.Request) -> httpx.Response:
        raise excecao(f"falhou em {requisicao.url}", request=requisicao)

    cliente, _ = _cliente(tg, tratador)
    with pytest.raises(tg.ErroTelegram) as info:
        cliente.enviar_texto("oi")
    assert info.value.categoria == categoria
    assert info.value.status_http is None
    _sem_token(info.value)


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(200, text="não é json"),
        httpx.Response(200, json={"ok": False}),
        httpx.Response(200, json={"result": {}}),
        httpx.Response(200, json=["ok"]),
        httpx.Response(200, json={"ok": "true"}),
    ],
    ids=["texto", "ok-false", "sem-ok", "lista", "ok-texto"],
)
def test_resposta_invalida(tg: ModuleType, resposta: httpx.Response) -> None:
    cliente, _ = _cliente(tg, lambda _: resposta)
    with pytest.raises(tg.ErroTelegram) as info:
        cliente.enviar_texto("oi")
    assert info.value.categoria == "resposta_invalida"
    _sem_token(info.value)


def test_erro_telegram_por_palavra_chave(tg: ModuleType) -> None:
    erro = tg.ErroTelegram(categoria="servidor", status_http=500, retry_after=None)
    assert isinstance(erro, Exception)
    assert (erro.categoria, erro.status_http, erro.retry_after) == ("servidor", 500, None)


# --- Configuração --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "campos",
    [
        {"telegram_token": None, "telegram_chat_id": CHAT},
        {"telegram_token": TOKEN, "telegram_chat_id": None},
        {"telegram_token": None, "telegram_chat_id": None},
    ],
)
def test_configuracao_incompleta(
    tg: ModuleType, monkeypatch: pytest.MonkeyPatch, campos: dict[str, Any]
) -> None:
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    transporte = Transporte(lambda _: httpx.Response(200, json=OK))
    with pytest.raises(tg.ErroConfiguracaoTelegram) as info:
        tg.criar_cliente_telegram(Configuracao(**campos), _http(transporte))
    assert transporte.requisicoes == []
    assert TOKEN not in str(info.value)


@pytest.mark.parametrize(
    "token",
    ["123:ABC\n", " 123:ABC", "123:ABC ", "123: ABC", "123:\tABC", "123:ABC\x00", "123:A\u200bBC"],
)
def test_token_com_espaco_ou_controle_e_recusado(tg: ModuleType, token: str) -> None:
    transporte = Transporte(lambda _: httpx.Response(200, json=OK))
    config = Configuracao(telegram_token=token, telegram_chat_id=CHAT)
    with pytest.raises(tg.ErroConfiguracaoTelegram) as info:
        tg.criar_cliente_telegram(config, _http(transporte))
    assert transporte.requisicoes == []
    assert "ABC" not in str(info.value)
    assert info.value.__cause__ is None


def test_url_invalida_vira_erro_de_requisicao(tg: ModuleType) -> None:
    def tratador(requisicao: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL(f"URL inválida: {requisicao.url}")

    cliente, _ = _cliente(tg, tratador)
    with pytest.raises(tg.ErroTelegram) as info:
        cliente.enviar_texto("oi")
    assert info.value.categoria == "requisicao"
    assert info.value.status_http is None
    _sem_token(info.value)


def test_criar_cliente_usa_o_chat_da_configuracao(tg: ModuleType) -> None:
    transporte = Transporte(lambda _: httpx.Response(200, json=OK))
    config = Configuracao(telegram_token=TOKEN, telegram_chat_id=CHAT)
    cliente = tg.criar_cliente_telegram(config, _http(transporte))
    assert transporte.requisicoes == []
    cliente.enviar_texto("oi")
    assert json.loads(transporte.requisicoes[0].content)["chat_id"] == CHAT


# --- Logs ----------------------------------------------------------------------------------


def test_token_mascarado_nos_logs(tg: ModuleType, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    cliente, _ = _cliente(tg, lambda _: httpx.Response(200, json=OK))
    cliente.enviar_texto("oi")
    with pytest.raises(tg.ErroTelegram):
        _cliente(tg, lambda _: _erro_api(500))[0].enviar_texto("oi")
    mensagens = [registro.getMessage() for registro in caplog.records]
    assert any("/bot***/" in m for m in mensagens)
    assert not any(TOKEN in m for m in mensagens)
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("nome", ["httpx", "httpcore", "httpcore.socks"])
def test_filtro_instalado_nos_loggers(tg: ModuleType, nome: str) -> None:
    logger = logging.getLogger(nome)
    registro = logger.makeRecord(
        nome,
        logging.DEBUG,
        __file__,
        1,
        "POST https://api.telegram.org/bot%s/sendMessage",
        (TOKEN,),
        None,
    )
    assert logger.filters, f"sem filtro no logger {nome}"
    for filtro in logger.filters:
        filtrar = filtro.filter if hasattr(filtro, "filter") else filtro
        filtrar(registro)
    assert TOKEN not in registro.getMessage()
    assert "/bot***/" in registro.getMessage()
