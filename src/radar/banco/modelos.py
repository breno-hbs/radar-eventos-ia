"""Modelo ORM (SQLAlchemy 2.0) das cinco tabelas do radar (D12).

Enums são `text` + CHECK (mais fáceis de migrar que tipos ENUM do Postgres); horários são
`timestamptz`. A migração `0001` repete este esquema à mão e um teste garante que os dois
batem (`compare_metadata`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CHAR, DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CONVENCAO_NOMES = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TIMESTAMPTZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCAO_NOMES)


class Evento(Base):
    __tablename__ = "eventos"
    __table_args__ = (
        UniqueConstraint("fonte", "id_fonte"),
        CheckConstraint("fonte IN ('luma')", name="fonte"),
        CheckConstraint(
            "estado IN ('ativo', 'alterado', 'cancelado', 'encerrado', 'nao_encontrado')",
            name="estado",
        ),
        CheckConstraint(
            "status_fonte IN ('confirmado', 'cancelado', 'desconhecido')", name="status_fonte"
        ),
        CheckConstraint(
            "localizacao IN ('sp', 'fora', 'online', 'indefinida')", name="localizacao"
        ),
        CheckConstraint(
            "evidencia_localizacao IN ('geo', 'texto', 'online_url', 'online_texto', 'nenhuma')",
            name="evidencia_localizacao",
        ),
        CheckConstraint("ausencias_consecutivas >= 0", name="ausencias_consecutivas"),
        CheckConstraint("rodadas_escalares_mantidos >= 0", name="rodadas_escalares_mantidos"),
        Index(None, "estado"),
        Index(None, "inicio"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fonte: Mapped[str] = mapped_column(Text)
    id_fonte: Mapped[str] = mapped_column(Text)
    calendarios: Mapped[list[str]] = mapped_column(ARRAY(Text))
    titulo: Mapped[str] = mapped_column(Text)
    inicio: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    fim: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    dia_inteiro: Mapped[bool] = mapped_column(Boolean)
    local_texto: Mapped[str | None] = mapped_column(Text)
    local_url: Mapped[str | None] = mapped_column(Text)
    link_inscricao: Mapped[str | None] = mapped_column(Text)
    organizador: Mapped[str | None] = mapped_column(Text)
    endereco_oculto: Mapped[bool] = mapped_column(Boolean)
    latitude: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    longitude: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    descricoes: Mapped[dict[str, str | None]] = mapped_column(JSONB)
    status_fonte: Mapped[str] = mapped_column(Text)
    localizacao: Mapped[str] = mapped_column(Text)
    evidencia_localizacao: Mapped[str] = mapped_column(Text)
    hash_conteudo: Mapped[str] = mapped_column(CHAR(64))
    estado: Mapped[str] = mapped_column(Text)
    ausencias_consecutivas: Mapped[int] = mapped_column(Integer)
    # Rodadas seguidas com os escalares gravados mantidos (calendário de menor slug em falha).
    rodadas_escalares_mantidos: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    primeira_vez_visto: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    ultima_vez_visto: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    atualizado_em: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class Execucao(Base):
    """Uma linha por calendário lido em cada rodada."""

    __tablename__ = "execucoes"
    __table_args__ = (
        UniqueConstraint("rodada", "fonte", "calendario"),
        CheckConstraint("fonte IN ('luma')", name="fonte"),
        CheckConstraint("situacao IN ('ok', 'vazio', 'falha')", name="situacao"),
        CheckConstraint(
            "motivo_falha IS NULL OR motivo_falha IN ('bloqueado_robots', 'status_http', "
            "'redirecionamento', 'rede', 'formato_invalido')",
            name="motivo_falha",
        ),
        CheckConstraint(
            "(situacao = 'falha') = (motivo_falha IS NOT NULL)", name="falha_tem_motivo"
        ),
        CheckConstraint(
            "quantidade_eventos >= 0 AND quantidade_invalidos >= 0", name="quantidades"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rodada: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    fonte: Mapped[str] = mapped_column(Text)
    calendario: Mapped[str] = mapped_column(Text)
    executada_em: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    situacao: Mapped[str] = mapped_column(Text)
    quantidade_eventos: Mapped[int] = mapped_column(Integer)
    quantidade_invalidos: Mapped[int] = mapped_column(Integer)
    motivo_falha: Mapped[str | None] = mapped_column(Text)
    status_http: Mapped[int | None] = mapped_column(Integer)
    detalhe: Mapped[str | None] = mapped_column(Text)


class Classificacao(Base):
    """Uma linha por chamada ao LLM; só uma `ok` por chave (hash + prompt + modelo + schema)."""

    __tablename__ = "classificacoes"
    __table_args__ = (
        CheckConstraint("situacao IN ('ok', 'erro')", name="situacao"),
        CheckConstraint(
            "(situacao = 'ok' AND resultado IS NOT NULL) "
            "OR (situacao = 'erro' AND motivo_erro IS NOT NULL)",
            name="coerencia",
        ),
        Index(
            "uq_classificacoes_ok_por_chave",
            "evento_id",
            "hash_conteudo",
            "versao_prompt",
            "modelo",
            "versao_schema",
            unique=True,
            postgresql_where=text("situacao = 'ok'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("eventos.id", ondelete="CASCADE"))
    rodada: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    criada_em: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    hash_conteudo: Mapped[str] = mapped_column(CHAR(64))
    versao_prompt: Mapped[str] = mapped_column(Text)
    modelo: Mapped[str] = mapped_column(Text)
    versao_schema: Mapped[str] = mapped_column(Text)
    situacao: Mapped[str] = mapped_column(Text)
    resultado: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    motivo_erro: Mapped[str | None] = mapped_column(Text)
    tokens_entrada: Mapped[int | None] = mapped_column(Integer)
    tokens_saida: Mapped[int | None] = mapped_column(Integer)
    latencia_ms: Mapped[int | None] = mapped_column(Integer)
    custo_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


class Enriquecimento(Base):
    __tablename__ = "enriquecimentos"
    __table_args__ = (
        UniqueConstraint("evento_id", "hash_conteudo"),
        CheckConstraint(
            "estado_preco IN ('gratuito_confirmado', 'pago_com_valor', 'pago_sem_valor', "
            "'preco_variavel', 'preco_desconhecido')",
            name="estado_preco",
        ),
        CheckConstraint(
            "estado_preco <> 'pago_com_valor' "
            "OR (valor_centavos IS NOT NULL AND moeda IS NOT NULL)",
            name="pago_com_valor",
        ),
        CheckConstraint("valor_centavos IS NULL OR valor_centavos >= 0", name="valor_centavos"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("eventos.id", ondelete="CASCADE"))
    hash_conteudo: Mapped[str] = mapped_column(CHAR(64))
    rodada: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    criado_em: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    estado_preco: Mapped[str] = mapped_column(Text)
    valor_centavos: Mapped[int | None] = mapped_column(BigInteger)
    moeda: Mapped[str | None] = mapped_column(Text)
    motivo: Mapped[str | None] = mapped_column(Text)
    status_http: Mapped[int | None] = mapped_column(Integer)


class Notificacao(Base):
    __tablename__ = "notificacoes"
    __table_args__ = (
        UniqueConstraint("evento_id", "tipo"),
        CheckConstraint("estado IN ('enviando', 'enviado')", name="estado"),
        CheckConstraint(
            "(estado = 'enviado') = (enviada_em IS NOT NULL)", name="enviado_tem_horario"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("eventos.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text)
    criada_em: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    enviada_em: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


TIPOS_ENVIO_SEM_EVENTO = (
    "falha_extracao",
    "fonte_vazia",
    "inatividade",
    "robots_bloqueado",
    "enriquecimento_sistematico",
    "classificador_interrompido",
    "resumo_semanal",
)


class EnvioSemEvento(Base):
    """Envio que não pertence a um evento (alertas e o marco do resumo semanal, D22).

    `chave` única: "<tipo>:<alvo>:<data de SP>" nos alertas e "resumo_semanal:<ano>-W<ss>"
    no marco semanal. Mesmo ciclo de `notificacoes`: 'enviando' → 'enviado'.
    """

    __tablename__ = "envios_sem_evento"
    __table_args__ = (
        UniqueConstraint("chave"),
        CheckConstraint(
            "tipo IN ('falha_extracao', 'fonte_vazia', 'inatividade', 'robots_bloqueado', "
            "'enriquecimento_sistematico', 'classificador_interrompido', 'resumo_semanal')",
            name="tipo",
        ),
        CheckConstraint("estado IN ('enviando', 'enviado')", name="estado"),
        CheckConstraint(
            "(estado = 'enviado') = (enviado_em IS NOT NULL)", name="enviado_tem_horario"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chave: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    enviado_em: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
