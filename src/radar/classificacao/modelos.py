"""Modelo de saída do classificador e schema JSON enviado ao LLM (D13).

O schema é derivado do modelo Pydantic, mas fica plano e sem palavras min*/max*: os limites
de tamanho vivem nos validadores (o LLM vê só tipos e enums; a aplicação confere o resto).
Mudar o schema exige mudar `VERSAO_SCHEMA` e `SHA256_SCHEMA_V1` (um teste confere o hash).

Regra de evidência (CLAUDE.md): sem trecho literal, o estado é "desconhecido".
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODELO = "claude-haiku-5-5"
VERSAO_SCHEMA = "1"
SHA256_SCHEMA_V1 = "e47ffaa89088ce38ee130846bb4f70087c0803b6be987bfdf0ec05da5f7e6e39"

MAX_RESUMO = 200
MAX_CATEGORIA = 40
MAX_TOPICOS = 5
MAX_TOPICO = 40
MAX_TRECHO = 300

TipoEvento = Literal["meetup", "palestra", "hackathon", "conferencia", "workshop", "outro"]
Modalidade = Literal["presencial", "online", "hibrido", "desconhecida"]
# "outro" = idioma estrangeiro confirmado que não é pt nem en (ex.: espanhol).
Idioma = Literal["pt", "en", "bilingue", "outro", "desconhecido"]
EstadoPreco = Literal[
    "gratuito_confirmado",
    "pago_com_valor",
    "pago_sem_valor",
    "preco_variavel",
    "preco_desconhecido",
]
LocalizacaoLLM = Literal["sp", "fora", "online", "desconhecida", "nao_se_aplica"]


def _uma_linha(valor: str, campo: str, maximo: int) -> str:
    """Apara; recusa vazio, quebra de linha e texto acima de `maximo` caracteres."""
    aparado = valor.strip()
    if not aparado:
        raise ValueError(f"{campo} vazio")
    if "\n" in aparado or "\r" in aparado:
        raise ValueError(f"{campo} deve ter uma linha só")
    if len(aparado) > maximo:
        raise ValueError(f"{campo} acima de {maximo} caracteres")
    return aparado


class SaidaClassificador(BaseModel):
    """Saída validada do LLM para um evento."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    e_ia: bool = Field(description="O evento é sobre inteligência artificial.")
    categoria: str = Field(description="Categoria curta do tema (até 40 caracteres).")
    topicos: list[str] = Field(description="De 0 a 5 tópicos curtos (até 40 caracteres cada).")
    tipo_evento: TipoEvento
    modalidade: Modalidade
    trecho_modalidade: str = Field(description="Trecho literal; vazio se desconhecida.")
    idioma: Idioma
    tem_traducao: bool
    trecho_idioma: str = Field(
        description="Trecho literal; vazio só se idioma desconhecido e sem tradução."
    )
    estado_preco: EstadoPreco
    trecho_preco: str = Field(description="Trecho literal; vazio se preco_desconhecido.")
    localizacao: LocalizacaoLLM
    trecho_localizacao: str = Field(
        description="Trecho literal; vazio se desconhecida ou nao_se_aplica."
    )
    resumo: str = Field(description="Uma linha, até 200 caracteres.")
    trecho_evidencia: str = Field(description="Trecho literal que sustenta e_ia (obrigatório).")

    @field_validator("resumo")
    @classmethod
    def _validar_resumo(cls, valor: str) -> str:
        return _uma_linha(valor, "resumo", MAX_RESUMO)

    @field_validator("categoria")
    @classmethod
    def _validar_categoria(cls, valor: str) -> str:
        return _uma_linha(valor, "categoria", MAX_CATEGORIA)

    @field_validator("topicos")
    @classmethod
    def _validar_topicos(cls, valor: list[str]) -> list[str]:
        if len(valor) > MAX_TOPICOS:
            raise ValueError(f"mais de {MAX_TOPICOS} tópicos")
        return [_uma_linha(t, "tópico", MAX_TOPICO) for t in valor]

    @field_validator("trecho_modalidade", "trecho_idioma", "trecho_preco", "trecho_localizacao")
    @classmethod
    def _validar_trecho(cls, valor: str) -> str:
        aparado = valor.strip()
        if len(aparado) > MAX_TRECHO:
            raise ValueError(f"trecho acima de {MAX_TRECHO} caracteres")
        return aparado

    @field_validator("trecho_evidencia")
    @classmethod
    def _validar_trecho_evidencia(cls, valor: str) -> str:
        aparado = valor.strip()
        if not aparado:
            raise ValueError("trecho_evidencia vazio")
        if len(aparado) > MAX_TRECHO:
            raise ValueError(f"trecho acima de {MAX_TRECHO} caracteres")
        return aparado

    @model_validator(mode="after")
    def _evidencia_coerente(self) -> Self:
        """Estado conhecido <=> trecho não vazio (nunca inferir sem evidência)."""
        regras = [
            ("modalidade", self.modalidade != "desconhecida", self.trecho_modalidade),
            (
                "idioma",
                self.idioma != "desconhecido" or self.tem_traducao,
                self.trecho_idioma,
            ),
            ("preço", self.estado_preco != "preco_desconhecido", self.trecho_preco),
            (
                "localização",
                self.localizacao not in ("desconhecida", "nao_se_aplica"),
                self.trecho_localizacao,
            ),
        ]
        for nome, conhecido, trecho in regras:
            if conhecido and not trecho:
                raise ValueError(f"{nome} conhecido(a) exige trecho")
            if not conhecido and trecho:
                raise ValueError(f"{nome} sem evidência não pode ter trecho")
        return self


def _sem_titulos(no: Any) -> Any:
    if isinstance(no, dict):
        return {k: _sem_titulos(v) for k, v in no.items() if k != "title"}
    if isinstance(no, list):
        return [_sem_titulos(v) for v in no]
    return no


def _gerar_schema() -> dict[str, Any]:
    schema: dict[str, Any] = _sem_titulos(SaidaClassificador.model_json_schema())
    # A docstring da classe não deve mudar o schema (nem o hash) sem querer.
    schema.pop("description", None)
    schema["additionalProperties"] = False
    return schema


SCHEMA_SAIDA: dict[str, Any] = _gerar_schema()
