"""Motore solleciti end-to-end (M4): invii, consolidamento, escalation, pause."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from irec.adapters.db.models import Comunicazione, Fattura
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.adapters.mock.canali import MockCanaleInvio
from irec.domain.enums import (
    Canale,
    Pacchetto,
    StatoComunicazione,
    StatoFattura,
)
from irec.services.invii import MotoreInvii, metti_in_pausa, ricalcola_schedule
from tests.factories import (
    make_cliente,
    make_comunicazione,
    make_fattura,
    make_mandante,
    make_posizione,
)

TENANT = "tenant-abc"


def istante(anno: int, mese: int, giorno: int, ora: int = 12) -> datetime:
    return datetime(anno, mese, giorno, ora, 0, tzinfo=UTC)


def motore(canale: MockCanaleInvio, adesso: datetime) -> MotoreInvii:
    return MotoreInvii(canale, adesso=adesso, email_recupero_crediti="rc@irec.example")


def prepara(
    session_factory,
    *,
    pacchetto: Pacchetto = Pacchetto.VALUE,
    email: str | None = "cli@example.it",
    telefono: str | None = "+39333",
    alias: str | None = "acme@irec.example",
) -> dict[str, str]:
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, TENANT)
        mandante = repo.add(make_mandante(pacchetto=pacchetto, alias_email=alias))
        repo.flush()
        cliente = repo.add(make_cliente(mandante.id, email=email, telefono=telefono))
        repo.flush()
        posizione = repo.add(make_posizione(cliente.id))
        repo.flush()
        fattura = repo.add(make_fattura(posizione.id, cliente.id))
        repo.flush()
        return {"mandante": mandante.id, "cliente": cliente.id, "fattura": fattura.id}


def programma(
    session_factory,
    fattura_id: str,
    canale: Canale,
    quando: datetime,
    template: str = "sollecito_1",
) -> str:
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, TENANT)
        com = repo.add(
            make_comunicazione(
                fattura_id, canale=canale, template=template, programmata_per=quando
            )
        )
        repo.flush()
        return com.id


class TestInvioDovuto:
    def test_comunicazione_dovuta_viene_inviata(self, session_factory):
        ids = prepara(session_factory)
        programma(session_factory, ids["fattura"], Canale.EMAIL, istante(2026, 8, 10, 9))
        canale = MockCanaleInvio()

        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10, 12)).esegui(
                TenantRepository(session, TENANT)
            )

        assert esito.comunicazioni_inviate == 1
        assert esito.messaggi_inviati == 1
        assert len(canale.inviati) == 1
        assert canale.inviati[0].messaggio.destinatario == "cli@example.it"

    def test_comunicazione_futura_non_parte(self, session_factory):
        ids = prepara(session_factory)
        programma(session_factory, ids["fattura"], Canale.EMAIL, istante(2026, 8, 20))
        canale = MockCanaleInvio()

        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.comunicazioni_inviate == 0
        assert canale.inviati == []

    def test_fattura_saldata_non_riceve_solleciti(self, session_factory):
        """US-08 via controllo just-in-time: la comunicazione è annullata."""
        ids = prepara(session_factory)
        com_id = programma(
            session_factory, ids["fattura"], Canale.EMAIL, istante(2026, 8, 10, 9)
        )
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fattura = repo.get(Fattura, ids["fattura"])
            fattura.stato = StatoFattura.SALDATA
            fattura.importo_residuo = Decimal("0.00")

        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10, 12)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.comunicazioni_inviate == 0
        assert canale.inviati == []
        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, com_id)
            assert com.stato is StatoComunicazione.ANNULLATA


class TestConsolidamentoInvii:
    def test_due_fatture_stesso_cliente_un_solo_messaggio(self, session_factory):
        ids = prepara(session_factory)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            seconda = repo.add(
                make_fattura(
                    repo.get(Fattura, ids["fattura"]).posizione_id,
                    ids["cliente"],
                    numero="99-FA",
                )
            )
            repo.flush()
            seconda_id = seconda.id
        programma(session_factory, ids["fattura"], Canale.EMAIL, istante(2026, 8, 10, 9))
        programma(session_factory, seconda_id, Canale.EMAIL, istante(2026, 8, 10, 9))

        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10, 12)).esegui(
                TenantRepository(session, TENANT)
            )
        # Due comunicazioni, un solo messaggio recapitato (consolidamento).
        assert esito.comunicazioni_inviate == 2
        assert esito.messaggi_inviati == 1
        assert len(canale.inviati) == 1
        assert set(canale.inviati[0].messaggio.numeri_fattura) == {"32-FA", "99-FA"}
        assert canale.inviati[0].messaggio.importo_totale_residuo == "2440.00"


class TestCanaliPerPacchetto:
    def test_whatsapp_su_entry_e_saltato(self, session_factory):
        ids = prepara(session_factory, pacchetto=Pacchetto.ENTRY)
        com_id = programma(
            session_factory, ids["fattura"], Canale.WHATSAPP, istante(2026, 8, 10, 9)
        )
        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10, 12)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.comunicazioni_saltate == 1
        assert canale.inviati == []
        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, com_id)
            assert com.stato is StatoComunicazione.SALTATA
            assert com.esito_recapito == "canale_non_nel_pacchetto"

    def test_recapito_mancante_salta_ma_il_flusso_prosegue(self, session_factory):
        ids = prepara(session_factory, telefono=None)
        programma(
            session_factory, ids["fattura"], Canale.WHATSAPP, istante(2026, 8, 10, 9)
        )
        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10, 12)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.comunicazioni_saltate == 1
        assert any("recapito_mancante" in s for s in esito.segnalazioni)


class TestInvioFallito:
    def test_canale_in_errore_marca_fallita(self, session_factory):
        ids = prepara(session_factory)
        com_id = programma(
            session_factory, ids["fattura"], Canale.EMAIL, istante(2026, 8, 10, 9)
        )
        canale = MockCanaleInvio(canali_in_errore={"email"})
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10, 12)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.comunicazioni_fallite == 1
        assert esito.comunicazioni_inviate == 0
        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, com_id)
            assert com.stato is StatoComunicazione.FALLITA


class TestEscalation:
    def _fattura_a_t45(self, session_factory, giorni: int) -> dict[str, str]:
        ids = prepara(session_factory)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fattura = repo.get(Fattura, ids["fattura"])
            # Scadenza tale che "adesso" sia a T+giorni.
            fattura.data_scadenza = date(2026, 8, 10) - timedelta(days=giorni)
            fattura.data_emissione = fattura.data_scadenza - timedelta(days=30)
        return ids

    def test_t45_manda_le_due_mail_e_marca_insoluto(self, session_factory):
        ids = self._fattura_a_t45(session_factory, 45)
        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10)).esegui(
                TenantRepository(session, TENANT)
            )

        assert esito.escalation_eseguite == 1
        # Mail a Recupero Crediti + mail al mandante.
        destinatari = {reg.messaggio.destinatario for reg in canale.inviati}
        assert "rc@irec.example" in destinatari
        assert "acme@irec.example" in destinatari
        with session_scope(session_factory) as session:
            fattura = TenantRepository(session, TENANT).get(Fattura, ids["fattura"])
            assert fattura.stato is StatoFattura.INSOLUTO

    def test_t44_e_solo_preavviso(self, session_factory):
        self._fattura_a_t45(session_factory, 44)
        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.escalation_imminenti == 1
        assert esito.escalation_eseguite == 0

    def test_escalation_annulla_i_solleciti_pendenti(self, session_factory):
        ids = self._fattura_a_t45(session_factory, 45)
        com_id = programma(
            session_factory, ids["fattura"], Canale.EMAIL, istante(2026, 8, 10, 9)
        )
        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            motore(canale, istante(2026, 8, 10)).esegui(TenantRepository(session, TENANT))
        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, com_id)
            assert com.stato is StatoComunicazione.ANNULLATA


class TestPause:
    def test_pausa_scaduta_riprende_il_flusso(self, session_factory):
        ids = prepara(session_factory)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fattura = repo.get(Fattura, ids["fattura"])
            metti_in_pausa(
                repo, fattura, date(2026, 8, 5), operatore="u1", motivo="promessa"
            )

        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.pause_riprese == 1
        with session_scope(session_factory) as session:
            fattura = TenantRepository(session, TENANT).get(Fattura, ids["fattura"])
            assert fattura.stato is StatoFattura.GESTIONE
            assert fattura.pausa_fino_a is None

    def test_pausa_non_ancora_scaduta_resta(self, session_factory):
        ids = prepara(session_factory)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            metti_in_pausa(
                repo,
                repo.get(Fattura, ids["fattura"]),
                date(2026, 8, 20),
                operatore="u1",
                motivo="promessa",
            )
        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.pause_riprese == 0

    def test_fattura_in_pausa_non_riceve_solleciti(self, session_factory):
        ids = prepara(session_factory)
        programma(session_factory, ids["fattura"], Canale.EMAIL, istante(2026, 8, 10, 9))
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            metti_in_pausa(
                repo,
                repo.get(Fattura, ids["fattura"]),
                date(2026, 8, 20),  # ancora in pausa
                operatore="u1",
                motivo="contestazione",
            )
        canale = MockCanaleInvio()
        with session_scope(session_factory) as session:
            esito = motore(canale, istante(2026, 8, 10, 12)).esegui(
                TenantRepository(session, TENANT)
            )
        assert esito.comunicazioni_inviate == 0
        assert canale.inviati == []


class TestRicalcoloSchedule:
    def test_modifica_scadenza_ricalcola_gli_step_futuri(self, session_factory):
        from tests.factories import make_flusso, make_step

        ids = prepara(session_factory)
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            flusso = repo.add(make_flusso(ids["mandante"]))
            repo.flush()
            step = repo.add(make_step(flusso.id, offset_giorni=3))
            repo.flush()
            fattura = repo.get(Fattura, ids["fattura"])
            com = repo.add(
                make_comunicazione(
                    fattura.id,
                    step_id=step.id,
                    programmata_per=istante(2026, 9, 3, 8),
                )
            )
            repo.flush()
            com_id = com.id

            fattura.data_scadenza = date(2026, 10, 1)
            ricalcolate = ricalcola_schedule(repo, fattura)
            assert ricalcolate == 1

        with session_scope(session_factory) as session:
            com = TenantRepository(session, TENANT).get(Comunicazione, com_id)
            # T (1/10) + 3 gg = 4/10 (domenica) → lunedì 5/10.
            assert com.programmata_per.astimezone(UTC).date() == date(2026, 10, 5)
