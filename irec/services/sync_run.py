"""Ciclo di vita di una run di sincronizzazione (QUEUED → RUNNING → esito).

Vive nel layer services: è coordinamento e persistenza, non routing.
L'API si limita a validare la richiesta, creare la run e accodare
`esegui_run` nei BackgroundTasks.
"""

import logging
from datetime import UTC, datetime

from irec.adapters.db.models import SyncRun
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import SessionFactory, session_scope
from irec.adapters.providers import ProviderSet
from irec.domain.enums import StatoRun
from irec.errors import log_eccezione
from irec.logging_setup import correlation_id_var, tenant_id_var
from irec.services.sync import CicloSincronizzazione

logger = logging.getLogger("irec.sync")


def esegui_run(
    session_factory: SessionFactory,
    providers: ProviderSet,
    tenant_id: str,
    run_id: str,
    correlation_id: str | None,
) -> None:
    """Esecuzione asincrona della run (BackgroundTasks).

    Sessioni proprie: la richiesta HTTP che l'ha accodata è già conclusa
    e il middleware ha già resettato i contextvar — correlation id e
    tenant vengono re-impostati qui, così i log della run restano
    correlabili alla chiamata di Mind che l'ha originata.

    Qualunque errore finisce sulla run come FAILED, mai propagato.
    RUNNING è committato in una transazione separata: il poll lo osserva.
    """
    token_correlation = correlation_id_var.set(correlation_id)
    token_tenant = tenant_id_var.set(tenant_id)

    ciclo = CicloSincronizzazione(
        providers.fatture,
        providers.movimenti,
        providers.riconciliatore,
        oggi=datetime.now(UTC).date(),
    )
    try:
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, tenant_id)
            run = repo.get(SyncRun, run_id)
            if run is None:  # cancellazione GDPR nel frattempo
                return
            run.stato = StatoRun.RUNNING
            run.avviata_at = datetime.now(UTC)

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, tenant_id)
            run = repo.get(SyncRun, run_id)
            if run is None:  # cancellazione GDPR a run avviata
                return
            esito = ciclo.esegui(repo)
            run.risultato = esito.as_dict()
            run.stato = StatoRun.COMPLETED
            run.conclusa_at = datetime.now(UTC)
    except Exception as exc:
        log_eccezione(exc)
        try:
            with session_scope(session_factory) as session:
                repo = TenantRepository(session, tenant_id)
                run = repo.get(SyncRun, run_id)
                if run is not None:
                    run.stato = StatoRun.FAILED
                    run.errore = type(exc).__name__
                    run.conclusa_at = datetime.now(UTC)
        except Exception as errore_salvataggio:
            log_eccezione(errore_salvataggio)
    finally:
        # Ripristino del contesto: chi ci invoca direttamente (test, futuri
        # scheduler) non deve ereditare correlation/tenant di questa run.
        correlation_id_var.reset(token_correlation)
        tenant_id_var.reset(token_tenant)
