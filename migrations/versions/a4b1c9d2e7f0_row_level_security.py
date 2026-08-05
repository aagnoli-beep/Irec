"""Row Level Security su tutte le tabelle (quarta rete dell'isolamento tenant).

Revision ID: a4b1c9d2e7f0
Revises: 8e1021f67739
Create Date: 2026-08-05
"""
from collections.abc import Sequence

from alembic import op

from irec.adapters.db.rls import rls_drop_statements_for, rls_statements_for

revision: str = 'a4b1c9d2e7f0'
down_revision: str | None = '8e1021f67739'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Lista CONGELATA delle tabelle esistenti a questa revisione: una
# migrazione è uno snapshot storico e non deve dipendere dai modelli
# correnti. Ogni migrazione futura che crea una tabella deve applicare
# la RLS a quella tabella (tests/test_rls.py verifica che nessuna
# tabella dello schema resti senza policy).
TABELLE = [
    "mandante",
    "cliente_finale",
    "posizione",
    "fattura",
    "flusso",
    "flusso_step",
    "comunicazione",
    "pagamento",
    "audit_log",
]


def upgrade() -> None:
    # La RLS esiste solo su Postgres; su SQLite (test) è un no-op:
    # lì l'isolamento è garantito dalle altre tre reti.
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in rls_statements_for(TABELLE):
        op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in rls_drop_statements_for(TABELLE):
        op.execute(statement)
