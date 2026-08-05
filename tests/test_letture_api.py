"""Rotte di lettura autonoma /v1 (M5)."""

from datetime import date
from decimal import Decimal

import pytest

from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.domain.enums import StatoComunicazione, StatoFattura
from tests.factories import (
    make_cliente,
    make_comunicazione,
    make_fattura,
    make_mandante,
    make_posizione,
)

TENANT = "tenant-abc"


@pytest.fixture
def dati(session_factory) -> dict[str, str]:
    """Un mandante, un cliente, una posizione, due fatture con storico."""
    from datetime import UTC, datetime

    with session_scope(session_factory) as session:
        repo = TenantRepository(session, TENANT)
        mandante = repo.add(make_mandante())
        repo.flush()
        cliente = repo.add(make_cliente(mandante.id))
        repo.flush()
        posizione = repo.add(make_posizione(cliente.id))
        repo.flush()
        f1 = repo.add(
            make_fattura(
                posizione.id,
                cliente.id,
                numero="A-1",
                importo=Decimal("1000.00"),
                importo_residuo=Decimal("400.00"),
                data_scadenza=date(2026, 7, 1),
            )
        )
        f2 = repo.add(
            make_fattura(
                posizione.id,
                cliente.id,
                numero="A-2",
                importo=Decimal("500.00"),
                importo_residuo=Decimal("0.00"),
                data_scadenza=date(2026, 9, 1),
                stato=StatoFattura.SALDATA,
            )
        )
        repo.flush()
        com = repo.add(
            make_comunicazione(
                f1.id,
                programmata_per=datetime(2026, 6, 29, 8, tzinfo=UTC),
                inviata_at=datetime(2026, 6, 29, 10, tzinfo=UTC),
                stato=StatoComunicazione.INVIATA,
            )
        )
        repo.flush()
        return {
            "cliente": cliente.id,
            "posizione": posizione.id,
            "f1": f1.id,
            "f2": f2.id,
            "com": com.id,
        }


def _get(client, make_token, path: str):
    return client.get(path, headers={"Authorization": f"Bearer {make_token()}"})


class TestPortfolio:
    def test_kpi(self, app, client, make_token, dati):
        body = _get(client, make_token, "/v1/portfolio").json()
        # affidato 1500; recuperato = 600 (400 su A-1) + 500 (A-2 saldata).
        assert body["affidato"] == "1500.00"
        assert body["recuperato"] == "1100.00"
        assert body["da_recuperare"] == "400.00"
        assert body["passato_a_recupero"] == "0.00"
        assert body["fatture_per_stato"]["saldata"] == 1

    def test_importi_come_stringhe(self, app, client, make_token, dati):
        body = _get(client, make_token, "/v1/portfolio").json()
        assert isinstance(body["affidato"], str)

    def test_senza_token_401(self, app, client, dati):
        assert client.get("/v1/portfolio").status_code == 401


class TestAging:
    def test_bucket(self, app, client, make_token, dati):
        body = _get(client, make_token, "/v1/aging?as_of=2026-08-01").json()
        labels = {b["label"] for b in body["buckets"]}
        assert labels == {"a_scadere", "0-30", "31-60", "61-90", "90+"}
        # A-1 scade 1/7, as_of 1/8 → 31 giorni → bucket 31-60.
        b31 = next(b for b in body["buckets"] if b["label"] == "31-60")
        assert b31["importo"] == "400.00"
        # A-2 è saldata: non entra nell'aging.
        scadere = next(b for b in body["buckets"] if b["label"] == "a_scadere")
        assert scadere["numero_fatture"] == 0


class TestInvoices:
    def test_tutte(self, app, client, make_token, dati):
        body = _get(client, make_token, "/v1/invoices").json()
        assert [f["numero"] for f in body["items"]] == ["A-1", "A-2"]

    def test_filtro_stato(self, app, client, make_token, dati):
        body = _get(client, make_token, "/v1/invoices?status=saldata").json()
        assert [f["numero"] for f in body["items"]] == ["A-2"]

    def test_filtro_scaduta(self, app, client, make_token, dati):
        body = _get(
            client, make_token, "/v1/invoices?status=scaduta&as_of=2026-08-01"
        ).json()
        assert [f["numero"] for f in body["items"]] == ["A-1"]

    def test_stato_sconosciuto_400(self, app, client, make_token, dati):
        r = _get(client, make_token, "/v1/invoices?status=inesistente")
        assert r.status_code == 400


class TestPosition:
    def test_dettaglio(self, app, client, make_token, dati):
        body = _get(client, make_token, f"/v1/positions/{dati['posizione']}").json()
        assert body["importo_totale_residuo"] == "400.00"
        assert len(body["fatture"]) == 2

    def test_posizione_altrui_404(self, app, client, make_token, session_factory, dati):
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, "tenant-altro")
            m = repo.add(make_mandante())
            repo.flush()
            c = repo.add(make_cliente(m.id, piva_cf="11111111111"))
            repo.flush()
            p = repo.add(make_posizione(c.id))
            repo.flush()
            altrui = p.id
        r = _get(client, make_token, f"/v1/positions/{altrui}")
        assert r.status_code == 404


class TestStoricoESpiegazione:
    def test_storico(self, app, client, make_token, dati):
        body = _get(client, make_token, f"/v1/invoices/{dati['f1']}/history").json()
        assert len(body["comunicazioni"]) == 1
        assert body["comunicazioni"][0]["stato"] == "inviata"

    def test_prossimi_invii_solo_programmate(self, app, client, make_token, dati):
        body = _get(client, make_token, f"/v1/invoices/{dati['f1']}/next").json()
        assert body["prossimi"] == []  # l'unica comunicazione è già inviata

    def test_spiegazione_inviata(self, app, client, make_token, dati):
        body = _get(
            client, make_token, f"/v1/communications/{dati['com']}/explain"
        ).json()
        assert body["codice"] == "inviata"


class TestUsage:
    def test_metriche(self, app, client, make_token, dati):
        body = _get(client, make_token, "/v1/usage?from=2026-06-01&to=2026-06-30").json()
        assert body["tenant_id"] == TENANT
        assert "messaggi_inviati" in body["metrics"]
        assert body["metrics"]["messaggi_inviati"] == 1
