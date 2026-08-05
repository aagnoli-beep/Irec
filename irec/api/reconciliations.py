import logging
from datetime import UTC, date, datetime
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from pydantic import BaseModel

from irec.adapters.db.models import SyncRun
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import SessionFactory, session_scope
from irec.adapters.providers import ProviderSet
from irec.api.deps import RepositoryDep
from irec.auth.context import CallContext, get_call_context
from irec.domain.enums import StatoRun
from irec.errors import AppError, log_eccezione
from irec.services.sync import CicloSincronizzazione

router = APIRouter(prefix="/v1")

logger = logging.getLogger("irec.sync")


class RunAccettata(BaseModel):
    run_id: str


class RunDettaglio(BaseModel):
    run_id: str
    status: StatoRun
    risultato: dict[str, object] | None = None
    errore: str | None = None


def _providers(request: Request) -> "ProviderSet":
    providers = getattr(request.app.state, "providers", None)
    if providers is None:
        raise AppError(503, "providers_not_configured", "external providers not configured")
    return cast("ProviderSet", providers)


@router.post("/reconciliations", status_code=202, response_model=RunAccettata)
def start_reconciliation(
    request: Request,
    background: BackgroundTasks,
    ctx: Annotated[CallContext, Depends(get_call_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunAccettata:
    """Avvia una run del ciclo di sincronizzazione → `202 {run_id}`.

    `Idempotency-Key` obbligatoria: un retry con la stessa chiave
    restituisce la stessa run, mai una seconda esecuzione (brief §4).

    La run è creata e COMMITTATA in una sessione dedicata prima di
    accodare il task: i BackgroundTasks partono prima della chiusura
    della sessione di richiesta, e il task deve trovare la riga.

    Errori: 400 missing_idempotency_key, 401/403 auth,
    503 database_not_configured / providers_not_configured.
    """
    if not idempotency_key:
        raise AppError(400, "missing_idempotency_key", "Idempotency-Key header required")

    providers = _providers(request)
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise AppError(503, "database_not_configured", "database not configured")

    with session_scope(session_factory) as session:
        repo = TenantRepository(session, ctx.tenant_id)
        for esistente in repo.list(SyncRun):
            if esistente.chiave_idempotenza == idempotency_key:
                return RunAccettata(run_id=esistente.id)
        run = repo.add(SyncRun(chiave_idempotenza=idempotency_key, avviata_da=ctx.sub))
        repo.flush()
        run_id = run.id

    background.add_task(_esegui_run, session_factory, providers, ctx.tenant_id, run_id)
    return RunAccettata(run_id=run_id)


@router.get("/reconciliations/{run_id}", response_model=RunDettaglio)
def get_reconciliation(run_id: str, repo: RepositoryDep) -> RunDettaglio:
    """Stato e risultato di una run del proprio tenant.

    Errori: 401/403 auth, 404 run_not_found (anche per run di altri
    tenant: un id altrui è indistinguibile da un id inesistente).
    """
    run = repo.get(SyncRun, run_id)
    if run is None:
        raise AppError(404, "run_not_found", "run not found")
    return RunDettaglio(
        run_id=run.id, status=run.stato, risultato=run.risultato, errore=run.errore
    )


def _esegui_run(
    session_factory: SessionFactory,
    providers: ProviderSet,
    tenant_id: str,
    run_id: str,
) -> None:
    """Esecuzione asincrona della run (BackgroundTasks).

    Sessione propria: la richiesta HTTP che l'ha accodata è già conclusa.
    Qualunque errore finisce sulla run come FAILED, mai propagato.
    """
    ciclo = CicloSincronizzazione(
        providers.fatture,
        providers.movimenti,
        providers.riconciliatore,
        oggi=date.today(),
    )
    try:
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, tenant_id)
            run = repo.get(SyncRun, run_id)
            if run is None:  # cancellazione GDPR nel frattempo
                return
            run.stato = StatoRun.RUNNING
            run.avviata_at = datetime.now(UTC)
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
