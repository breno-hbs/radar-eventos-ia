"""Testes do F1-06 — modelo de saída do classificador e schema JSON enviado ao LLM.

Assinaturas fixadas (radar.classificacao.modelos):
- `MODELO = "claude-haiku-5-5"`, `VERSAO_SCHEMA = "1"`;
- `SaidaClassificador` (Pydantic, extra="forbid", frozen) com os campos do F1-06;
- `SCHEMA_SAIDA: dict` derivado do modelo, plano (sem $ref/$defs/oneOf/anyOf/allOf e sem
  palavras min*/max*), `additionalProperties: false` em todo objeto;
- `SHA256_SCHEMA_V1`: sha256 de
  `json.dumps(SCHEMA_SAIDA, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`
  em UTF-8. Mudar o schema exige mudar a versão (e a constante).

Regras de evidência (CLAUDE.md: sem evidência o estado é "desconhecido"):
- modalidade "desconhecida" <=> trecho_modalidade vazio;
- idioma em {"pt","en","bilingue","outro","desconhecido"}; "outro" = idioma estrangeiro
  confirmado que não é pt nem en; "bilingue" = pt + outro idioma;
- (idioma "desconhecido" e tem_traducao False) <=> trecho_idioma vazio;
- estado_preco "preco_desconhecido" <=> trecho_preco vazio;
- localizacao em {"desconhecida", "nao_se_aplica"} <=> trecho_localizacao vazio;
- trecho_evidencia obrigatório (1–300 após strip).
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from .fabricas import saida_valida


@pytest.fixture
def modelos() -> ModuleType:
    return importlib.import_module("radar.classificacao.modelos")


def _criar(modelos: ModuleType, **campos: Any) -> Any:
    return modelos.SaidaClassificador.model_validate(saida_valida(**campos))


def _invalido(modelos: ModuleType, **campos: Any) -> None:
    with pytest.raises(ValidationError):
        _criar(modelos, **campos)


# --- Constantes ----------------------------------------------------------------------------


def test_constantes(modelos: ModuleType) -> None:
    assert modelos.MODELO == "claude-haiku-5-5"
    assert modelos.VERSAO_SCHEMA == "1"


# --- Saída válida --------------------------------------------------------------------------


def test_saida_valida(modelos: ModuleType) -> None:
    saida = _criar(modelos)
    assert saida.e_ia is True
    assert saida.tipo_evento == "meetup"
    assert saida.topicos == ["LLM", "agentes"]
    assert saida.model_dump(mode="json") == saida_valida()


def test_saida_e_imutavel(modelos: ModuleType) -> None:
    saida = _criar(modelos)
    with pytest.raises(ValidationError):
        saida.e_ia = False


def test_campo_extra_e_recusado(modelos: ModuleType) -> None:
    _invalido(modelos, nota="10")


def test_campo_faltando_e_recusado(modelos: ModuleType) -> None:
    dados = saida_valida()
    del dados["trecho_evidencia"]
    with pytest.raises(ValidationError):
        modelos.SaidaClassificador.model_validate(dados)


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("tipo_evento", "festa"),
        ("modalidade", "remoto"),
        ("idioma", "es"),
        ("idioma", "espanhol"),
        ("estado_preco", "barato"),
        ("localizacao", "rj"),
    ],
)
def test_enum_invalido(modelos: ModuleType, campo: str, valor: str) -> None:
    _invalido(modelos, **{campo: valor})


@pytest.mark.parametrize(
    "tipo", ["meetup", "palestra", "hackathon", "conferencia", "workshop", "outro"]
)
def test_tipos_de_evento_aceitos(modelos: ModuleType, tipo: str) -> None:
    assert _criar(modelos, tipo_evento=tipo).tipo_evento == tipo


# --- Tamanhos e linhas ---------------------------------------------------------------------


def test_resumo_com_200_caracteres_e_aceito(modelos: ModuleType) -> None:
    assert len(_criar(modelos, resumo="r" * 200).resumo) == 200


@pytest.mark.parametrize("resumo", ["r" * 201, "", "   ", "linha um\nlinha dois", "a\rb"])
def test_resumo_invalido(modelos: ModuleType, resumo: str) -> None:
    _invalido(modelos, resumo=resumo)


@pytest.mark.parametrize("categoria", ["", "c" * 41])
def test_categoria_invalida(modelos: ModuleType, categoria: str) -> None:
    _invalido(modelos, categoria=categoria)


def test_categoria_com_40_caracteres_e_aceita(modelos: ModuleType) -> None:
    assert len(_criar(modelos, categoria="c" * 40).categoria) == 40


@pytest.mark.parametrize("quantidade", [0, 5])
def test_topicos_de_0_a_5(modelos: ModuleType, quantidade: int) -> None:
    topicos = [f"t{i}" for i in range(quantidade)]
    assert _criar(modelos, topicos=topicos).topicos == topicos


@pytest.mark.parametrize(
    "topicos",
    [
        [f"t{i}" for i in range(6)],
        [""],
        ["x" * 41],
        ["dois\ntopicos"],
    ],
)
def test_topicos_invalidos(modelos: ModuleType, topicos: list[str]) -> None:
    _invalido(modelos, topicos=topicos)


@pytest.mark.parametrize(
    "campo",
    [
        "trecho_modalidade",
        "trecho_idioma",
        "trecho_preco",
        "trecho_localizacao",
        "trecho_evidencia",
    ],
)
def test_trecho_com_mais_de_300_e_recusado(modelos: ModuleType, campo: str) -> None:
    _invalido(modelos, **{campo: "x" * 301})


def test_trecho_com_300_e_aceito(modelos: ModuleType) -> None:
    assert len(_criar(modelos, trecho_evidencia="x" * 300).trecho_evidencia) == 300


def test_trechos_sao_aparados(modelos: ModuleType) -> None:
    saida = _criar(modelos, trecho_evidencia="  modelos de linguagem \n", trecho_preco=" grátis ")
    assert saida.trecho_evidencia == "modelos de linguagem"
    assert saida.trecho_preco == "grátis"


@pytest.mark.parametrize("trecho", ["", "   "])
def test_trecho_evidencia_vazio_e_recusado(modelos: ModuleType, trecho: str) -> None:
    _invalido(modelos, trecho_evidencia=trecho)


# --- Regras trecho <=> desconhecido --------------------------------------------------------


def test_modalidade_desconhecida_sem_trecho_e_aceita(modelos: ModuleType) -> None:
    saida = _criar(modelos, modalidade="desconhecida", trecho_modalidade="")
    assert saida.modalidade == "desconhecida"


def test_modalidade_desconhecida_com_trecho_e_recusada(modelos: ModuleType) -> None:
    _invalido(modelos, modalidade="desconhecida", trecho_modalidade="Presencial")


@pytest.mark.parametrize("modalidade", ["presencial", "online", "hibrido"])
def test_modalidade_conhecida_sem_trecho_e_recusada(modelos: ModuleType, modalidade: str) -> None:
    _invalido(modelos, modalidade=modalidade, trecho_modalidade="  ")


def test_idioma_desconhecido_sem_traducao_e_sem_trecho_e_aceito(modelos: ModuleType) -> None:
    saida = _criar(modelos, idioma="desconhecido", tem_traducao=False, trecho_idioma="")
    assert saida.idioma == "desconhecido"


def test_idioma_desconhecido_sem_traducao_com_trecho_e_recusado(modelos: ModuleType) -> None:
    _invalido(modelos, idioma="desconhecido", tem_traducao=False, trecho_idioma="em português")


def test_traducao_sem_trecho_e_recusada(modelos: ModuleType) -> None:
    _invalido(modelos, idioma="desconhecido", tem_traducao=True, trecho_idioma="")


def test_idioma_desconhecido_com_traducao_e_trecho_e_aceito(modelos: ModuleType) -> None:
    saida = _criar(
        modelos, idioma="desconhecido", tem_traducao=True, trecho_idioma="tradução simultânea"
    )
    assert saida.tem_traducao is True


@pytest.mark.parametrize("idioma", ["pt", "en", "bilingue", "outro"])
def test_idioma_conhecido_sem_trecho_e_recusado(modelos: ModuleType, idioma: str) -> None:
    _invalido(modelos, idioma=idioma, trecho_idioma="")


def test_idioma_outro_com_trecho_e_aceito(modelos: ModuleType) -> None:
    # "outro" = idioma estrangeiro confirmado que não é pt nem en (não é "desconhecido").
    saida = _criar(modelos, idioma="outro", trecho_idioma="Charlas en español")
    assert saida.idioma == "outro"


def test_preco_desconhecido_sem_trecho_e_aceito(modelos: ModuleType) -> None:
    saida = _criar(modelos, estado_preco="preco_desconhecido", trecho_preco="")
    assert saida.estado_preco == "preco_desconhecido"


def test_preco_desconhecido_com_trecho_e_recusado(modelos: ModuleType) -> None:
    _invalido(modelos, estado_preco="preco_desconhecido", trecho_preco="Encontro gratuito")


@pytest.mark.parametrize(
    "estado", ["gratuito_confirmado", "pago_com_valor", "pago_sem_valor", "preco_variavel"]
)
def test_preco_conhecido_sem_trecho_e_recusado(modelos: ModuleType, estado: str) -> None:
    _invalido(modelos, estado_preco=estado, trecho_preco="")


@pytest.mark.parametrize("localizacao", ["desconhecida", "nao_se_aplica"])
def test_localizacao_sem_evidencia_sem_trecho_e_aceita(
    modelos: ModuleType, localizacao: str
) -> None:
    saida = _criar(modelos, localizacao=localizacao, trecho_localizacao="")
    assert saida.localizacao == localizacao


@pytest.mark.parametrize("localizacao", ["desconhecida", "nao_se_aplica"])
def test_localizacao_sem_evidencia_com_trecho_e_recusada(
    modelos: ModuleType, localizacao: str
) -> None:
    _invalido(modelos, localizacao=localizacao, trecho_localizacao="São Paulo")


@pytest.mark.parametrize("localizacao", ["sp", "fora", "online"])
def test_localizacao_conhecida_sem_trecho_e_recusada(modelos: ModuleType, localizacao: str) -> None:
    _invalido(modelos, localizacao=localizacao, trecho_localizacao="")


# --- Schema JSON enviado ao LLM ------------------------------------------------------------

_PROIBIDAS = {"$ref", "$defs", "definitions", "oneOf", "anyOf", "allOf", "not"}


def _nos(no: Any) -> Iterator[Any]:
    yield no
    if isinstance(no, dict):
        for valor in no.values():
            yield from _nos(valor)
    elif isinstance(no, list):
        for valor in no:
            yield from _nos(valor)


def _chaves(schema: dict[str, Any]) -> set[str]:
    return {chave for no in _nos(schema) if isinstance(no, dict) for chave in no}


def test_schema_e_serializavel(modelos: ModuleType) -> None:
    assert isinstance(modelos.SCHEMA_SAIDA, dict)
    json.dumps(modelos.SCHEMA_SAIDA)


def test_schema_sem_palavras_proibidas(modelos: ModuleType) -> None:
    chaves = _chaves(modelos.SCHEMA_SAIDA)
    assert not chaves & _PROIBIDAS
    assert not {c for c in chaves if c.startswith(("min", "max", "exclusiveM"))}


def test_schema_objetos_com_additional_properties_false(modelos: ModuleType) -> None:
    objetos = [
        no
        for no in _nos(modelos.SCHEMA_SAIDA)
        if isinstance(no, dict) and (no.get("type") == "object" or "properties" in no)
    ]
    assert objetos
    assert all(no.get("additionalProperties") is False for no in objetos)


def test_schema_tem_todos_os_campos_obrigatorios(modelos: ModuleType) -> None:
    schema = modelos.SCHEMA_SAIDA
    campos = set(modelos.SaidaClassificador.model_fields)
    assert schema["type"] == "object"
    assert set(schema["properties"]) == campos
    assert set(schema["required"]) == campos


def test_schema_lista_os_valores_dos_enums(modelos: ModuleType) -> None:
    propriedades = modelos.SCHEMA_SAIDA["properties"]
    assert set(propriedades["tipo_evento"]["enum"]) == {
        "meetup",
        "palestra",
        "hackathon",
        "conferencia",
        "workshop",
        "outro",
    }
    assert set(propriedades["estado_preco"]["enum"]) == {
        "gratuito_confirmado",
        "pago_com_valor",
        "pago_sem_valor",
        "preco_variavel",
        "preco_desconhecido",
    }
    assert set(propriedades["idioma"]["enum"]) == {
        "pt",
        "en",
        "bilingue",
        "outro",
        "desconhecido",
    }
    assert set(propriedades["localizacao"]["enum"]) == {
        "sp",
        "fora",
        "online",
        "desconhecida",
        "nao_se_aplica",
    }


def test_hash_do_schema_bate_com_a_versao(modelos: ModuleType) -> None:
    canonico = json.dumps(
        modelos.SCHEMA_SAIDA, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert hashlib.sha256(canonico.encode("utf-8")).hexdigest() == modelos.SHA256_SCHEMA_V1
    assert modelos.VERSAO_SCHEMA == "1"
