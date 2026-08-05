import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# Ri-esportata per i layer superiori (che non possono importare SQLAlchemy):
# serve a gestire i conflitti sui vincoli di unicità, es. i retry
# concorrenti sull'Idempotency-Key.
__all__ = ["IntegrityError", "SessionFactory"]

logger = logging.getLogger("irec.db")

_ENGINE_OPTIONS = {
    # Connessione verificata prima dell'uso: evita di servire errori su
    # connessioni chiuse dal database dopo un periodo di inattività.
    "pool_pre_ping": True,
    # Senza questo, i messaggi d'errore di SQLAlchemy includono lo statement
    # CON I PARAMETRI: P.IVA, email, importi e tenant finirebbero nei log a
    # ogni violazione di vincolo (evento atteso sui reimport).
    "hide_parameters": True,
}


def create_db_engine(database_url: str) -> Engine:
    """Engine del database di IREC, con i parametri esclusi dagli errori."""
    return create_engine(database_url, **_ENGINE_OPTIONS)


# Alias per i layer superiori: possono tipizzare la factory senza
# importare SQLAlchemy (vietato fuori da adapters/db dal lint TID251).
SessionFactory = sessionmaker[Session]


def create_session_factory(engine: Engine) -> SessionFactory:
    """Factory di sessioni; gli oggetti restano usabili dopo il commit."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Sessione transazionale: commit se tutto va bene, rollback su eccezione."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection(engine: Engine) -> bool:
    """Ping usato da /ready. Non propaga l'errore né la connection string al
    client, ma lascia traccia del motivo nei log del servizio."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("ping database fallito: %s", type(exc).__name__)
        return False
    return True
