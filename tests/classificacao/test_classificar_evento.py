"""Testes do F1-07 — `classificar_evento`, a parte do classificador sem banco.

Assinaturas fixadas (radar.classificacao.classificador):
- `classificar_evento(evento: Evento, localizacao_filtro: str, cliente: ClienteLLM)
  -> ResultadoEvento`; uma chamada ao cliente, com o prompt carregado, `SCHEMA_SAIDA`,
  `montar_mensagem(evento, localizacao_filtro)[0]` e `max_tokens=1024`;
- `ResultadoEvento` (construível por palavra-chave): saida: SaidaClassificador | None,
  motivo_erro: str | None, tokens_entrada: int | None, tokens_saida: int | None,
  custo_usd: Decimal | None, erro_api: ErroAPI | None. Erro de API não é levantado: vira
  `erro_api` + `motivo_erro="api_<categoria>"`, sem tokens nem custo.
- `classificar_pendentes` passa a usar essa função (os testes do F1-06 continuam valendo).
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest

from .fabricas import evento_orm, saida_valida


@pytest.fixture
def classificador() -> ModuleType:
    return importlib.import_module("radar.classificacao.classificador")


@pytest.fixture
def cliente_mod() -> ModuleType:
    return importlib.import_module("radar.classificacao.cliente")


@dataclass
class ClienteFalso:
    resposta: Any
    chamadas: list[dict[str, Any]] = field(default_factory=list)

    def classificar(
        self, system: str, mensagem: str, schema: dict[str, Any], max_tokens: int
    ) -> Any:
        self.chamadas.append(
            {"system": system, "mensagem": mensagem, "schema": schema, "max_tokens": max_tokens}
        )
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


def _resposta(cliente_mod: ModuleType, texto: str | None = None) -> Any:
    return cliente_mod.RespostaLLM(
        texto=texto if texto is not None else json.dumps(saida_valida()),
        stop_reason="end_turn",
        tokens_entrada=1_000,
        tokens_saida=200,
    )


def test_classificacao_ok(classificador: ModuleType, cliente_mod: ModuleType) -> None:
    prompt = importlib.import_module("radar.classificacao.prompt")
    modelos = importlib.import_module("radar.classificacao.modelos")
    evento = evento_orm()
    cliente = ClienteFalso(_resposta(cliente_mod))

    resultado = classificador.classificar_evento(evento, "sp", cliente)

    assert isinstance(resultado, classificador.ResultadoEvento)
    assert isinstance(resultado.saida, modelos.SaidaClassificador)
    assert resultado.saida.model_dump(mode="json") == saida_valida()
    assert resultado.motivo_erro is None
    assert resultado.erro_api is None
    assert resultado.tokens_entrada == 1_000
    assert resultado.tokens_saida == 200
    assert resultado.custo_usd == Decimal("0.000200")
    [chamada] = cliente.chamadas
    assert chamada["system"] == prompt.carregar_prompt()
    assert chamada["schema"] == modelos.SCHEMA_SAIDA
    assert chamada["mensagem"] == prompt.montar_mensagem(evento, "sp")[0]
    assert chamada["max_tokens"] == 1024


def test_json_invalido(classificador: ModuleType, cliente_mod: ModuleType) -> None:
    cliente = ClienteFalso(_resposta(cliente_mod, texto="não é json"))
    resultado = classificador.classificar_evento(evento_orm(), "sp", cliente)
    assert resultado.saida is None
    assert resultado.motivo_erro == "saida_json_invalido"
    assert resultado.erro_api is None
    assert resultado.tokens_entrada == 1_000
    assert resultado.custo_usd == Decimal("0.000200")
    assert len(cliente.chamadas) == 1


def test_localizacao_conferida_com_o_filtro_recebido(
    classificador: ModuleType, cliente_mod: ModuleType
) -> None:
    # o filtro vem do argumento, não de `evento.localizacao`
    cliente = ClienteFalso(_resposta(cliente_mod))
    resultado = classificador.classificar_evento(
        evento_orm(localizacao="sp"), "indefinida", cliente
    )
    assert resultado.motivo_erro == "saida_localizacao_ausente"


@pytest.mark.parametrize(("categoria", "status"), [("servidor", 500), ("rate_limit", 429)])
def test_erro_de_api_e_registrado(
    classificador: ModuleType, cliente_mod: ModuleType, categoria: str, status: int
) -> None:
    erro = cliente_mod.ErroAPI(categoria=categoria, status_http=status)
    resultado = classificador.classificar_evento(evento_orm(), "sp", ClienteFalso(erro))
    assert resultado.saida is None
    assert resultado.erro_api is erro
    assert resultado.erro_api.categoria == categoria
    assert resultado.motivo_erro == f"api_{categoria}"
    assert resultado.tokens_entrada is None
    assert resultado.tokens_saida is None
    assert resultado.custo_usd is None


def test_nao_acessa_banco(
    classificador: ModuleType, cliente_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.orm import Session

    def proibido(*_: Any, **__: Any) -> None:
        raise AssertionError("classificar_evento não pode usar o banco")

    for metodo in ("add", "flush", "commit", "execute", "scalars", "scalar"):
        monkeypatch.setattr(Session, metodo, proibido)
    repositorio = importlib.import_module("radar.banco.repositorio")
    monkeypatch.setattr(repositorio, "eventos_para_classificar", proibido)

    resultado = classificador.classificar_evento(
        evento_orm(), "sp", ClienteFalso(_resposta(cliente_mod))
    )
    assert resultado.saida is not None
