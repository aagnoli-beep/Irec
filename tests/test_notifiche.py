"""Notifiche proattive: generazione nel ciclo, deduplica, polling (M6)."""

from datetime import date, timedelta
from decimal import Decimal

from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.adapters.mock import (
    MockBanca,
    MockCassettoFiscale,
    MockRiconciliatore,
    ScenarioTenant,
)
from irec.adapters.mock.canali import MockCanaleInvio
from irec.domain.enums import StatoFattura, TipoNotifica
from irec.domain.porte import CollegamentoEsterno, StatoCollegamento
from irec.services.invii import MotoreInvii
from irec.services.notifiche import (
    conteggio_per_tipo,
    emetti,
    marca_lette,
    notifica_collegamento,
    notifiche_da_consegnare,
)
from irec.services.sync import CicloSincronizzazione
from tests.factories import make_cliente, make_fattura, make_mandante, make_posizione

OGGI = date(2026, 8, 5)
TENANT = "tenant-abc"


def _fattura_a_giorni(session_factory, giorni_da_scadenza: int) -> str:
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, TENANT)
        m = repo.add(make_mandante())
        repo.flush()
        c = repo.add(make_cliente(m.id))
        repo.flush()
        p = repo.add(make_posizione(c.id))
        repo.flush()
        scadenza = OGGI - timedelta(days=giorni_da_scadenza)
        f = repo.add(
            make_fattura(
                p.id,
                c.id,
                data_emissione=scadenza - timedelta(days=30),
                data_scadenza=scadenza,
                stato=StatoFattura.GESTIONE,
            )
        )
        repo.flush()
        return f.id


class TestEmissioneEDeduplica:
    def test_emette_una_notifica(self, session_factory):
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            n = emetti(repo, TipoNotifica.DATO_IN_RITARDO, "rif-1")
            assert n is not None

    def test_deduplica_stessa_chiave(self, session_factory):
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            emetti(repo, TipoNotifica.DATO_IN_RITARDO, "rif-1")
            repo.flush()
            assert emetti(repo, TipoNotifica.DATO_IN_RITARDO, "rif-1") is None

    def test_ri_emette_dopo_che_e_stata_letta(self, session_factory):
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            n = emetti(repo, TipoNotifica.DATO_IN_RITARDO, "rif-1")
            repo.flush()
            marca_lette(repo, [n.id])
            # La situazione si ripresenta: nuova notifica.
            assert emetti(repo, TipoNotifica.DATO_IN_RITARDO, "rif-1") is not None


class TestCollegamenti:
    def test_collegamento_caduto_emette(self, session_factory):
        caduto = CollegamentoEsterno(stato=StatoCollegamento.SCADUTO)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            notifica_collegamento(repo, TipoNotifica.CONSENSO_PSD2, caduto)
            repo.flush()
            assert len(conteggio_per_tipo(repo)) == 1

    def test_collegamento_tornato_attivo_risolve(self, session_factory):
        caduto = CollegamentoEsterno(stato=StatoCollegamento.SCADUTO)
        attivo = CollegamentoEsterno(stato=StatoCollegamento.ATTIVO)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            notifica_collegamento(repo, TipoNotifica.CONSENSO_PSD2, caduto)
            repo.flush()
            notifica_collegamento(repo, TipoNotifica.CONSENSO_PSD2, attivo)
            repo.flush()
            # La notifica è stata marcata come letta: nulla da consegnare.
            assert notifiche_da_consegnare(repo) == []


class TestGenerazioneNelCiclo:
    def _ciclo(self, scenario: ScenarioTenant) -> CicloSincronizzazione:
        from datetime import UTC, datetime

        scenari = {TENANT: scenario}
        canale = MockCanaleInvio()
        adesso = datetime(2026, 8, 5, 12, tzinfo=UTC)
        return CicloSincronizzazione(
            MockCassettoFiscale(scenari, oggi=OGGI),
            MockBanca(scenari, oggi=OGGI),
            MockRiconciliatore(),
            oggi=OGGI,
            motore_invii=lambda: MotoreInvii(
                canale, adesso=adesso, email_recupero_crediti="rc@irec.example"
            ),
        )

    def test_preavviso_t44_genera_notifica(self, session_factory):
        _fattura_a_giorni(session_factory, 44)  # domani T+45
        scenario = ScenarioTenant()  # nessun recupero: collegamenti attivi
        with session_scope(session_factory) as session:
            esito = self._ciclo(scenario).esegui(TenantRepository(session, TENANT))
        assert esito.notifiche_generate == 1
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            notifiche = notifiche_da_consegnare(repo)
            assert len(notifiche) == 1
            assert notifiche[0].tipo is TipoNotifica.ESCALATION_IMMINENTE

    def test_ciclo_ripetuto_non_duplica_la_notifica(self, session_factory):
        _fattura_a_giorni(session_factory, 44)
        scenario = ScenarioTenant()
        with session_scope(session_factory) as session:
            self._ciclo(scenario).esegui(TenantRepository(session, TENANT))
        with session_scope(session_factory) as session:
            esito = self._ciclo(scenario).esegui(TenantRepository(session, TENANT))
        assert esito.notifiche_generate == 0
        with session_scope(session_factory) as session:
            assert len(notifiche_da_consegnare(TenantRepository(session, TENANT))) == 1

    def test_collegamento_caduto_nel_ciclo(self, session_factory):
        _fattura_a_giorni(session_factory, 10)
        scenario = ScenarioTenant(
            consenso_psd2=CollegamentoEsterno(stato=StatoCollegamento.SCADUTO)
        )
        with session_scope(session_factory) as session:
            self._ciclo(scenario).esegui(TenantRepository(session, TENANT))
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            tipi = {n.tipo for n in notifiche_da_consegnare(repo)}
            assert TipoNotifica.CONSENSO_PSD2 in tipi


class TestApiProattivo:
    def _prepara(self, session_factory) -> None:
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            m = repo.add(make_mandante())
            repo.flush()
            c = repo.add(make_cliente(m.id))
            repo.flush()
            p = repo.add(make_posizione(c.id))
            repo.flush()
            repo.add(
                make_fattura(
                    p.id,
                    c.id,
                    importo=Decimal("1000.00"),
                    importo_residuo=Decimal("400.00"),
                )
            )
            emetti(repo, TipoNotifica.CONSENSO_PSD2, "consenso_psd2", "scaduto")

    def test_brief(self, app, client, make_token, session_factory):
        self._prepara(session_factory)
        body = client.get(
            "/v1/brief", headers={"Authorization": f"Bearer {make_token()}"}
        ).json()
        assert body["affidato"] == "1000.00"
        assert body["recuperato"] == "600.00"
        assert body["azioni_principali"][0]["tipo"] == "consenso_psd2"

    def test_notifications_e_ack(self, app, client, make_token, session_factory):
        self._prepara(session_factory)
        hdr = {"Authorization": f"Bearer {make_token()}"}
        items = client.get("/v1/notifications", headers=hdr).json()["items"]
        assert len(items) == 1
        nid = items[0]["id"]

        ack = client.post(
            "/v1/notifications/ack", headers=hdr, json={"ids": [nid]}
        ).json()
        assert ack["marcate"] == 1
        # Dopo l'ack, la coda è vuota.
        assert client.get("/v1/notifications", headers=hdr).json()["items"] == []

    def test_brief_senza_token_401(self, app, client, session_factory):
        assert client.get("/v1/brief").status_code == 401
