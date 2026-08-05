from fastapi import FastAPI

from irec import __version__
from irec.api.health import router as health_router
from irec.auth.verifier import CallTokenVerifier
from irec.config import Settings, get_settings
from irec.errors import register_error_handlers
from irec.logging_setup import setup_logging
from irec.middleware import CorrelationIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(title="IREC", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)

    if settings.environment == "production" and not settings.jwks_url:
        # Fail-fast: mai un deploy di produzione "vivo" con auth non operativa.
        raise RuntimeError("IREC_JWKS_URL è obbligatoria con IREC_ENVIRONMENT=production")

    if settings.jwks_url:
        app.state.verifier = CallTokenVerifier(
            jwks_url=settings.jwks_url,
            audience=settings.token_audience,
        )

    app.include_router(health_router)
    return app


app = create_app()
