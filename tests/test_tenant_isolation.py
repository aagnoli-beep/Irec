"""Isolamento multi-tenant: la garanzia cardine di IREC.

Il tenant arriva dal call-token e filtra ogni lettura, scrittura e
cancellazione. Un tenant non deve mai vedere né toccare i dati di un altro.
"""

import pytest
from sqlalchemy import select

from irec.adapters.db.models import AuditLog, ClienteFinale, Fattura, Mandante, Posizione
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.domain.enums import TipoEvento
from tests.factories import make_cliente, make_fattura, make_mandante, make_posizione


def _popola_tenant(session_factory, tenant_id: str, denominazione: str) -> str:
    """Crea mandante + cliente + posizione + fattura per un tenant."""
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, tenant_id)
        mandante = repo.add(make_mandante(ragione_sociale=denominazione))
        repo.flush()
        cliente = repo.add(make_cliente(mandante.id, denominazione=denominazione))
        repo.flush()
        posizione = repo.add(make_posizione(cliente.id))
        repo.flush()
        fattura = repo.add(make_fattura(posizione.id, cliente.id))
        repo.flush()
        return fattura.id


def test_lettura_non_vede_altri_tenant(session_factory):
    _popola_tenant(session_factory, "tenant-a", "Alfa SRL")
    _popola_tenant(session_factory, "tenant-b", "Beta SRL")

    with session_scope(session_factory) as session:
        repo_a = TenantRepository(session, "tenant-a")
        mandanti = repo_a.list(Mandante)
        assert [m.ragione_sociale for m in mandanti] == ["Alfa SRL"]
        assert len(repo_a.list(Fattura)) == 1


def test_get_per_id_di_altro_tenant_restituisce_none(session_factory):
    _popola_tenant(session_factory, "tenant-a", "Alfa SRL")
    fattura_b = _popola_tenant(session_factory, "tenant-b", "Beta SRL")

    with session_scope(session_factory) as session:
        repo_a = TenantRepository(session, "tenant-a")
        assert repo_a.get(Fattura, fattura_b) is None


def test_scrittura_forza_il_tenant_del_repository(session_factory):
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, "tenant-a")
        mandante = repo.add(make_mandante())
        repo.flush()
        assert mandante.tenant_id == "tenant-a"


def test_scrittura_su_tenant_diverso_e_rifiutata(session_factory):
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, "tenant-a")
        entita = make_mandante()
        entita.tenant_id = "tenant-b"
        with pytest.raises(ValueError, match="tenant diverso"):
            repo.add(entita)


def test_repository_senza_tenant_e_rifiutato(session_factory):
    with session_scope(session_factory) as session:
        with pytest.raises(ValueError, match="tenant_id obbligatorio"):
            TenantRepository(session, "")


def test_cancellazione_gdpr_rimuove_solo_il_proprio_tenant(session_factory):
    _popola_tenant(session_factory, "tenant-a", "Alfa SRL")
    _popola_tenant(session_factory, "tenant-b", "Beta SRL")

    with session_scope(session_factory) as session:
        TenantRepository(session, "tenant-a").cancella_tenant()

    with session_scope(session_factory) as session:
        # Il tenant cancellato non ha più nulla in nessuna tabella.
        repo_a = TenantRepository(session, "tenant-a")
        for model in (Mandante, ClienteFinale, Posizione, Fattura):
            assert repo_a.list(model) == []
        # Il tenant vicino è intatto.
        repo_b = TenantRepository(session, "tenant-b")
        assert len(repo_b.list(Mandante)) == 1
        assert len(repo_b.list(Fattura)) == 1


def test_cancellazione_gdpr_restituisce_i_conteggi(session_factory):
    _popola_tenant(session_factory, "tenant-a", "Alfa SRL")

    with session_scope(session_factory) as session:
        conteggi = TenantRepository(session, "tenant-a").cancella_tenant()

    assert conteggi["mandante"] == 1
    assert conteggi["fattura"] == 1


def test_audit_log_scritto_con_il_tenant(session_factory):
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, "tenant-a")
        repo.log_evento(
            TipoEvento.TRANSIZIONE_STATO,
            entita="fattura",
            entita_id="fatt-1",
            stato_precedente="gestione",
            stato_successivo="saldata",
            correlation_id="corr-1",
        )

    with session_scope(session_factory) as session:
        eventi = list(session.scalars(select(AuditLog)))
        assert len(eventi) == 1
        assert eventi[0].tenant_id == "tenant-a"
        assert eventi[0].stato_successivo == "saldata"
