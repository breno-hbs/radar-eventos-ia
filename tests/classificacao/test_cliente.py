"""Testes do F1-06 — adaptador do SDK da Anthropic (sem rede: `httpx2.MockTransport`).

Assinaturas fixadas (radar.classificacao.cliente):
- `RespostaLLM(texto: str, stop_reason: str | None, tokens_entrada: int | None,
  tokens_saida: int | None)`;
- `ErroAPI(Exception)` com `categoria` ("autenticacao", "rate_limit", "servidor", "timeout",
  "conexao", "requisicao", "outro") e `status_http: int | None`;
- `ClienteLLM` (Protocol): `classificar(system, mensagem, schema, max_tokens) -> RespostaLLM`;
- `criar_cliente_anthropic(config: Configuracao, *, http_client: httpx2.Client | None = None)
  -> ClienteAnthropic`: sem `anthropic_api_key` → ValueError (sem segredo na mensagem);
  `anthropic.Anthropic(max_retries=0, timeout=30.0)`; `http_client` só existe para os testes
  injetarem `anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(...))`.
- A chamada usa `messages.create(model=MODELO, max_tokens=..., system=...,
  messages=[{"role": "user", "content": mensagem}],
  output_config={"format": {"type": "json_schema", "schema": schema}})`.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest

from radar.config import Configuracao


def _sdk() -> tuple[Any, Any]:
    """(anthropic, httpx2), importados por teste: sem a dependência no pyproject o teste
    falha (não é pulado)."""
    return importlib.import_module("anthropic"), importlib.import_module("httpx2")


CHAVE = "sk-ant-teste-NAO-VAZAR-123"
SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

Tratador = Callable[[Any], Any]


@pytest.fixture
def cliente_mod() -> ModuleType:
    return importlib.import_module("radar.classificacao.cliente")


def _config(chave: str | None = CHAVE) -> Configuracao:
    return Configuracao(anthropic_api_key=chave)


def _resposta_ok(
    texto: str = '{"ok": true}',
    stop_reason: str | None = "end_turn",
    usage: dict[str, int] | None = None,
    conteudo: list[dict[str, Any]] | None = None,
) -> Any:
    _, httpx2 = _sdk()
    return httpx2.Response(
        200,
        json={
            "id": "msg_teste",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": conteudo if conteudo is not None else [{"type": "text", "text": texto}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage or {"input_tokens": 1234, "output_tokens": 56},
        },
    )


def _erro_http(status: int) -> Any:
    _, httpx2 = _sdk()
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": "erro_teste", "message": "falhou"}}
    )


class Transporte:
    """Grava as requisições e responde com o tratador dado."""

    def __init__(self, tratador: Tratador) -> None:
        self.requisicoes: list[Any] = []
        self._tratador = tratador

    def __call__(self, requisicao: Any) -> Any:
        self.requisicoes.append(requisicao)
        return self._tratador(requisicao)


def _cliente(cliente_mod: ModuleType, tratador: Tratador) -> tuple[Any, Transporte]:
    anthropic, httpx2 = _sdk()
    transporte = Transporte(tratador)
    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(transporte))
    return cliente_mod.criar_cliente_anthropic(_config(), http_client=http), transporte


def _classificar(cliente: Any) -> Any:
    return cliente.classificar(
        system="instruções do sistema", mensagem="mensagem do evento", schema=SCHEMA, max_tokens=900
    )


# --- Criação -------------------------------------------------------------------------------


def test_sem_chave_e_erro(cliente_mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError):
        cliente_mod.criar_cliente_anthropic(Configuracao(anthropic_api_key=None))


def test_cliente_nao_mostra_a_chave(cliente_mod: ModuleType) -> None:
    cliente = cliente_mod.criar_cliente_anthropic(_config())
    assert CHAVE not in repr(cliente)
    assert CHAVE not in str(cliente)


# --- Requisição ----------------------------------------------------------------------------


def test_requisicao_enviada(cliente_mod: ModuleType) -> None:
    cliente, transporte = _cliente(cliente_mod, lambda _: _resposta_ok())
    _classificar(cliente)
    assert len(transporte.requisicoes) == 1
    requisicao = transporte.requisicoes[0]
    assert requisicao.url.path == "/v1/messages"
    corpo = json.loads(requisicao.content)
    assert corpo["model"] == "claude-haiku-5-5"
    assert corpo["max_tokens"] == 900
    assert corpo["system"] == "instruções do sistema"
    assert corpo["messages"] == [{"role": "user", "content": "mensagem do evento"}]
    assert corpo["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


def test_timeout_de_30_segundos(cliente_mod: ModuleType) -> None:
    cliente, transporte = _cliente(cliente_mod, lambda _: _resposta_ok())
    _classificar(cliente)
    timeout = transporte.requisicoes[0].extensions["timeout"]
    assert timeout["read"] == pytest.approx(30.0)
    assert timeout["connect"] <= 30.0


@pytest.mark.parametrize("status", [429, 500, 529])
def test_sem_novas_tentativas(cliente_mod: ModuleType, status: int) -> None:
    cliente, transporte = _cliente(cliente_mod, lambda _: _erro_http(status))
    with pytest.raises(cliente_mod.ErroAPI):
        _classificar(cliente)
    assert len(transporte.requisicoes) == 1


def test_thinking_desligado_e_sem_parametros_de_amostragem(cliente_mod: ModuleType) -> None:
    # Haiku 5.5 usa thinking adaptativo sem o campo `thinking`; os tokens de thinking contam
    # no max_tokens e cortavam a resposta (F1-07b).
    cliente, transporte = _cliente(cliente_mod, lambda _: _resposta_ok())
    _classificar(cliente)
    _classificar(cliente)
    for requisicao in transporte.requisicoes:
        corpo = json.loads(requisicao.content)
        assert corpo["thinking"] == {"type": "disabled"}
        assert "effort" not in corpo.get("output_config", {})
        assert "effort" not in corpo
        for campo in ("temperature", "top_p", "top_k"):
            assert campo not in corpo


# --- Resposta ------------------------------------------------------------------------------


def test_le_texto_stop_reason_e_usage(cliente_mod: ModuleType) -> None:
    cliente, _ = _cliente(
        cliente_mod,
        lambda _: _resposta_ok(
            texto='{"e_ia": true}', usage={"input_tokens": 1500, "output_tokens": 80}
        ),
    )
    resposta = _classificar(cliente)
    assert isinstance(resposta, cliente_mod.RespostaLLM)
    assert resposta.texto == '{"e_ia": true}'
    assert resposta.stop_reason == "end_turn"
    assert resposta.tokens_entrada == 1500
    assert resposta.tokens_saida == 80


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_stop_reason_repassado(cliente_mod: ModuleType, stop_reason: str) -> None:
    cliente, _ = _cliente(cliente_mod, lambda _: _resposta_ok(stop_reason=stop_reason))
    assert _classificar(cliente).stop_reason == stop_reason


def test_resposta_sem_bloco_de_texto(cliente_mod: ModuleType) -> None:
    cliente, _ = _cliente(cliente_mod, lambda _: _resposta_ok(stop_reason="refusal", conteudo=[]))
    resposta = _classificar(cliente)
    assert resposta.texto == ""
    assert resposta.stop_reason == "refusal"


def test_bloco_de_thinking_e_ignorado(cliente_mod: ModuleType) -> None:
    conteudo = [
        {"type": "thinking", "thinking": "", "signature": "x"},
        {"type": "text", "text": '{"e_ia": true}'},
    ]
    cliente, _ = _cliente(cliente_mod, lambda _: _resposta_ok(conteudo=conteudo))
    assert _classificar(cliente).texto == '{"e_ia": true}'


# --- Erros ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "categoria"),
    [
        (401, "autenticacao"),
        (429, "rate_limit"),
        (500, "servidor"),
        (529, "servidor"),
        (400, "requisicao"),
    ],
)
def test_erros_http_mapeados(cliente_mod: ModuleType, status: int, categoria: str) -> None:
    cliente, _ = _cliente(cliente_mod, lambda _: _erro_http(status))
    with pytest.raises(cliente_mod.ErroAPI) as info:
        _classificar(cliente)
    assert info.value.categoria == categoria
    assert info.value.status_http == status
    assert CHAVE not in str(info.value)
    assert CHAVE not in repr(info.value)


def test_timeout_mapeado(cliente_mod: ModuleType) -> None:
    _, httpx2 = _sdk()

    def tratador(requisicao: Any) -> Any:
        raise httpx2.ReadTimeout("demorou", request=requisicao)

    cliente, _ = _cliente(cliente_mod, tratador)
    with pytest.raises(cliente_mod.ErroAPI) as info:
        _classificar(cliente)
    assert info.value.categoria == "timeout"
    assert info.value.status_http is None


def test_conexao_mapeada(cliente_mod: ModuleType) -> None:
    _, httpx2 = _sdk()

    def tratador(requisicao: Any) -> Any:
        raise httpx2.ConnectError("sem rede", request=requisicao)

    cliente, _ = _cliente(cliente_mod, tratador)
    with pytest.raises(cliente_mod.ErroAPI) as info:
        _classificar(cliente)
    assert info.value.categoria == "conexao"
    assert info.value.status_http is None


def test_erro_api_e_exception(cliente_mod: ModuleType) -> None:
    erro = cliente_mod.ErroAPI(categoria="servidor", status_http=503)
    assert isinstance(erro, Exception)
    assert erro.categoria == "servidor"
    assert erro.status_http == 503


class _MensagensQueFalham:
    def __init__(self, erro: Exception) -> None:
        self._erro = erro

    def create(self, **_: Any) -> Any:
        raise self._erro


class _SdkFalso:
    """Objeto no lugar de `anthropic.Anthropic`: só `messages.create`, que levanta."""

    def __init__(self, erro: Exception) -> None:
        self.messages = _MensagensQueFalham(erro)


def test_anthropic_error_generica_vira_outro(cliente_mod: ModuleType) -> None:
    anthropic, _ = _sdk()
    erro_sdk = anthropic.AnthropicError("falha interna do SDK")
    assert not isinstance(erro_sdk, anthropic.APIError)
    cliente = cliente_mod.ClienteAnthropic(_SdkFalso(erro_sdk))
    with pytest.raises(cliente_mod.ErroAPI) as info:
        _classificar(cliente)
    assert info.value.categoria == "outro"
    assert info.value.status_http is None
