"""Stato 'saltata' per le comunicazioni (canale non utilizzabile, PRD 4.6.2).

Revision ID: b7c3d9e1f402
Revises: ed1582b4f518
Create Date: 2026-08-05

Il CHECK degli enum non nativi va ricreato a mano: l'autogenerate di
Alembic non lo rileva (falso negativo simmetrico ai falsi positivi già
documentati in ed1582b4f518). batch_alter_table per compatibilità SQLite.
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'b7c3d9e1f402'
down_revision: str | None = 'ed1582b4f518'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATI_NUOVI = "'programmata', 'inviata', 'annullata', 'fallita', 'saltata'"
STATI_VECCHI = "'programmata', 'inviata', 'annullata', 'fallita'"


def upgrade() -> None:
    with op.batch_alter_table("comunicazione") as batch:
        batch.drop_constraint("statocomunicazione", type_="check")
        batch.create_check_constraint("statocomunicazione", f"stato IN ({STATI_NUOVI})")


def downgrade() -> None:
    with op.batch_alter_table("comunicazione") as batch:
        batch.drop_constraint("statocomunicazione", type_="check")
        batch.create_check_constraint("statocomunicazione", f"stato IN ({STATI_VECCHI})")
