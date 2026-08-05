import logging

from fastapi import FastAPI

from irec import __version__
from irec.adapters.db.rls import connection_bypasses_rls
from irec.adapters.db.session import create_db_engine, create_session_factory
from irec.adapters.providers import build_providers
from irec.api.azioni import router as azioni_router
from irec.api.health import router as health_router
from irec.api.letture import router as letture_router
from irec.api.proattivo import router as proattivo_router
from irec.api.reconciliations import router as reconciliations_router
from irec.api.tenant import router as tenant_router
from irec.auth.verifier import CallTokenVerifier
from irec.config import Settings, get_settings
from irec.errors import register_error_handlers
from irec.logging_setup import setup_logging
from irec.middleware import CorrelationIdMiddleware


def _verify_rls_role(app: FastAPI, settings: Settings) -> None:
    """La RLS non si applica ai superuser: un deploy con il ruolo sbagliato
    annullerebbe la quarta rete di isolamento senza alcun sintomo.

    In production: fail-fast (anche su database irraggiungibile allo
    startup — l'orchestratore riavvierà). In sviluppo: solo warning.
    """
    if settings.environment == "production":
        if connection_bypasses_rls(app.state.engine):
            raise RuntimeError(
                "il ruolo database è superuser/BYPASSRLS: la Row Level "
                "Security sarebbe inerte. Usare un ruolo non privilegiato."
            )
        return
    try:
        if connection_bypasses_rls(app.state.engine):
            logging.getLogger("irec").warning(
                "ruolo database superuser/BYPASSRLS: RLS inerte (ok solo in sviluppo)"
            )
    except Exception:
        # Database non raggiungibile allo startup: in sviluppo non blocca,
        # lo segnalerà /ready.
        logging.getLogger("irec").warning("check ruolo RLS non eseguibile allo startup")


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
        _verify_rls_role(app, settings)

    # Fail-fast qui dentro se production seleziona i mock.
    app.state.providers = build_providers(settings)

    app.include_router(health_router)
    app.include_router(tenant_router)
    app.include_router(reconciliations_router)
    app.include_router(letture_router)
    app.include_router(azioni_router)
    app.include_router(proattivo_router)
    return app


app = create_app()
