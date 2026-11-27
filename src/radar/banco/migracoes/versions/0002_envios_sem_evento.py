"""Envios sem evento: alertas e marco do resumo semanal (D22).

Escrita à mão, igual a `radar.banco.modelos.EnvioSemEvento` (um teste compara os dois).

Revision ID: 0002_envios_sem_evento
Revises: 0001_esquema_inicial
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_envios_sem_evento"
down_revision: str | None = "0001_esquema_inicial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "envios_sem_evento",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chave", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False),
        sa.Column("criado_em", TIMESTAMPTZ, nullable=False),
        sa.Column("enviado_em", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_envios_sem_evento"),
        sa.UniqueConstraint("chave", name="uq_envios_sem_evento_chave"),
        sa.CheckConstraint(
            "tipo IN ('falha_extracao', 'fonte_vazia', 'inatividade', 'robots_bloqueado', "
            "'enriquecimento_sistematico', 'classificador_interrompido', 'resumo_semanal')",
            name="ck_envios_sem_evento_tipo",
        ),
        sa.CheckConstraint("estado IN ('enviando', 'enviado')", name="ck_envios_sem_evento_estado"),
        sa.CheckConstraint(
            "(estado = 'enviado') = (enviado_em IS NOT NULL)",
            name="ck_envios_sem_evento_enviado_tem_horario",
        ),
    )


def downgrade() -> None:
    op.drop_table("envios_sem_evento")
