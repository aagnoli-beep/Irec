"""Row Level Security verificata con un ruolo Postgres reale non privilegiato.

I superuser bypassano la RLS, quindi il resto della suite (che usa
l'utente admin del container) non può provarla: qui si crea un ruolo
`irec_app` senza privilegi e si verifica che il database, da solo,
impedisca di vedere o scrivere fuori dal tenant — anche con query
scritte deliberatamente senza filtro.
"""

import os

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool

from irec.adapters.db.models import Base, Mandante
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.rls import enable_rls
from irec.adapters.db.session import create_session_factory, session_scope
from tests.factories import make_mandante

APP_ROLE = "irec_app_test"
APP_PASSWORD = "irec_app_test"


@pytest.fixture
def rls_engines():
    """Coppia (admin, app): schema creato dall'admin, test eseguiti dall'app."""
    admin_url = os.environ.get("IREC_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("IREC_TEST_DATABASE_URL non configurata")
    admin = create_engine(admin_url, poolclass=NullPool)
    try:
        with admin.connect():
            pass
    except OperationalError:
        admin.dispose()
        pytest.skip("Postgres di test non raggiungibile")

    Base.metadata.drop_all(admin)
    Base.metadata.create_all(admin)
    enable_rls(admin)
    with admin.begin() as connection:
        connection.execute(
            text(
                f"""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}'
                    ) THEN
                        CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}';
                    END IF;
                END $$
                """
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA public TO {APP_ROLE}"
            )
        )

    app_url = make_url(admin_url).set(username=APP_ROLE, password=APP_PASSWORD)
    app = create_engine(app_url, poolclass=NullPool)

    yield admin, app

    app.dispose()
    Base.metadata.drop_all(admin)
    admin.dispose()


def test_rls_blocca_letture_e_scritture_fuori_tenant(rls_engines):
    admin, app = rls_engines
    factory = create_session_factory(app)

    # Scrittura attraverso il repository: la policy WITH CHECK passa
    # perché il listener imposta irec.tenant_id sulla transazione.
    with session_scope(factory) as session:
        TenantRepository(session, "tenant-a").add(make_mandante())

    # Lo stesso dato, letto dal repository del tenant giusto, c'è.
    with session_scope(factory) as session:
        assert len(TenantRepository(session, "tenant-a").list(Mandante)) == 1

    # Un altro tenant non lo vede nemmeno attraverso il repository.
    with session_scope(factory) as session:
        assert TenantRepository(session, "tenant-b").list(Mandante) == []

    # Una sessione SENZA repository — cioè una query scritta domani fuori
    # dalle regole, deliberatamente senza filtro — non vede nulla:
    # variabile non impostata → policy fail-closed.
    with session_scope(factory) as session:
        assert list(session.scalars(select(Mandante))) == []

    # E non può nemmeno scrivere fuori tenant.
    with pytest.raises(DBAPIError):
        with session_scope(factory) as session:
            session.add(make_mandante(tenant_id="tenant-z"))
            session.flush()

    # L'admin (superuser, bypassa la RLS) conferma che la riga esiste
    # davvero: il "non vedo nulla" dell'app è la policy, non un DB vuoto.
    admin_factory = create_session_factory(admin)
    with session_scope(admin_factory) as session:
        assert len(list(session.scalars(select(Mandante)))) == 1
