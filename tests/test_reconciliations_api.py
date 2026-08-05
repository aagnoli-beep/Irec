"""Rotte /v1/reconciliations: run asincrone con run_id e Idempotency-Key."""

from datetime import date

import pytest

from irec.adapters.db.models import SyncRun
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.adapters.mock import MockBanca, MockCassettoFiscale, MockRiconciliatore
from irec.adapters.mock.canali import MockCanaleInvio
from irec.adapters.mock.demo import scenario_demo
from irec.adapters.providers import ProviderSet
from irec.domain.enums import StatoRun
from tests.factories import make_mandante, make_sync_run

OGGI = date.today()
TENANT = "tenant-abc"


@pytest.fixture
def app_con_providers(app, session_factory):
    scenari = {TENANT: scenario_demo(OGGI)}
    app.state.providers = ProviderSet(
        fatture=MockCassettoFiscale(scenari, oggi=OGGI),
        movimenti=MockBanca(scenari, oggi=OGGI),
        riconciliatore=MockRiconciliatore(),
        canale_invio=MockCanaleInvio(),
    )
    with session_scope(session_factory) as session:
        TenantRepository(session, TENANT).add(make_mandante())
    return app


def _avvia(client, make_token, chiave: str = "run-key-1", **token_kwargs):
    return client.post(
        "/v1/reconciliations",
        headers={
            "Authorization": f"Bearer {make_token(**token_kwargs)}",
            "Idempotency-Key": chiave,
        },
    )


class TestAvvioRun:
    def test_202_con_run_id(self, app_con_providers, client, make_token):
        response = _avvia(client, make_token)
        assert response.status_code == 202
        assert response.json()["run_id"]

    def test_senza_idempotency_key_400(self, app_con_providers, client, make_token):
        response = client.post(
            "/v1/reconciliations",
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "missing_idempotency_key"

    def test_retry_con_stessa_chiave_restituisce_la_stessa_run(
        self, app_con_providers, client, make_token
    ):
        primo = _avvia(client, make_token, chiave="stessa-chiave")
        secondo = _avvia(client, make_token, chiave="stessa-chiave")
        assert primo.json()["run_id"] == secondo.json()["run_id"]

    def test_chiavi_diverse_creano_run_diverse(
        self, app_con_providers, client, make_token
    ):
        primo = _avvia(client, make_token, chiave="chiave-a")
        secondo = _avvia(client, make_token, chiave="chiave-b")
        assert primo.json()["run_id"] != secondo.json()["run_id"]

    def test_senza_token_401(self, app_con_providers, client):
        response = client.post(
            "/v1/reconciliations", headers={"Idempotency-Key": "k"}
        )
        assert response.status_code == 401

    def test_chiave_oltre_il_limite_400(self, app_con_providers, client, make_token):
        """Una chiave più lunga della colonna deve dare 400, non 500."""
        response = _avvia(client, make_token, chiave="k" * 129)
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_idempotency_key"

    def test_run_attiva_blocca_le_nuove_run_409(
        self, app_con_providers, client, make_token, session_factory
    ):
        """Una sola run attiva per tenant: il ciclo è costoso e due run
        sovrapposte si contenderebbero gli stessi dati."""
        with session_scope(session_factory) as session:
            TenantRepository(session, TENANT).add(
                make_sync_run(chiave_idempotenza="run-in-corso", stato=StatoRun.RUNNING)
            )
        response = _avvia(client, make_token, chiave="chiave-nuova")
        assert response.status_code == 409
        assert response.json()["code"] == "run_in_progress"

    def test_race_concorrente_stessa_chiave_riceve_la_stessa_run(
        self, app_con_providers, client, make_token, session_factory, monkeypatch
    ):
        """TOCTOU: se il lookup non vede la run (inserita da un retry
        concorrente), l'IntegrityError sul vincolo viene risolto
        restituendo la run vincente — mai un 500."""
        with session_scope(session_factory) as session:
            vincente = TenantRepository(session, TENANT).add(
                make_sync_run(
                    chiave_idempotenza="chiave-contesa", stato=StatoRun.COMPLETED
                )
            )
            session.flush()
            id_vincente = vincente.id

        originale = TenantRepository.find
        chiamate = {"n": 0}

        def find_cieco_al_primo_lookup(self, model, *criteri):
            chiamate["n"] += 1
            if chiamate["n"] == 1:
                return []  # simula il lookup che non vede la run concorrente
            return originale(self, model, *criteri)

        monkeypatch.setattr(TenantRepository, "find", find_cieco_al_primo_lookup)
        response = _avvia(client, make_token, chiave="chiave-contesa")

        assert response.status_code == 202
        assert response.json()["run_id"] == id_vincente

    def test_providers_non_configurati_503(self, app_con_providers, client, make_token):
        app_con_providers.state.providers = None
        response = _avvia(client, make_token)
        assert response.status_code == 503
        assert response.json()["code"] == "providers_not_configured"


class TestEsitoRun:
    def test_la_run_completa_e_produce_il_risultato(
        self, app_con_providers, client, make_token
    ):
        """Con TestClient i BackgroundTasks girano alla chiusura della
        risposta: al poll successivo la run è già conclusa."""
        run_id = _avvia(client, make_token).json()["run_id"]

        response = client.get(
            f"/v1/reconciliations/{run_id}",
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == StatoRun.COMPLETED
        # Con il lookback di default (30 gg) entrano le 2 fatture recenti
        # dello scenario demo, e i bonifici coprono 34-FA e parte di 35-FA.
        assert body["result"]["fatture_importate"] == 2
        assert body["result"]["pagamenti_registrati"] == 2
        assert body["error"] is None

    def test_run_inesistente_404(self, app_con_providers, client, make_token):
        response = client.get(
            "/v1/reconciliations/run-che-non-esiste",
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "run_not_found"

    def test_run_di_un_altro_tenant_e_indistinguibile_da_inesistente(
        self, app_con_providers, client, make_token
    ):
        run_id = _avvia(client, make_token).json()["run_id"]
        token_altro = make_token(tenant_id="tenant-intruso")
        response = client.get(
            f"/v1/reconciliations/{run_id}",
            headers={"Authorization": f"Bearer {token_altro}"},
        )
        assert response.status_code == 404

    def test_errore_del_provider_marca_la_run_failed(
        self, app_con_providers, client, make_token, session_factory
    ):
        """Un'eccezione imprevista non si propaga: la run diventa FAILED."""

        class ProviderRotto:
            def stato_collegamento(self, tenant_id: str):
                raise RuntimeError("boom interno")

            def recupera_fatture(self, tenant_id, dal, al):
                raise RuntimeError("boom interno")

        app_con_providers.state.providers = ProviderSet(
            fatture=ProviderRotto(),
            movimenti=app_con_providers.state.providers.movimenti,
            riconciliatore=app_con_providers.state.providers.riconciliatore,
            canale_invio=app_con_providers.state.providers.canale_invio,
        )
        run_id = _avvia(client, make_token, chiave="run-rotta").json()["run_id"]

        response = client.get(
            f"/v1/reconciliations/{run_id}",
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        body = response.json()
        assert body["status"] == StatoRun.FAILED
        assert body["error"] == "RuntimeError"
        assert body["result"] is None

    def test_shape_della_risposta_conforme_al_contratto(
        self, app_con_providers, client, make_token
    ):
        run_id = _avvia(client, make_token).json()["run_id"]
        body = client.get(
            f"/v1/reconciliations/{run_id}",
            headers={"Authorization": f"Bearer {make_token()}"},
        ).json()
        assert set(body) == {"run_id", "status", "result", "error"}
        assert body["status"] in {"queued", "running", "completed", "failed"}

    def test_timestamp_e_transizioni_della_run(
        self, app_con_providers, client, make_token, session_factory
    ):
        """QUEUED alla creazione; COMPLETED con avviata/conclusa valorizzate."""
        with session_scope(session_factory) as session:
            nuova = TenantRepository(session, TENANT).add(
                make_sync_run(chiave_idempotenza="appena-creata")
            )
            session.flush()
            assert nuova.stato is StatoRun.QUEUED
            assert nuova.avviata_at is None
            # Chiusa subito, altrimenti bloccherebbe la run successiva (409).
            nuova.stato = StatoRun.COMPLETED

        run_id = _avvia(client, make_token, chiave="run-completa").json()["run_id"]
        with session_scope(session_factory) as session:
            run = TenantRepository(session, TENANT).get(SyncRun, run_id)
            assert run.stato is StatoRun.COMPLETED
            assert run.avviata_at is not None
            assert run.conclusa_at is not None
            assert run.avviata_at <= run.conclusa_at


class TestCancellazioneGdprDuranteLaRun:
    def test_run_cancellata_prima_dell_esecuzione_termina_senza_effetti(
        self, app_con_providers, session_factory
    ):
        """DELETE /v1/tenant fra la creazione della run e l'esecuzione:
        il task termina senza eccezioni e non risuscita nulla."""
        from irec.services.sync_run import esegui_run

        with session_scope(session_factory) as session:
            run = TenantRepository(session, TENANT).add(
                make_sync_run(chiave_idempotenza="da-cancellare")
            )
            session.flush()
            run_id = run.id
        with session_scope(session_factory) as session:
            TenantRepository(session, TENANT).delete_tenant_data()

        esegui_run(
            session_factory,
            app_con_providers.state.providers,
            TENANT,
            run_id,
            correlation_id="corr-gdpr",
        )

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            assert repo.list(SyncRun) == []
            from irec.adapters.db.models import AuditLog, Fattura

            assert repo.list(Fattura) == []
            assert repo.list(AuditLog) == []
