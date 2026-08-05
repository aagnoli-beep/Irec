"""Tabella notifica per le notifiche proattive (M6).

Revision ID: 26814ec71749
Revises: b7c3d9e1f402
Create Date: 2026-08-05

Gli op di drop dei CHECK enum proposti dall'autogenerate sono rimossi:
falsi positivi noti di Alembic sugli Enum non nativi (i CHECK esistono e
restano invariati). RLS applicata alla nuova tabella (regola di processo
review M2).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from irec.adapters.db.rls import rls_drop_statements_for, rls_statements_for

revision: str = '26814ec71749'
down_revision: str | None = 'b7c3d9e1f402'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABELLE_NUOVE = ["notifica"]


def upgrade() -> None:
    op.create_table(
        'notifica',
        sa.Column('tipo', sa.Enum(
            'escalation_imminente', 'consenso_psd2', 'collegamento_ade',
            'escalation_eseguita', 'dato_in_ritardo',
            name='tiponotifica', native_enum=False, create_constraint=True, length=32,
        ), nullable=False),
        sa.Column('riferimento', sa.String(length=64), nullable=False),
        sa.Column('chiave', sa.String(length=128), nullable=False),
        sa.Column('dettaglio', sa.String(length=255), nullable=True),
        sa.Column('letta_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'chiave', name='uq_notifica_tenant_chiave'),
        sa.UniqueConstraint('tenant_id', 'id', name='uq_notifica_tenant_id'),
    )
    op.create_index(op.f('ix_notifica_tenant_id'), 'notifica', ['tenant_id'], unique=False)
    op.create_index(
        'ix_notifica_tenant_letta', 'notifica', ['tenant_id', 'letta_at'], unique=False
    )

    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_statements_for(TABELLE_NUOVE):
            op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_drop_statements_for(TABELLE_NUOVE):
            op.execute(statement)
    op.drop_index('ix_notifica_tenant_letta', table_name='notifica')
    op.drop_index(op.f('ix_notifica_tenant_id'), table_name='notifica')
    op.drop_table('notifica')
