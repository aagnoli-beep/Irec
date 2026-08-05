import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from irec.logging_setup import correlation_id_var

CORRELATION_HEADER = "x-correlation-id"

logger = logging.getLogger("irec")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Riceve x-correlation-id da Mind (o ne genera uno) e lo ri-emette in log e risposta.

    Cattura anche le eccezioni non gestite: senza questo catch il 500 verrebbe
    reso dal ServerErrorMiddleware più esterno, fuori da questo middleware,
    e la risposta perderebbe l'header di correlazione.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or uuid.uuid4().hex
        token = correlation_id_var.set(correlation_id)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error")
            response = JSONResponse(
                status_code=500,
                content={"error": "internal server error", "code": "internal"},
            )
        finally:
            correlation_id_var.reset(token)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
