from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from irec.adapters.db.session import check_connection

router = APIRouter()


@router.get("/health")
def health() -> dict:
    """Liveness: il processo è vivo. Sempre 200."""
    return {"status": "ok"}


def _motivo_non_pronto(state) -> str | None:
    """Prima dipendenza non pronta, o None se il servizio può servire traffico."""
    if getattr(state, "verifier", None) is None:
        return "auth_not_configured"
    engine = getattr(state, "engine", None)
    if engine is None:
        # Senza database le rotte dati rispondono 503: dichiararsi pronti
        # farebbe arrivare traffico a un servizio che non può servirlo.
        return "database_not_configured"
    if not check_connection(engine):
        return "database_unreachable"
    return None


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: 200 se il servizio può servire traffico, 503 altrimenti.

    Verifica che il verifier dei call-token sia configurato, che il
    database sia configurato e che risponda. Il motivo del 503 non
    espone la connection string.
    """
    motivo = _motivo_non_pronto(request.app.state)
    if motivo is not None:
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "reason": motivo}
        )
    return JSONResponse(status_code=200, content={"status": "ready"})
