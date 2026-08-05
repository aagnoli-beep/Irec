"""Row Level Security verificata con un ruolo Postgres reale non privilegiato.

I superuser bypassano la RLS, quindi il resto della suite (che usa
l'utente admin del container) non può provarla: qui si crea un ruolo
`irec_app` senza privilegi e si verifica che il database, da solo,
impedisca di vedere o scrivere fuori dal tenant — anche con query
scritte deliberatamente senza filtro.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool

from irec.adapters.db.models import Base, Mandante
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.rls import RLS_POLICY_NAME, enable_rls
from irec.adapters.db.session import create_session_factory, session_scope
from tests.factories import make_mandante

REPO_ROOT = Path(__file__).resolve().parents[1]

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


def test_rls_blocca_update_e_delete_senza_filtro(rls_engines):
    """Il caso "cancellazione GDPR scritta male domani": UPDATE e DELETE
    deliberatamente senza filtro, da una sessione senza tenant, non
    toccano nulla."""
    admin, app = rls_engines
    factory = create_session_factory(app)

    with session_scope(factory) as session:
        TenantRepository(session, "tenant-a").add(make_mandante())

    with session_scope(factory) as session:
        aggiornate = session.execute(
            update(Mandante).values(ragione_sociale="hack")
        ).rowcount
        cancellate = session.execute(delete(Mandante)).rowcount
    assert aggiornate == 0
    assert cancellate == 0

    admin_factory = create_session_factory(admin)
    with session_scope(admin_factory) as session:
        righe = list(session.scalars(select(Mandante)))
        assert len(righe) == 1
        assert righe[0].ragione_sociale != "hack"


def test_policy_presente_su_ogni_tabella(rls_engines):
    """Nessuna tabella dello schema può restare fuori dalla quarta rete."""
    admin, _ = rls_engines
    with admin.connect() as connection:
        con_policy = set(
            connection.execute(
                text(
                    "SELECT tablename FROM pg_policies WHERE policyname = :nome"
                ),
                {"nome": RLS_POLICY_NAME},
            ).scalars()
        )
        forzate = set(
            connection.execute(
                text(
                    "SELECT relname FROM pg_class "
                    "WHERE relrowsecurity AND relforcerowsecurity"
                )
            ).scalars()
        )
    assert con_policy == set(Base.metadata.tables)
    assert set(Base.metadata.tables) <= forzate


def test_migrazione_rls_su_postgres():
    """La catena alembic REALE (non enable_rls) attiva e rimuove le policy."""
    admin_url = os.environ.get("IREC_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("IREC_TEST_DATABASE_URL non configurata")
    engine = create_engine(admin_url, poolclass=NullPool)
    try:
        with engine.connect():
            pass
    except OperationalError:
        engine.dispose()
        pytest.skip("Postgres di test non raggiungibile")

    def alembic(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT,
            env={"PATH": "/usr/bin:/bin", "IREC_DATABASE_URL": admin_url},
            capture_output=True,
            text=True,
        )

    # Parte da un database pulito e percorre tutta la catena.
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    try:
        result = alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr

        with engine.connect() as connection:
            con_policy = set(
                connection.execute(
                    text("SELECT tablename FROM pg_policies WHERE policyname = :nome"),
                    {"nome": RLS_POLICY_NAME},
                ).scalars()
            )
        assert con_policy == set(Base.metadata.tables)

        # Fino a PRIMA della revisione RLS: tutte le policy devono sparire.
        result = alembic("downgrade", "8e1021f67739")
        assert result.returncode == 0, result.stderr
        with engine.connect() as connection:
            residue = connection.execute(
                text("SELECT count(*) FROM pg_policies WHERE policyname = :nome"),
                {"nome": RLS_POLICY_NAME},
            ).scalar()
        assert residue == 0
    finally:
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()
