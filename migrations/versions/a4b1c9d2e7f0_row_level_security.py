"""Row Level Security su tutte le tabelle (quarta rete dell'isolamento tenant).

Revision ID: a4b1c9d2e7f0
Revises: 8e1021f67739
Create Date: 2026-08-05
"""
from collections.abc import Sequence

from alembic import op

from irec.adapters.db.models import Base
from irec.adapters.db.rls import rls_statements

revision: str = 'a4b1c9d2e7f0'
down_revision: str | None = '8e1021f67739'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # La RLS esiste solo su Postgres; su SQLite (test) è un no-op:
    # lì l'isolamento è garantito dalle altre tre reti.
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in rls_statements():
        op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for nome_tabella in Base.metadata.tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {nome_tabella}")
        op.execute(f"ALTER TABLE {nome_tabella} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {nome_tabella} DISABLE ROW LEVEL SECURITY")
