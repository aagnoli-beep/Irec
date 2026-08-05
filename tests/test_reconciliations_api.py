"""Rotte /v1/reconciliations: run asincrone con run_id e Idempotency-Key."""

from datetime import date

import pytest

from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.adapters.mock import MockBanca, MockCassettoFiscale, MockRiconciliatore
from irec.adapters.mock.demo import scenario_demo
from irec.adapters.providers import ProviderSet
from irec.domain.enums import StatoRun
from tests.factories import make_mandante

OGGI = date.today()
TENANT = "tenant-abc"


@pytest.fixture
def app_con_providers(app, session_factory):
    scenari = {TENANT: scenario_demo(OGGI)}
    app.state.providers = ProviderSet(
        fatture=MockCassettoFiscale(scenari, oggi=OGGI),
        movimenti=MockBanca(scenari, oggi=OGGI),
        riconciliatore=MockRiconciliatore(),
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
        assert body["risultato"]["fatture_importate"] == 2
        assert body["risultato"]["pagamenti_registrati"] == 2
        assert body["errore"] is None

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
        )
        run_id = _avvia(client, make_token, chiave="run-rotta").json()["run_id"]

        response = client.get(
            f"/v1/reconciliations/{run_id}",
            headers={"Authorization": f"Bearer {make_token()}"},
        )
        body = response.json()
        assert body["status"] == StatoRun.FAILED
        assert body["errore"] == "RuntimeError"
        assert body["risultato"] is None
