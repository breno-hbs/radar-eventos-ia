"""Esquema inicial: eventos, execucoes, classificacoes, enriquecimentos e notificacoes (D12).

Escrita à mão, igual a `radar.banco.modelos` (um teste compara os dois).

Revision ID: 0001_esquema_inicial
Revises:
Create Date: 2026-11-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_esquema_inicial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True)


def _evento_id(tabela: str) -> sa.Column:
    return sa.Column(
        "evento_id",
        sa.BigInteger(),
        sa.ForeignKey("eventos.id", ondelete="CASCADE", name=f"fk_{tabela}_evento_id_eventos"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "eventos",
        _id(),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("id_fonte", sa.Text(), nullable=False),
        sa.Column("calendarios", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("inicio", TIMESTAMPTZ, nullable=False),
        sa.Column("fim", TIMESTAMPTZ, nullable=True),
        sa.Column("dia_inteiro", sa.Boolean(), nullable=False),
        sa.Column("local_texto", sa.Text(), nullable=True),
        sa.Column("local_url", sa.Text(), nullable=True),
        sa.Column("link_inscricao", sa.Text(), nullable=True),
        sa.Column("organizador", sa.Text(), nullable=True),
        sa.Column("endereco_oculto", sa.Boolean(), nullable=False),
        sa.Column("latitude", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column("longitude", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column("descricoes", postgresql.JSONB(), nullable=False),
        sa.Column("status_fonte", sa.Text(), nullable=False),
        sa.Column("localizacao", sa.Text(), nullable=False),
        sa.Column("evidencia_localizacao", sa.Text(), nullable=False),
        sa.Column("hash_conteudo", postgresql.CHAR(64), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False),
        sa.Column("ausencias_consecutivas", sa.Integer(), nullable=False),
        sa.Column(
            "rodadas_escalares_mantidos", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("primeira_vez_visto", TIMESTAMPTZ, nullable=False),
        sa.Column("ultima_vez_visto", TIMESTAMPTZ, nullable=False),
        sa.Column("atualizado_em", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_eventos"),
        sa.UniqueConstraint("fonte", "id_fonte", name="uq_eventos_fonte_id_fonte"),
        sa.CheckConstraint("fonte IN ('luma')", name="ck_eventos_fonte"),
        sa.CheckConstraint(
            "estado IN ('ativo', 'alterado', 'cancelado', 'encerrado', 'nao_encontrado')",
            name="ck_eventos_estado",
        ),
        sa.CheckConstraint(
            "status_fonte IN ('confirmado', 'cancelado', 'desconhecido')",
            name="ck_eventos_status_fonte",
        ),
        sa.CheckConstraint(
            "localizacao IN ('sp', 'fora', 'online', 'indefinida')", name="ck_eventos_localizacao"
        ),
        sa.CheckConstraint(
            "evidencia_localizacao IN ('geo', 'texto', 'online_url', 'online_texto', 'nenhuma')",
            name="ck_eventos_evidencia_localizacao",
        ),
        sa.CheckConstraint("ausencias_consecutivas >= 0", name="ck_eventos_ausencias_consecutivas"),
        sa.CheckConstraint(
            "rodadas_escalares_mantidos >= 0", name="ck_eventos_rodadas_escalares_mantidos"
        ),
    )
    op.create_index("ix_eventos_estado", "eventos", ["estado"])
    op.create_index("ix_eventos_inicio", "eventos", ["inicio"])

    op.create_table(
        "execucoes",
        _id(),
        sa.Column("rodada", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("calendario", sa.Text(), nullable=False),
        sa.Column("executada_em", TIMESTAMPTZ, nullable=False),
        sa.Column("situacao", sa.Text(), nullable=False),
        sa.Column("quantidade_eventos", sa.Integer(), nullable=False),
        sa.Column("quantidade_invalidos", sa.Integer(), nullable=False),
        sa.Column("motivo_falha", sa.Text(), nullable=True),
        sa.Column("status_http", sa.Integer(), nullable=True),
        sa.Column("detalhe", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_execucoes"),
        sa.UniqueConstraint(
            "rodada", "fonte", "calendario", name="uq_execucoes_rodada_fonte_calendario"
        ),
        sa.CheckConstraint("fonte IN ('luma')", name="ck_execucoes_fonte"),
        sa.CheckConstraint("situacao IN ('ok', 'vazio', 'falha')", name="ck_execucoes_situacao"),
        sa.CheckConstraint(
            "motivo_falha IS NULL OR motivo_falha IN ('bloqueado_robots', 'status_http', "
            "'redirecionamento', 'rede', 'formato_invalido')",
            name="ck_execucoes_motivo_falha",
        ),
        sa.CheckConstraint(
            "(situacao = 'falha') = (motivo_falha IS NOT NULL)",
            name="ck_execucoes_falha_tem_motivo",
        ),
        sa.CheckConstraint(
            "quantidade_eventos >= 0 AND quantidade_invalidos >= 0",
            name="ck_execucoes_quantidades",
        ),
    )

    op.create_table(
        "classificacoes",
        _id(),
        _evento_id("classificacoes"),
        sa.Column("rodada", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("criada_em", TIMESTAMPTZ, nullable=False),
        sa.Column("hash_conteudo", postgresql.CHAR(64), nullable=False),
        sa.Column("versao_prompt", sa.Text(), nullable=False),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("versao_schema", sa.Text(), nullable=False),
        sa.Column("situacao", sa.Text(), nullable=False),
        sa.Column("resultado", postgresql.JSONB(), nullable=True),
        sa.Column("motivo_erro", sa.Text(), nullable=True),
        sa.Column("tokens_entrada", sa.Integer(), nullable=True),
        sa.Column("tokens_saida", sa.Integer(), nullable=True),
        sa.Column("latencia_ms", sa.Integer(), nullable=True),
        sa.Column("custo_usd", sa.Numeric(12, 6), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_classificacoes"),
        sa.CheckConstraint("situacao IN ('ok', 'erro')", name="ck_classificacoes_situacao"),
        sa.CheckConstraint(
            "(situacao = 'ok' AND resultado IS NOT NULL) "
            "OR (situacao = 'erro' AND motivo_erro IS NOT NULL)",
            name="ck_classificacoes_coerencia",
        ),
    )
    op.create_index(
        "uq_classificacoes_ok_por_chave",
        "classificacoes",
        ["evento_id", "hash_conteudo", "versao_prompt", "modelo", "versao_schema"],
        unique=True,
        postgresql_where=sa.text("situacao = 'ok'"),
    )

    op.create_table(
        "enriquecimentos",
        _id(),
        _evento_id("enriquecimentos"),
        sa.Column("hash_conteudo", postgresql.CHAR(64), nullable=False),
        sa.Column("rodada", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("criado_em", TIMESTAMPTZ, nullable=False),
        sa.Column("estado_preco", sa.Text(), nullable=False),
        sa.Column("valor_centavos", sa.BigInteger(), nullable=True),
        sa.Column("moeda", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("status_http", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_enriquecimentos"),
        sa.UniqueConstraint(
            "evento_id", "hash_conteudo", name="uq_enriquecimentos_evento_id_hash_conteudo"
        ),
        sa.CheckConstraint(
            "estado_preco IN ('gratuito_confirmado', 'pago_com_valor', 'pago_sem_valor', "
            "'preco_variavel', 'preco_desconhecido')",
            name="ck_enriquecimentos_estado_preco",
        ),
        sa.CheckConstraint(
            "estado_preco <> 'pago_com_valor' "
            "OR (valor_centavos IS NOT NULL AND moeda IS NOT NULL)",
            name="ck_enriquecimentos_pago_com_valor",
        ),
        sa.CheckConstraint(
            "valor_centavos IS NULL OR valor_centavos >= 0",
            name="ck_enriquecimentos_valor_centavos",
        ),
    )

    op.create_table(
        "notificacoes",
        _id(),
        _evento_id("notificacoes"),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False),
        sa.Column("criada_em", TIMESTAMPTZ, nullable=False),
        sa.Column("enviada_em", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_notificacoes"),
        sa.UniqueConstraint("evento_id", "tipo", name="uq_notificacoes_evento_id_tipo"),
        sa.CheckConstraint("estado IN ('enviando', 'enviado')", name="ck_notificacoes_estado"),
        sa.CheckConstraint(
            "(estado = 'enviado') = (enviada_em IS NOT NULL)",
            name="ck_notificacoes_enviado_tem_horario",
        ),
    )


def downgrade() -> None:
    op.drop_table("notificacoes")
    op.drop_table("enriquecimentos")
    op.drop_index("uq_classificacoes_ok_por_chave", table_name="classificacoes")
    op.drop_table("classificacoes")
    op.drop_table("execucoes")
    op.drop_index("ix_eventos_inicio", table_name="eventos")
    op.drop_index("ix_eventos_estado", table_name="eventos")
    op.drop_table("eventos")
