"""Vincoli dello schema, migrazioni e rotta di cancellazione GDPR."""

import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, create_engine, inspect
from sqlalchemy.exc import IntegrityError, StatementError

from irec.adapters.db.models import Base, ClienteFinale, Fattura, Mandante
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import check_connection, session_scope
from tests.factories import (
    make_cliente,
    make_fattura,
    make_pagamento,
    make_posizione,
    make_step,
    popola_tenant,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TENANT = "tenant-abc"


def _alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "IREC_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )


class TestVincoliUnicita:
    def test_numero_fattura_unico_per_cliente(self, session_factory):
        """Reimport dallo stesso cassetto fiscale non deve duplicare la fattura."""
        ids = popola_tenant(session_factory, TENANT)
        with pytest.raises(IntegrityError):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, TENANT)
                repo.add(make_fattura(ids["posizione"], ids["cliente"], numero="32-FA"))
                repo.flush()

    def test_stesso_numero_ammesso_per_clienti_diversi(self, session_factory):
        ids = popola_tenant(session_factory, TENANT)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            altro = repo.add(make_cliente(ids["mandante"], piva_cf="11111111111"))
            repo.flush()
            posizione = repo.add(make_posizione(altro.id))
            repo.flush()
            repo.add(make_fattura(posizione.id, altro.id, numero="32-FA"))
            repo.flush()

    def test_un_solo_mandante_per_tenant(self, session_factory):
        popola_tenant(session_factory, TENANT)
        with pytest.raises(IntegrityError):
            popola_tenant(session_factory, TENANT, mandante={"partita_iva": "22222222222"})

    def test_piva_cliente_unica_per_tenant(self, session_factory):
        ids = popola_tenant(session_factory, TENANT)
        with pytest.raises(IntegrityError):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, TENANT)
                repo.add(make_cliente(ids["mandante"], piva_cf="09876543210"))
                repo.flush()

    def test_chiave_idempotenza_pagamento_unica_per_tenant(self, session_factory):
        """Lo stesso incasso non viene contato due volte, da qualunque fonte arrivi."""
        ids = popola_tenant(session_factory, TENANT)
        with pytest.raises(IntegrityError):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, TENANT)
                repo.add(make_pagamento(ids["fattura"], chiave_idempotenza="mov-0001"))
                repo.flush()

    def test_stessa_chiave_pagamento_ammessa_in_tenant_diversi(self, session_factory):
        popola_tenant(session_factory, "tenant-a")
        popola_tenant(session_factory, "tenant-b")  # entrambi usano "mov-0001"

    def test_ordine_step_unico_per_flusso(self, session_factory):
        ids = popola_tenant(session_factory, TENANT)
        with pytest.raises(IntegrityError):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, TENANT)
                repo.add(make_step(ids["flusso"], ordine=1))
                repo.flush()


class TestVincoliValori:
    def test_importo_negativo_rifiutato(self, session_factory):
        with pytest.raises(IntegrityError):
            popola_tenant(
                session_factory,
                TENANT,
                fattura={"importo": Decimal("-100.00"), "importo_residuo": Decimal("0.00")},
            )

    def test_residuo_maggiore_dell_importo_rifiutato(self, session_factory):
        with pytest.raises(IntegrityError):
            popola_tenant(
                session_factory,
                TENANT,
                fattura={
                    "importo": Decimal("100.00"),
                    "importo_residuo": Decimal("200.00"),
                },
            )

    def test_scadenza_precedente_all_emissione_rifiutata(self, session_factory):
        """Una T anteriore all'emissione genererebbe uno schedule già scaduto."""
        with pytest.raises(IntegrityError):
            popola_tenant(
                session_factory,
                TENANT,
                fattura={
                    "data_emissione": date(2026, 12, 31),
                    "data_scadenza": date(2026, 1, 1),
                },
            )

    def test_stato_fuori_enum_rifiutato(self, session_factory):
        ids = popola_tenant(session_factory, TENANT)
        with pytest.raises((IntegrityError, StatementError, LookupError, ValueError)):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, TENANT)
                fattura = repo.get(Fattura, ids["fattura"])
                fattura.stato = "pagata"
                repo.flush()

    def test_importi_conservano_i_centesimi(self, session_factory):
        """Numeric, non float: nessuna deriva sui decimali."""
        ids = popola_tenant(
            session_factory,
            TENANT,
            fattura={"importo": Decimal("1234.56"), "importo_residuo": Decimal("1234.56")},
        )
        with session_scope(session_factory) as session:
            fattura = TenantRepository(session, TENANT).get(Fattura, ids["fattura"])
            assert fattura.importo == Decimal("1234.56")
            assert isinstance(fattura.importo, Decimal)

    def test_gli_enum_si_rileggono_come_enum(self, session_factory):
        """Non stringhe: il dominio riceve i tipi che le firme dichiarano."""
        from irec.domain.enums import Pacchetto, StatoFattura

        ids = popola_tenant(session_factory, TENANT)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fattura = repo.get(Fattura, ids["fattura"])
            mandante = repo.get(Mandante, ids["mandante"])
            assert isinstance(fattura.stato, StatoFattura)
            assert fattura.stato is StatoFattura.GESTIONE
            assert isinstance(mandante.pacchetto, Pacchetto)

    def test_cancellare_il_cliente_cancella_le_sue_fatture(self, session_factory):
        """Cascade: nessun orfano dopo una cancellazione."""
        ids = popola_tenant(session_factory, TENANT)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            session.delete(repo.get(ClienteFinale, ids["cliente"]))
        with session_scope(session_factory) as session:
            assert TenantRepository(session, TENANT).list(Fattura) == []


class TestMigrazioni:
    def test_migrazione_allineata_ai_modelli(self, tmp_path):
        """`alembic upgrade head` deve produrre lo schema dei modelli.

        Confronta colonne, tipi, vincoli e indici (non solo i nomi delle
        tabelle): un modello modificato senza migrazione fa fallire qui.
        """
        db_path = tmp_path / "migrated.db"
        url = f"sqlite:///{db_path}"
        result = _alembic("upgrade", "head", database_url=url)
        assert result.returncode == 0, result.stderr

        engine = create_engine(url)
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
        with engine.connect() as connection:
            contesto = MigrationContext.configure(connection)
            differenze = compare_metadata(contesto, Base.metadata)

        # I CHECK generati dai tipi Enum non sono riflessi in modo affidabile
        # da Alembic e comparirebbero come differenze fantasma. Il loro
        # effetto è verificato da TestVincoliValori, non da qui.
        differenze = [
            diff
            for diff in differenze
            if not (
                isinstance(diff, tuple)
                and diff[0] in {"add_constraint", "remove_constraint"}
                and isinstance(diff[1], CheckConstraint)
            )
        ]
        assert differenze == [], differenze

    def test_migrazione_reversibile(self, tmp_path):
        """upgrade → downgrade → upgrade deve essere ripetibile."""
        url = f"sqlite:///{tmp_path / 'cycle.db'}"
        for args in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
            result = _alembic(*args, database_url=url)
            assert result.returncode == 0, result.stderr


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

    def test_ready_503_se_database_non_configurato(self, app, client):
        """Dichiararsi pronti senza database farebbe arrivare traffico a vuoto."""
        app.state.engine = None
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["reason"] == "database_not_configured"

    def test_ready_non_espone_la_connection_string(self, app, client):
        app.state.engine = create_engine(
            "postgresql+psycopg://utente:segreto@127.0.0.1:1/none"
        )
        response = client.get("/ready")
        assert "segreto" not in response.text


class TestRottaCancellazioneTenant:
    def test_cancella_solo_il_tenant_del_token(self, client, session_factory, make_token):
        popola_tenant(session_factory, TENANT)
        popola_tenant(session_factory, "tenant-altro")

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
            assert TenantRepository(session, TENANT).list(Fattura) == []
            assert len(TenantRepository(session, "tenant-altro").list(Fattura)) == 1

    def test_senza_scope_dedicato_non_cancella(self, client, session_factory, make_token):
        """L'operazione più distruttiva non passa con un token ordinario."""
        popola_tenant(session_factory, TENANT)
        token = make_token(scope="irec.read irec.write")

        response = client.request(
            "DELETE", "/v1/tenant", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
        assert response.json()["code"] == "scope_missing"
        with session_scope(session_factory) as session:
            assert len(TenantRepository(session, TENANT).list(Fattura)) == 1

    def test_senza_token_non_cancella_nulla(self, client, session_factory):
        popola_tenant(session_factory, TENANT)
        response = client.request("DELETE", "/v1/tenant")
        assert response.status_code == 401
        with session_scope(session_factory) as session:
            assert len(TenantRepository(session, TENANT).list(Fattura)) == 1

    def test_503_se_database_non_configurato(self, app, client, make_token):
        app.state.session_factory = None
        response = client.request(
            "DELETE", "/v1/tenant", headers={"Authorization": f"Bearer {make_token()}"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "database_not_configured"
