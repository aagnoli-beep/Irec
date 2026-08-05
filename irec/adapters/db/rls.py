"""Row Level Security di Postgres: la quarta rete dell'isolamento tenant.

Le prime tre (repository, guard before_flush, FK composite) vivono nel
codice applicativo. La RLS vive nel database: copre anche una query
scritta domani fuori dal repository, o una connessione applicativa non
passata da `TenantRepository`.

La policy confronta `tenant_id` con la variabile di sessione
`irec.tenant_id`, impostata a inizio transazione dal listener in
`repository.py`. Se la variabile non è impostata, `current_setting(...,
true)` restituisce NULL e la policy non lascia passare nulla: fail-closed.

Nota operativa: la RLS non si applica ai superuser. In produzione il
servizio deve connettersi con un ruolo dedicato non privilegiato
(`FORCE ROW LEVEL SECURITY` la applica anche al proprietario delle
tabelle). Il test `tests/test_rls.py` lo verifica con un ruolo reale.
"""

from sqlalchemy import Engine, text

from irec.adapters.db.models import Base

RLS_TENANT_SETTING = "irec.tenant_id"

# Nome della policy: usato anche dal downgrade delle migrazioni.
RLS_POLICY_NAME = "tenant_isolation"


def rls_statements_for(table_names: list[str] | None = None) -> list[str]:
    """Gli statement che attivano la RLS sulle tabelle indicate.

    Con `None` copre tutte le tabelle dei modelli correnti (uso: test e
    `enable_rls`). Le MIGRAZIONI devono invece passare la loro lista
    congelata: una migrazione è uno snapshot storico e non può dipendere
    dai modelli "vivi" — su un DB vergine fallirebbe sulle tabelle nate
    dopo di lei, e su un DB migrato lascerebbe scoperte le nuove.
    """
    if table_names is None:
        table_names = list(Base.metadata.tables)
    statements: list[str] = []
    for table_name in table_names:
        statements.extend(
            [
                f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS {RLS_POLICY_NAME} ON {table_name}",
                (
                    f"CREATE POLICY {RLS_POLICY_NAME} ON {table_name} "
                    f"USING (tenant_id = current_setting('{RLS_TENANT_SETTING}', true)) "
                    f"WITH CHECK "
                    f"(tenant_id = current_setting('{RLS_TENANT_SETTING}', true))"
                ),
            ]
        )
    return statements


def rls_drop_statements_for(table_names: list[str]) -> list[str]:
    """Statement di disattivazione, per i downgrade delle migrazioni."""
    statements: list[str] = []
    for table_name in table_names:
        statements.extend(
            [
                f"DROP POLICY IF EXISTS {RLS_POLICY_NAME} ON {table_name}",
                f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY",
                f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY",
            ]
        )
    return statements


def enable_rls(engine: Engine) -> None:
    """Attiva la RLS su un database Postgres. No-op su altri dialetti."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for statement in rls_statements_for(None):
            connection.execute(text(statement))


def connection_bypasses_rls(engine: Engine) -> bool | None:
    """True se l'utente della connessione bypassa la RLS.

    I superuser e i ruoli con BYPASSRLS ignorano le policy: con un ruolo
    così la quarta rete è silenziosamente inerte. None su dialetti non
    Postgres (dove la RLS non esiste).
    """
    if engine.dialect.name != "postgresql":
        return None
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            ).scalar()
        )
