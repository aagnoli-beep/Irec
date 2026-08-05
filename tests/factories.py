"""Costruttori di entità per i test. Nessuna logica: solo dati coerenti."""

from datetime import UTC, date, datetime
from decimal import Decimal

from irec.adapters.db.models import (
    AuditLog,
    ClienteFinale,
    Comunicazione,
    Fattura,
    Flusso,
    FlussoStep,
    Mandante,
    Notifica,
    Pagamento,
    Posizione,
    SyncRun,
)
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.domain.enums import (
    Canale,
    OriginePagamento,
    Pacchetto,
    StatoFattura,
    TipoEvento,
)


def make_mandante(**overrides) -> Mandante:
    valori = {
        "ragione_sociale": "Acme SRL",
        "partita_iva": "01234567890",
        "pacchetto": Pacchetto.VALUE,
        "alias_email": "acme@incassi-intelligenti.it",
    }
    valori.update(overrides)
    return Mandante(**valori)


def make_cliente(mandante_id: str, **overrides) -> ClienteFinale:
    valori = {
        "mandante_id": mandante_id,
        "denominazione": "Globex Corp",
        "piva_cf": "09876543210",
        "email": "amministrazione@globex.example",
    }
    valori.update(overrides)
    return ClienteFinale(**valori)


def make_posizione(cliente_id: str, **overrides) -> Posizione:
    valori = {"cliente_id": cliente_id}
    valori.update(overrides)
    return Posizione(**valori)


def make_fattura(posizione_id: str, cliente_id: str, **overrides) -> Fattura:
    valori = {
        "posizione_id": posizione_id,
        "cliente_id": cliente_id,
        "numero": "32-FA",
        "data_emissione": date(2026, 7, 1),
        "data_scadenza": date(2026, 8, 31),
        "importo": Decimal("1220.00"),
        "importo_residuo": Decimal("1220.00"),
        "stato": StatoFattura.GESTIONE,
    }
    valori.update(overrides)
    return Fattura(**valori)


def make_flusso(mandante_id: str, **overrides) -> Flusso:
    valori = {"mandante_id": mandante_id, "nome": "Flusso standard"}
    valori.update(overrides)
    return Flusso(**valori)


def make_step(flusso_id: str, **overrides) -> FlussoStep:
    valori = {
        "flusso_id": flusso_id,
        "ordine": 1,
        "offset_giorni": -2,
        "canale": Canale.EMAIL,
        "template": "promemoria",
    }
    valori.update(overrides)
    return FlussoStep(**valori)


def make_comunicazione(fattura_id: str, **overrides) -> Comunicazione:
    valori = {
        "fattura_id": fattura_id,
        "canale": Canale.EMAIL,
        "template": "promemoria",
        "programmata_per": datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
    }
    valori.update(overrides)
    return Comunicazione(**valori)


def make_pagamento(fattura_id: str, **overrides) -> Pagamento:
    valori = {
        "fattura_id": fattura_id,
        "importo": Decimal("500.00"),
        "data_pagamento": date(2026, 9, 1),
        "origine": OriginePagamento.RICONCILIAZIONE,
        "chiave_idempotenza": "mov-0001",
    }
    valori.update(overrides)
    return Pagamento(**valori)


def make_notifica(**overrides) -> Notifica:
    from irec.domain.enums import TipoNotifica

    valori = {
        "tipo": TipoNotifica.ESCALATION_IMMINENTE,
        "riferimento": "fatt-1",
        "chiave": "escalation_imminente:fatt-1",
    }
    valori.update(overrides)
    return Notifica(**valori)


def make_sync_run(**overrides) -> SyncRun:
    valori = {"chiave_idempotenza": "run-0001"}
    valori.update(overrides)
    return SyncRun(**valori)


def make_audit(**overrides) -> AuditLog:
    valori = {
        "tipo": TipoEvento.TRANSIZIONE_STATO,
        "entita": "fattura",
        "entita_id": "fatt-1",
        "stato_precedente": "gestione",
        "stato_successivo": "saldata",
    }
    valori.update(overrides)
    return AuditLog(**valori)


def popola_tenant(session_factory, tenant_id: str, **overrides) -> dict[str, str]:
    """Crea una riga per ognuna delle 9 entità del tenant.

    Restituisce gli id, così i test possono verificare l'isolamento e la
    cancellazione GDPR su tutto lo schema, non solo sulle radici.
    """
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, tenant_id)
        mandante = repo.add(make_mandante(**overrides.get("mandante", {})))
        repo.flush()
        cliente = repo.add(make_cliente(mandante.id, **overrides.get("cliente", {})))
        repo.flush()
        posizione = repo.add(make_posizione(cliente.id))
        repo.flush()
        fattura = repo.add(
            make_fattura(posizione.id, cliente.id, **overrides.get("fattura", {}))
        )
        flusso = repo.add(make_flusso(mandante.id))
        repo.flush()
        step = repo.add(make_step(flusso.id))
        repo.flush()
        comunicazione = repo.add(make_comunicazione(fattura.id, step_id=step.id))
        pagamento = repo.add(make_pagamento(fattura.id))
        audit = repo.add(make_audit(entita_id=fattura.id))
        run = repo.add(make_sync_run())
        notifica = repo.add(make_notifica(riferimento=fattura.id))
        repo.flush()
        return {
            "mandante": mandante.id,
            "cliente": cliente.id,
            "posizione": posizione.id,
            "fattura": fattura.id,
            "flusso": flusso.id,
            "step": step.id,
            "comunicazione": comunicazione.id,
            "pagamento": pagamento.id,
            "audit": audit.id,
            "run": run.id,
            "notifica": notifica.id,
        }
