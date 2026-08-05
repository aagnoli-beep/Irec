"""Tabella sync_run per le run asincrone del ciclo di sincronizzazione.

Revision ID: ed1582b4f518
Revises: a4b1c9d2e7f0
Create Date: 2026-08-05

Nota: gli op di drop/create dei CHECK degli enum proposti dall'autogenerate
sono stati rimossi — sono falsi positivi noti di Alembic sugli Enum non
nativi (i CHECK esistono e restano invariati).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from irec.adapters.db.rls import rls_drop_statements_for, rls_statements_for

revision: str = 'ed1582b4f518'
down_revision: str | None = 'a4b1c9d2e7f0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Regola di processo (review M2): ogni migrazione che crea una tabella
# applica la RLS a quella tabella. Lista congelata a questa revisione.
TABELLE_NUOVE = ["sync_run"]


def upgrade() -> None:
    op.create_table(
        'sync_run',
        sa.Column('stato', sa.Enum(
            'queued', 'running', 'completed', 'failed',
            name='statorun', native_enum=False, create_constraint=True, length=32,
        ), nullable=False),
        sa.Column('chiave_idempotenza', sa.String(length=128), nullable=False),
        sa.Column('avviata_da', sa.String(length=64), nullable=True),
        sa.Column('avviata_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('conclusa_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('risultato', sa.JSON(), nullable=True),
        sa.Column('errore', sa.String(length=255), nullable=True),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'chiave_idempotenza', name='uq_sync_run_tenant_chiave'),
        sa.UniqueConstraint('tenant_id', 'id', name='uq_sync_run_tenant_id'),
    )
    op.create_index(op.f('ix_sync_run_tenant_id'), 'sync_run', ['tenant_id'], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_statements_for(TABELLE_NUOVE):
            op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for statement in rls_drop_statements_for(TABELLE_NUOVE):
            op.execute(statement)
    op.drop_index(op.f('ix_sync_run_tenant_id'), table_name='sync_run')
    op.drop_table('sync_run')
