from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Connessione verificata prima dell'uso: evita di servire errori su
# connessioni chiuse dal database dopo un periodo di inattività.
_ENGINE_OPTIONS = {"pool_pre_ping": True}


def create_db_engine(database_url: str) -> Engine:
    return create_engine(database_url, **_ENGINE_OPTIONS)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
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
    """Ping usato da /ready. Non propaga l'errore né la connection string."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
