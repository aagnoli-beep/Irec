import logging
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from irec.errors import log_eccezione
from irec.logging_setup import correlation_id_var

CORRELATION_HEADER = "x-correlation-id"

# L'header arriva anche da richieste non autenticate e finisce nei log:
# vincolato a un formato innocuo e a una lunghezza massima.
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

logger = logging.getLogger("irec")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Riceve x-correlation-id da Mind (o ne genera uno) e lo ri-emette in log e risposta.

    Cattura anche le eccezioni non gestite: senza questo catch il 500 verrebbe
    reso dal ServerErrorMiddleware più esterno, fuori da questo middleware,
    e la risposta perderebbe l'header di correlazione.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        ricevuto = request.headers.get(CORRELATION_HEADER)
        correlation_id = (
            ricevuto if ricevuto and _CORRELATION_PATTERN.match(ricevuto) else uuid.uuid4().hex
        )
        token = correlation_id_var.set(correlation_id)
        try:
            response = await call_next(request)
        except Exception as exc:
            log_eccezione(exc)
            response = JSONResponse(
                status_code=500,
                content={"error": "internal server error", "code": "internal"},
            )
        finally:
            correlation_id_var.reset(token)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
