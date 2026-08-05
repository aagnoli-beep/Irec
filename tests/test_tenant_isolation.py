"""Isolamento multi-tenant: la garanzia cardine di IREC.

Il tenant arriva dal call-token e filtra ogni lettura, scrittura e
cancellazione. Un tenant non deve mai vedere né toccare i dati di un altro.
La garanzia è verificata su TUTTE le entità dello schema, non solo sulle
radici: è nelle foglie (audit, pagamenti) che i dati personali si nascondono.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from irec.adapters.db.models import (
    AuditLog,
    ClienteFinale,
    Comunicazione,
    Fattura,
    Flusso,
    FlussoStep,
    Mandante,
    Pagamento,
    Posizione,
    SyncRun,
)
from irec.adapters.db.repository import TenantRepository, TenantViolation
from irec.adapters.db.session import session_scope
from tests.factories import make_cliente, make_mandante, popola_tenant

TUTTI_I_MODELLI = [
    Mandante,
    ClienteFinale,
    Posizione,
    Fattura,
    Flusso,
    FlussoStep,
    Comunicazione,
    Pagamento,
    AuditLog,
    SyncRun,
]


class TestLettura:
    @pytest.mark.parametrize("model", TUTTI_I_MODELLI)
    def test_nessun_modello_espone_righe_di_altri_tenant(self, session_factory, model):
        popola_tenant(session_factory, "tenant-a")
        popola_tenant(session_factory, "tenant-b")

        with session_scope(session_factory) as session:
            righe = TenantRepository(session, "tenant-a").list(model)
            assert len(righe) == 1
            assert all(riga.tenant_id == "tenant-a" for riga in righe)

    @pytest.mark.parametrize("model", TUTTI_I_MODELLI)
    def test_get_per_id_di_altro_tenant_restituisce_none(self, session_factory, model):
        popola_tenant(session_factory, "tenant-a")
        ids_b = popola_tenant(session_factory, "tenant-b")
        chiave = {
            Mandante: "mandante",
            ClienteFinale: "cliente",
            Posizione: "posizione",
            Fattura: "fattura",
            Flusso: "flusso",
            FlussoStep: "step",
            Comunicazione: "comunicazione",
            Pagamento: "pagamento",
            AuditLog: "audit",
            SyncRun: "run",
        }[model]

        with session_scope(session_factory) as session:
            assert TenantRepository(session, "tenant-a").get(model, ids_b[chiave]) is None

    def test_navigare_le_relazioni_non_porta_fuori_dal_tenant(self, session_factory):
        """Le FK composite impediscono grafi che attraversano il confine."""
        popola_tenant(session_factory, "tenant-a")
        popola_tenant(session_factory, "tenant-b")

        with session_scope(session_factory) as session:
            fattura = TenantRepository(session, "tenant-a").list(Fattura)[0]
            assert fattura.posizione.tenant_id == "tenant-a"
            assert fattura.posizione.cliente.tenant_id == "tenant-a"
            assert fattura.posizione.cliente.mandante.tenant_id == "tenant-a"


class TestScrittura:
    def test_scrittura_forza_il_tenant_del_repository(self, session_factory):
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, "tenant-a")
            mandante = repo.add(make_mandante())
            repo.flush()
            assert mandante.tenant_id == "tenant-a"

    def test_scrittura_su_tenant_diverso_e_rifiutata(self, session_factory):
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, "tenant-a")
            entita = make_mandante()
            entita.tenant_id = "tenant-b"
            with pytest.raises(TenantViolation, match="tenant diverso"):
                repo.add(entita)

    def test_tenant_alterato_dopo_add_e_rifiutato(self, session_factory):
        """Il guard before_flush copre ciò che add() non può vedere."""
        with pytest.raises(TenantViolation, match="fuori dal tenant"):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, "tenant-a")
                mandante = repo.add(make_mandante())
                mandante.tenant_id = "tenant-b"
                repo.flush()

    def test_tenant_di_una_riga_esistente_non_e_modificabile(self, session_factory):
        """Nessuno può spostare righe già salvate in un altro tenant."""
        popola_tenant(session_factory, "tenant-a")

        with pytest.raises(TenantViolation):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, "tenant-a")
                mandante = repo.list(Mandante)[0]
                mandante.tenant_id = "tenant-b"
                repo.flush()

        with session_scope(session_factory) as session:
            assert TenantRepository(session, "tenant-b").list(Mandante) == []

    def test_foreign_key_verso_un_altro_tenant_e_rifiutata(self, session_factory):
        """Il database non accetta archi fra tenant diversi."""
        ids_a = popola_tenant(session_factory, "tenant-a")

        with pytest.raises(IntegrityError):
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, "tenant-b")
                repo.add(make_cliente(ids_a["mandante"], piva_cf="99999999999"))
                repo.flush()

    def test_repository_senza_tenant_e_rifiutato(self, session_factory):
        with session_scope(session_factory) as session:
            with pytest.raises(ValueError, match="tenant_id obbligatorio"):
                TenantRepository(session, "")

    def test_una_sessione_un_tenant(self, session_factory):
        """Un secondo repository con tenant diverso sulla stessa sessione è
        un errore di programmazione, non un degrado silenzioso."""
        with session_scope(session_factory) as session:
            TenantRepository(session, "tenant-a")
            with pytest.raises(TenantViolation, match="un tenant"):
                TenantRepository(session, "tenant-b")

    def test_furto_di_riga_verso_il_proprio_tenant_e_rifiutato(self, session_factory):
        """Il guard sullo storico: una riga di tenant-a riassegnata a
        tenant-b DA una sessione tenant-b supera il primo check (il valore
        nuovo coincide con l'atteso) e deve cadere sul secondo."""
        ids_a = popola_tenant(session_factory, "tenant-a")

        with pytest.raises(TenantViolation, match="non è modificabile"):
            with session_scope(session_factory) as session:
                TenantRepository(session, "tenant-b")
                # Accesso raw deliberato: simula il codice scritto male che
                # il guard deve fermare.
                mandante = session.get(Mandante, ids_a["mandante"])
                mandante.tenant_id = "tenant-b"
                session.flush()

        with session_scope(session_factory) as session:
            assert TenantRepository(session, "tenant-b").list(Mandante) == []


class TestCancellazioneGdpr:
    @pytest.mark.parametrize("model", TUTTI_I_MODELLI)
    def test_cancella_ogni_tabella_del_proprio_tenant(self, session_factory, model):
        popola_tenant(session_factory, "tenant-a")
        popola_tenant(session_factory, "tenant-b")

        with session_scope(session_factory) as session:
            TenantRepository(session, "tenant-a").delete_tenant_data()

        # Una sessione = un tenant: la verifica dei due tenant usa
        # sessioni separate.
        with session_scope(session_factory) as session:
            assert TenantRepository(session, "tenant-a").list(model) == []
        with session_scope(session_factory) as session:
            assert len(TenantRepository(session, "tenant-b").list(model)) == 1

    def test_conteggi_coprono_tutte_le_tabelle(self, session_factory):
        popola_tenant(session_factory, "tenant-a")

        with session_scope(session_factory) as session:
            conteggi = TenantRepository(session, "tenant-a").delete_tenant_data()

        assert set(conteggi) == {model.__tablename__ for model in TUTTI_I_MODELLI}
        assert all(valore == 1 for valore in conteggi.values()), conteggi

    def test_tenant_vuoto_non_e_un_errore(self, session_factory):
        with session_scope(session_factory) as session:
            conteggi = TenantRepository(session, "tenant-mai-usato").delete_tenant_data()
        assert all(valore == 0 for valore in conteggi.values())

    def test_il_tenant_puo_essere_ripopolato_dopo_la_cancellazione(self, session_factory):
        """Nessun residuo che avveleni i vincoli di unicità."""
        popola_tenant(session_factory, "tenant-a")
        with session_scope(session_factory) as session:
            TenantRepository(session, "tenant-a").delete_tenant_data()

        popola_tenant(session_factory, "tenant-a")
        with session_scope(session_factory) as session:
            assert len(TenantRepository(session, "tenant-a").list(Mandante)) == 1


class TestAuditTrail:
    def test_log_event_scrive_nel_tenant_del_repository(self, session_factory):
        from irec.domain.enums import TipoEvento

        with session_scope(session_factory) as session:
            TenantRepository(session, "tenant-a").log_event(
                TipoEvento.TRANSIZIONE_STATO,
                entita="fattura",
                entita_id="fatt-1",
                stato_precedente="gestione",
                stato_successivo="saldata",
                operatore="user-123",
                correlation_id="corr-1",
            )

        with session_scope(session_factory) as session:
            eventi = TenantRepository(session, "tenant-a").list(AuditLog)
            assert len(eventi) == 1
            evento = eventi[0]
            assert evento.tenant_id == "tenant-a"
            assert evento.stato_successivo == "saldata"
            assert evento.correlation_id == "corr-1"
        with session_scope(session_factory) as session:
            assert TenantRepository(session, "tenant-b").list(AuditLog) == []
