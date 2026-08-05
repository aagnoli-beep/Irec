"""Vincoli dello schema, migrazioni e rotta di cancellazione GDPR."""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from irec.adapters.db.models import Base, Fattura, Mandante
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import check_connection, session_scope
from tests.factories import make_cliente, make_fattura, make_mandante, make_posizione

REPO_ROOT = Path(__file__).resolve().parents[1]


def _crea_fattura(session_factory, tenant_id: str = "tenant-abc", **overrides):
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, tenant_id)
        mandante = repo.add(make_mandante())
        repo.flush()
        cliente = repo.add(make_cliente(mandante.id))
        repo.flush()
        posizione = repo.add(make_posizione(cliente.id))
        repo.flush()
        fattura = repo.add(make_fattura(posizione.id, cliente.id, **overrides))
        repo.flush()
        return fattura.id, cliente.id, posizione.id


class TestVincoliSchema:
    def test_numero_fattura_unico_per_cliente(self, session_factory):
        """Reimport dallo stesso cassetto fiscale non deve duplicare la fattura."""
        _, cliente_id, posizione_id = _crea_fattura(session_factory)
        with pytest.raises(IntegrityError):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, "tenant-abc")
                repo.add(make_fattura(posizione_id, cliente_id, numero="32-FA"))
                repo.flush()

    def test_stesso_numero_ammesso_per_clienti_diversi(self, session_factory):
        _, _, _ = _crea_fattura(session_factory)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, "tenant-abc")
            mandante = repo.list(Mandante)[0]
            altro = repo.add(make_cliente(mandante.id, piva_cf="11111111111"))
            repo.flush()
            posizione = repo.add(make_posizione(altro.id))
            repo.flush()
            repo.add(make_fattura(posizione.id, altro.id, numero="32-FA"))
            repo.flush()

    def test_importi_conservano_i_centesimi(self, session_factory):
        """Numeric, non float: nessuna deriva sui decimali."""
        fattura_id, _, _ = _crea_fattura(
            session_factory, importo=Decimal("1234.56"), importo_residuo=Decimal("1234.56")
        )
        with session_scope(session_factory) as session:
            fattura = TenantRepository(session, "tenant-abc").get(Fattura, fattura_id)
            assert fattura.importo == Decimal("1234.56")
            assert isinstance(fattura.importo, Decimal)

    def test_cancellare_il_cliente_cancella_le_sue_fatture(self, session_factory):
        """Cascade: nessun orfano dopo una cancellazione."""
        from irec.adapters.db.models import ClienteFinale

        _, cliente_id, _ = _crea_fattura(session_factory)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, "tenant-abc")
            cliente = repo.get(ClienteFinale, cliente_id)
            session.delete(cliente)
        with session_scope(session_factory) as session:
            assert TenantRepository(session, "tenant-abc").list(Fattura) == []


class TestMigrazioni:
    def test_migrazione_allineata_ai_modelli(self, tmp_path):
        """`alembic upgrade head` deve produrre le stesse tabelle dei modelli.

        Protegge dal drift silenzioso fra ORM e migrazioni: un modello
        modificato senza migrazione fa fallire questo test.
        """
        db_path = tmp_path / "migrated.db"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env={
                "PATH": "/usr/bin:/bin",
                "IREC_DATABASE_URL": f"sqlite:///{db_path}",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

        migrated = inspect(create_engine(f"sqlite:///{db_path}")).get_table_names()
        attese = set(Base.metadata.tables) | {"alembic_version"}
        assert set(migrated) == attese


class TestReadinessDatabase:
    def test_check_connection_su_db_valido(self, db_engine):
        assert check_connection(db_engine) is True

    def test_check_connection_su_db_irraggiungibile(self):
        engine = create_engine("postgresql+psycopg://nessuno@127.0.0.1:1/none")
        assert check_connection(engine) is False

    def test_ready_503_se_database_irraggiungibile(self, app, client):
        app.state.engine = create_engine("postgresql+psycopg://nessuno@127.0.0.1:1/none")
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "database_unreachable"

    def test_ready_non_espone_la_connection_string(self, app, client):
        app.state.engine = create_engine(
            "postgresql+psycopg://utente:segreto@127.0.0.1:1/none"
        )
        response = client.get("/ready")
        assert "segreto" not in response.text


class TestRottaCancellazioneTenant:
    def test_cancella_solo_il_tenant_del_token(self, client, session_factory, make_token):
        _crea_fattura(session_factory, tenant_id="tenant-abc")
        _crea_fattura(session_factory, tenant_id="tenant-altro")

        response = client.request(
            "DELETE",
            "/v1/tenant",
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["tenant_cancellato"] is True
        assert body["righe_cancellate"]["fattura"] == 1

        with session_scope(session_factory) as session:
            assert TenantRepository(session, "tenant-abc").list(Fattura) == []
            assert len(TenantRepository(session, "tenant-altro").list(Fattura)) == 1

    def test_senza_token_non_cancella_nulla(self, client, session_factory):
        _crea_fattura(session_factory, tenant_id="tenant-abc")
        response = client.request("DELETE", "/v1/tenant")
        assert response.status_code == 401
        with session_scope(session_factory) as session:
            assert len(TenantRepository(session, "tenant-abc").list(Fattura)) == 1
