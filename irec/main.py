from fastapi import FastAPI

from irec import __version__
from irec.adapters.db.session import create_db_engine, create_session_factory
from irec.api.health import router as health_router
from irec.api.tenant import router as tenant_router
from irec.auth.verifier import CallTokenVerifier
from irec.config import Settings, get_settings
from irec.errors import register_error_handlers
from irec.logging_setup import setup_logging
from irec.middleware import CorrelationIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Costruisce l'app FastAPI.

    In `production` l'assenza di `IREC_JWKS_URL` o `IREC_DATABASE_URL`
    blocca lo startup: mai un deploy vivo con auth o dati non operativi.
    Fuori da `production` i due componenti sono opzionali e le rotte che
    li richiedono rispondono 503.
    """
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    if settings.environment == "production":
        if not settings.jwks_url:
            raise RuntimeError("IREC_JWKS_URL è obbligatoria con IREC_ENVIRONMENT=production")
        if not settings.database_url:
            raise RuntimeError(
                "IREC_DATABASE_URL è obbligatoria con IREC_ENVIRONMENT=production"
            )

    app = FastAPI(title="IREC", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)

    if settings.jwks_url:
        app.state.verifier = CallTokenVerifier(
            jwks_url=settings.jwks_url,
            audience=settings.token_audience,
        )

    if settings.database_url:
        app.state.engine = create_db_engine(settings.database_url)
        app.state.session_factory = create_session_factory(app.state.engine)

    app.include_router(health_router)
    app.include_router(tenant_router)
    return app


app = create_app()
