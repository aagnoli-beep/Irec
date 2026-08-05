from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Liveness: il processo è vivo. Sempre 200."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: 200 se il servizio può servire traffico, 503 altrimenti.

    Verifica che il verifier dei call-token sia configurato; da M1
    verificherà anche la raggiungibilità di Postgres (docs/ROADMAP.md).
    """
    if getattr(request.app.state, "verifier", None) is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "auth_not_configured"},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})
