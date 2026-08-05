import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("irec")


def log_eccezione(exc: BaseException) -> None:
    """Logga un errore non gestito senza far uscire dati del debitore.

    Gli errori SQLAlchemy portano nello stack lo statement e, a seconda
    della configurazione, i valori: di quelli si registra solo il tipo.
    """
    if isinstance(exc, SQLAlchemyError):
        logger.error("errore database: %s", type(exc).__name__)
        return
    logger.exception("unhandled error", exc_info=exc)


class AppError(Exception):
    """Errore applicativo reso come JSON {error, code} (contratto openapi.yaml)."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message, "code": exc.code},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid request", "code": "validation_error"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log_eccezione(exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal server error", "code": "internal"},
        )
