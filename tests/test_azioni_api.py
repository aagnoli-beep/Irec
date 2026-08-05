"""Rotte di azione con conferma /v1 (M5): scope, permessi per pacchetto, stati."""

from datetime import UTC, datetime

import pytest

from irec.adapters.db.models import Comunicazione, Fattura, Flusso
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.adapters.mock.canali import MockCanaleInvio
from irec.adapters.providers import ProviderSet
from irec.domain.enums import (
    Canale,
    Pacchetto,
    StatoComunicazione,
)
from tests.factories import (
    make_cliente,
    make_comunicazione,
    make_fattura,
    make_mandante,
    make_posizione,
)

TENANT = "tenant-abc"


@pytest.fixture
def canale() -> MockCanaleInvio:
    return MockCanaleInvio()


@pytest.fixture
def app_azioni(app, canale):
    app.state.providers = ProviderSet(
        fatture=object(),
        movimenti=object(),
        riconciliatore=object(),
        canale_invio=canale,
    )
    return app


@pytest.fixture
def dati(session_factory, pacchetto=Pacchetto.VALUE):
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, TENANT)
        mandante = repo.add(make_mandante(pacchetto=Pacchetto.VALUE))
        repo.flush()
        cliente = repo.add(make_cliente(mandante.id, email="cli@ex.it", telefono="+39"))
        repo.flush()
        posizione = repo.add(make_posizione(cliente.id))
        repo.flush()
        fattura = repo.add(make_fattura(posizione.id, cliente.id))
        repo.flush()
        com = repo.add(
            make_comunicazione(
                fattura.id,
                canale=Canale.EMAIL,
                programmata_per=datetime(2026, 9, 1, 8, tzinfo=UTC),
            )
        )
        repo.flush()
        return {"cliente": cliente.id, "fattura": fattura.id, "com": com.id}


def _post(client, make_token, path, json=None, scope="irec.read irec.write"):
    return client.post(
        path,
        headers={"Authorization": f"Bearer {make_token(scope=scope)}"},
        json=json,
    )


class TestScopeDiScrittura:
    def test_senza_scope_write_403(self, app_azioni, client, make_token, dati):
        r = _post(
            client,
            make_token,
            f"/v1/invoices/{dati['fattura']}/pause",
            json={},
            scope="irec.read",
        )
        assert r.status_code == 403
        assert r.json()["code"] == "scope_missing"

    def test_senza_token_401(self, app_azioni, client, dati):
        r = client.post(f"/v1/invoices/{dati['fattura']}/pause", json={})
        assert r.status_code == 401


class TestPausaRipresa:
    def test_pausa_e_ripresa(self, app_azioni, client, make_token, dati, session_factory):
        r = _post(
            client,
            make_token,
            f"/v1/invoices/{dati['fattura']}/pause",
            json={"fino_a": "2026-09-15", "motivo": "promessa"},
        )
        assert r.status_code == 200
        assert r.json()["stato"] == "pausa"

        r = _post(client, make_token, f"/v1/invoices/{dati['fattura']}/resume")
        assert r.json()["stato"] == "gestione"
        with session_scope(session_factory) as session:
            fattura = TenantRepository(session, TENANT).get(Fattura, dati["fattura"])
            assert fattura.pausa_fino_a is None

    def test_riprendi_non_in_pausa_409(self, app_azioni, client, make_token, dati):
        r = _post(client, make_token, f"/v1/invoices/{dati['fattura']}/resume")
        assert r.status_code == 409


class TestComunicazioni:
    def test_annulla(self, app_azioni, client, make_token, dati, session_factory):
        r = _post(client, make_token, f"/v1/communications/{dati['com']}/cancel")
        assert r.status_code == 200
        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, dati["com"])
            assert com.stato is StatoComunicazione.ANNULLATA

    def test_forza_invio(self, app_azioni, client, make_token, dati, canale, session_factory):
        r = _post(client, make_token, f"/v1/communications/{dati['com']}/force")
        assert r.status_code == 200
        assert len(canale.inviati) == 1
        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, dati["com"])
            assert com.stato is StatoComunicazione.INVIATA

    def test_annulla_gia_annullata_409(
        self, app_azioni, client, make_token, dati, session_factory
    ):
        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, dati["com"])
            com.stato = StatoComunicazione.INVIATA
        r = _post(client, make_token, f"/v1/communications/{dati['com']}/cancel")
        assert r.status_code == 409


class TestPagamentoManuale:
    def test_pagamento_parziale(self, app_azioni, client, make_token, dati):
        r = _post(
            client,
            make_token,
            f"/v1/invoices/{dati['fattura']}/payments",
            json={
                "importo": "220.00",
                "data_pagamento": "2026-09-01",
                "idempotency_key": "pay-1",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["stato"] == "gestione"
        assert body["gia_registrato"] is False

    def test_pagamento_totale_salda_e_annulla(
        self, app_azioni, client, make_token, dati
    ):
        r = _post(
            client,
            make_token,
            f"/v1/invoices/{dati['fattura']}/payments",
            json={
                "importo": "1220.00",
                "data_pagamento": "2026-09-01",
                "idempotency_key": "pay-full",
            },
        )
        body = r.json()
        assert body["stato"] == "saldata"
        assert body["comunicazioni_annullate"] == 1

    def test_idempotenza(self, app_azioni, client, make_token, dati):
        payload = {
            "importo": "100.00",
            "data_pagamento": "2026-09-01",
            "idempotency_key": "pay-idem",
        }
        _post(client, make_token, f"/v1/invoices/{dati['fattura']}/payments", json=payload)
        r = _post(
            client, make_token, f"/v1/invoices/{dati['fattura']}/payments", json=payload
        )
        assert r.json()["gia_registrato"] is True

    def test_importo_non_positivo_400(self, app_azioni, client, make_token, dati):
        r = _post(
            client,
            make_token,
            f"/v1/invoices/{dati['fattura']}/payments",
            json={
                "importo": "0.00",
                "data_pagamento": "2026-09-01",
                "idempotency_key": "pay-zero",
            },
        )
        assert r.status_code == 400


class TestRecapiti:
    def test_aggiorna_email(self, app_azioni, client, make_token, dati, session_factory):
        r = client.patch(
            f"/v1/clients/{dati['cliente']}/contacts",
            headers={"Authorization": f"Bearer {make_token(scope='irec.write')}"},
            json={"email": "nuova@ex.it"},
        )
        assert r.status_code == 200

    def test_canale_opt_out_invalido_400(self, app_azioni, client, make_token, dati):
        r = client.patch(
            f"/v1/clients/{dati['cliente']}/contacts",
            headers={"Authorization": f"Bearer {make_token(scope='irec.write')}"},
            json={"canali_opt_out": ["piccione"]},
        )
        assert r.status_code == 400


class TestFlussoEPermessiPacchetto:
    def _flusso_body(self):
        return {
            "steps": [
                {"ordine": 1, "offset_giorni": -2, "canale": "email", "template": "t1"},
                {"ordine": 2, "offset_giorni": 3, "canale": "whatsapp", "template": "t2"},
            ]
        }

    def test_value_puo_personalizzare(
        self, app_azioni, client, make_token, dati, session_factory
    ):
        r = client.put(
            "/v1/flow",
            headers={"Authorization": f"Bearer {make_token(scope='irec.write')}"},
            json=self._flusso_body(),
        )
        assert r.status_code == 200
        with session_scope(session_factory) as session:
            flussi = [f for f in TenantRepository(session, TENANT).list(Flusso) if f.attivo]
            assert len(flussi) == 1
            assert flussi[0].nome == "Flusso personalizzato"

    def test_entry_riceve_upsell(self, app_azioni, client, make_token, session_factory):
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            repo.add(make_mandante(pacchetto=Pacchetto.ENTRY))
        r = client.put(
            "/v1/flow",
            headers={"Authorization": f"Bearer {make_token(scope='irec.write')}"},
            json={
                "steps": [
                    {"ordine": 1, "offset_giorni": 3, "canale": "email", "template": "t"}
                ]
            },
        )
        assert r.status_code == 403
        assert r.json()["code"] == "upgrade_required"
        # Non un errore freddo: il messaggio invita all'upgrade.
        assert "Value" in r.json()["error"]

    def test_value_non_puo_usare_voice(
        self, app_azioni, client, make_token, dati
    ):
        r = client.put(
            "/v1/flow",
            headers={"Authorization": f"Bearer {make_token(scope='irec.write')}"},
            json={
                "steps": [
                    {"ordine": 1, "offset_giorni": 3, "canale": "voice", "template": "t"}
                ]
            },
        )
        assert r.status_code == 403
        assert r.json()["code"] == "upgrade_required"


class TestReport:
    def test_invia_report(self, app_azioni, client, make_token, dati, canale):
        r = _post(client, make_token, "/v1/report")
        assert r.status_code == 200
        assert r.json()["inviato"] is True
        assert len(canale.inviati) == 1
        assert canale.inviati[0].messaggio.template == "report_periodico"
