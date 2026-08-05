from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from irec.adapters.db.session import check_connection

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Liveness: il processo è vivo. Sempre 200."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: 200 se il servizio può servire traffico, 503 altrimenti.

    Verifica che il verifier dei call-token sia configurato e che il
    database risponda. Il motivo del 503 non espone la connection string.
    """
    if getattr(request.app.state, "verifier", None) is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "auth_not_configured"},
        )
    engine = getattr(request.app.state, "engine", None)
    if engine is not None and not check_connection(engine):
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "database_unreachable"},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})
