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

_POLICY = "tenant_isolation"


def rls_statements() -> list[str]:
    """Gli statement che attivano la RLS su ogni tabella dello schema.

    Usati sia dalla migrazione sia dai test: un'unica fonte, così una
    tabella nuova non può ricevere la policy in un posto e non nell'altro.
    """
    statements: list[str] = []
    for nome_tabella in Base.metadata.tables:
        statements.extend(
            [
                f"ALTER TABLE {nome_tabella} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {nome_tabella} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS {_POLICY} ON {nome_tabella}",
                (
                    f"CREATE POLICY {_POLICY} ON {nome_tabella} "
                    f"USING (tenant_id = current_setting('{RLS_TENANT_SETTING}', true)) "
                    f"WITH CHECK "
                    f"(tenant_id = current_setting('{RLS_TENANT_SETTING}', true))"
                ),
            ]
        )
    return statements


def enable_rls(engine: Engine) -> None:
    """Attiva la RLS su un database Postgres. No-op su altri dialetti."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for statement in rls_statements():
            connection.execute(text(statement))
