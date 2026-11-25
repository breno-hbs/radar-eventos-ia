"""Adaptador do SDK da Anthropic (D13): uma requisição por chamada, sem retries do SDK.

Os erros do SDK viram `ErroAPI` com uma categoria fechada; a mensagem nunca carrega a chave
nem o corpo da resposta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import anthropic
import httpx2

from radar.classificacao import modelos
from radar.config import Configuracao

TIMEOUT_SEGUNDOS = 30.0

CategoriaErroAPI = Literal[
    "autenticacao", "rate_limit", "servidor", "timeout", "conexao", "requisicao", "outro"
]


@dataclass(frozen=True)
class RespostaLLM:
    texto: str
    stop_reason: str | None
    tokens_entrada: int | None
    tokens_saida: int | None


class ErroAPI(Exception):
    """Falha ao chamar a API, sem detalhes do provedor (que podem ecoar dados)."""

    def __init__(self, *, categoria: CategoriaErroAPI, status_http: int | None = None) -> None:
        self.categoria = categoria
        self.status_http = status_http
        detalhe = f" (HTTP {status_http})" if status_http is not None else ""
        super().__init__(f"erro na API do LLM: {categoria}{detalhe}")


class ClienteLLM(Protocol):
    def classificar(
        self, system: str, mensagem: str, schema: dict[str, Any], max_tokens: int
    ) -> RespostaLLM: ...


def _categoria_status(status: int) -> CategoriaErroAPI:
    if status in (401, 403):
        return "autenticacao"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "servidor"
    if 400 <= status < 500:
        return "requisicao"
    return "outro"


class ClienteAnthropic:
    """Implementação de `ClienteLLM` com o SDK oficial."""

    def __init__(self, sdk: anthropic.Anthropic) -> None:
        self._sdk = sdk

    def __repr__(self) -> str:
        return "ClienteAnthropic()"

    def classificar(
        self, system: str, mensagem: str, schema: dict[str, Any], max_tokens: int
    ) -> RespostaLLM:
        try:
            resposta = self._sdk.messages.create(
                model=modelos.MODELO,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": mensagem}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APITimeoutError:
            raise ErroAPI(categoria="timeout") from None
        except anthropic.APIConnectionError:
            raise ErroAPI(categoria="conexao") from None
        except anthropic.APIStatusError as erro:
            status = erro.status_code
            raise ErroAPI(categoria=_categoria_status(status), status_http=status) from None
        except anthropic.APIError:
            raise ErroAPI(categoria="outro") from None
        except anthropic.AnthropicError:
            # Erro do próprio SDK, fora da hierarquia de APIError.
            raise ErroAPI(categoria="outro") from None

        texto = "".join(
            bloco.text for bloco in resposta.content if getattr(bloco, "type", None) == "text"
        )
        uso = resposta.usage
        return RespostaLLM(
            texto=texto,
            stop_reason=resposta.stop_reason,
            tokens_entrada=getattr(uso, "input_tokens", None),
            tokens_saida=getattr(uso, "output_tokens", None),
        )


def criar_cliente_anthropic(
    config: Configuracao, *, http_client: httpx2.Client | None = None
) -> ClienteAnthropic:
    """Cliente com `max_retries=0` e timeout de 30 s. `http_client` existe só para testes."""
    if config.anthropic_api_key is None:
        raise ValueError("ANTHROPIC_API_KEY não configurada")
    opcoes: dict[str, Any] = {}
    if http_client is not None:
        opcoes["http_client"] = http_client
    sdk = anthropic.Anthropic(
        api_key=config.anthropic_api_key.get_secret_value(),
        max_retries=0,
        timeout=TIMEOUT_SEGUNDOS,
        **opcoes,
    )
    return ClienteAnthropic(sdk)
