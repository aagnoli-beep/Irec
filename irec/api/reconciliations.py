from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from pydantic import BaseModel

from irec.adapters.db.models import SyncRun
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import IntegrityError, SessionFactory, session_scope
from irec.adapters.providers import ProviderSet
from irec.api.deps import RepositoryDep
from irec.auth.context import CallContext, get_call_context
from irec.domain.enums import StatoRun
from irec.errors import AppError
from irec.logging_setup import correlation_id_var
from irec.services.sync_run import esegui_run

router = APIRouter(prefix="/v1")

# Lunghezza della colonna sync_run.chiave_idempotenza: una chiave più
# lunga deve dare 400, non un errore del database.
IDEMPOTENCY_KEY_MAX_LENGTH = 128

STATI_RUN_ATTIVI = (StatoRun.QUEUED, StatoRun.RUNNING)


class RunAccettata(BaseModel):
    run_id: str


class RunDettaglio(BaseModel):
    run_id: str
    status: StatoRun
    result: dict[str, object] | None = None
    error: str | None = None


def _providers(request: Request) -> ProviderSet:
    providers = getattr(request.app.state, "providers", None)
    if providers is None:
        raise AppError(503, "providers_not_configured", "external providers not configured")
    return cast(ProviderSet, providers)


def _session_factory(request: Request) -> SessionFactory:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise AppError(503, "database_not_configured", "database not configured")
    return cast(SessionFactory, session_factory)


@router.post("/reconciliations", status_code=202, response_model=RunAccettata)
def start_reconciliation(
    request: Request,
    background: BackgroundTasks,
    ctx: Annotated[CallContext, Depends(get_call_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunAccettata:
    """Avvia una run del ciclo di sincronizzazione → `202 {run_id}`.

    `Idempotency-Key` obbligatoria (max 128 caratteri): un retry con la
    stessa chiave restituisce la stessa run, mai una seconda esecuzione
    (brief §4) — anche in caso di richieste concorrenti (il vincolo di
    unicità è il punto di verità, l'IntegrityError viene risolto
    rileggendo la run vincente).

    Una sola run attiva per tenant: con una run QUEUED/RUNNING in corso
    e una chiave nuova la risposta è `409 run_in_progress` — il ciclo è
    costoso e due run sovrapposte si contenderebbero gli stessi dati.

    La run è creata e COMMITTATA in una sessione dedicata prima di
    accodare il task: i BackgroundTasks partono prima della chiusura
    della sessione di richiesta, e il task deve trovare la riga.

    Errori: 400 missing_idempotency_key / invalid_idempotency_key,
    401/403 auth, 409 run_in_progress,
    503 database_not_configured / providers_not_configured.
    """
    if not idempotency_key:
        raise AppError(400, "missing_idempotency_key", "Idempotency-Key header required")
    if len(idempotency_key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise AppError(
            400,
            "invalid_idempotency_key",
            f"Idempotency-Key oltre {IDEMPOTENCY_KEY_MAX_LENGTH} caratteri",
        )

    providers = _providers(request)
    session_factory = _session_factory(request)
    correlation_id = correlation_id_var.get()

    try:
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, ctx.tenant_id)
            esistente = repo.find(
                SyncRun, SyncRun.chiave_idempotenza == idempotency_key
            )
            if esistente:
                return RunAccettata(run_id=esistente[0].id)
            attive = repo.find(SyncRun, SyncRun.stato.in_(STATI_RUN_ATTIVI))
            if attive:
                raise AppError(
                    409,
                    "run_in_progress",
                    "una run di sincronizzazione è già in corso per questo tenant",
                )
            run = repo.add(
                SyncRun(chiave_idempotenza=idempotency_key, avviata_da=ctx.sub)
            )
            repo.flush()
            run_id = run.id
    except IntegrityError:
        # Race con un retry concorrente: ha vinto l'altro. Il retry deve
        # ricevere la stessa run, non un 500.
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, ctx.tenant_id)
            vincente = repo.find(SyncRun, SyncRun.chiave_idempotenza == idempotency_key)
            if not vincente:  # pragma: no cover - conflitto su altro vincolo
                raise AppError(500, "internal", "internal server error") from None
            return RunAccettata(run_id=vincente[0].id)

    background.add_task(
        esegui_run, session_factory, providers, ctx.tenant_id, run_id, correlation_id
    )
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
        run_id=run.id, status=run.stato, result=run.risultato, error=run.errore
    )
