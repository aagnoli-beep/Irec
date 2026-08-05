import base64
import os
import time
import uuid
from typing import Annotated

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool, StaticPool

from irec.adapters.db.models import Base
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import create_session_factory, session_scope
from irec.auth.context import CallContext, get_call_context
from irec.auth.verifier import CallTokenVerifier
from irec.config import Settings
from irec.logging_setup import tenant_id_var
from irec.main import create_app

KID = "test-key-1"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwks(rsa_key) -> dict:
    public_numbers = rsa_key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "use": "sig",
                "alg": "RS256",
                "n": _b64url_uint(public_numbers.n),
                "e": _b64url_uint(public_numbers.e),
            }
        ]
    }


@pytest.fixture
def make_token(rsa_key):
    """Conia un call-token come farebbe Mind. Override dei claim via kwargs."""

    def _make(**overrides) -> str:
        claims = {
            "sub": "user-123",
            "tenant_id": "tenant-abc",
            "entitlement": "irec:pro",
            "scope": "irec.read irec.write irec.tenant.delete",
            "aud": "irec",
            "jti": uuid.uuid4().hex,
            "exp": int(time.time()) + 120,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": KID})

    return _make


def _abilita_foreign_key_sqlite(engine) -> None:
    """SQLite ignora le FK se non abilitate, e va fatto su OGNI connessione.

    Con un listener invece che una volta sola: altrimenti il giorno che si
    cambia il pool i test sulle FK smettono di provare qualcosa senza fallire.
    """

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture(params=["sqlite", "postgres"])
def db_engine(request):
    """Database di test reale, non un mock della sessione.

    Ogni test gira su entrambi i motori: SQLite per la velocità, Postgres
    (quello di produzione) perché SQLite non applica le larghezze delle
    colonne e tratta diversamente alcuni vincoli. Se Postgres non è
    disponibile in locale il parametro viene saltato, ma in CI c'è sempre.
    """
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        _abilita_foreign_key_sqlite(engine)
    else:
        url = os.environ.get("IREC_TEST_DATABASE_URL")
        if not url:
            pytest.skip("IREC_TEST_DATABASE_URL non configurata")
        engine = create_engine(url, poolclass=NullPool)
        try:
            with engine.connect():
                pass
        except OperationalError:
            engine.dispose()
            pytest.skip("Postgres di test non raggiungibile")
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    return create_session_factory(db_engine)


@pytest.fixture
def repo(session_factory):
    """Repository sul tenant di default dei token di test."""
    with session_scope(session_factory) as session:
        yield TenantRepository(session, "tenant-abc")


@pytest.fixture
def app(jwks, db_engine, session_factory):
    application = create_app(Settings(jwks_url=None, database_url=None))
    application.state.verifier = CallTokenVerifier(static_jwks=jwks, audience="irec")
    application.state.engine = db_engine
    application.state.session_factory = session_factory

    # Rotta protetta di prova: espone il CallContext per verificare l'auth end-to-end.
    @application.get("/v1/_whoami")
    def whoami(ctx: Annotated[CallContext, Depends(get_call_context)]) -> dict:
        return {"sub": ctx.sub, "tenant_id": ctx.tenant_id, "entitlement": ctx.entitlement}

    # Rotta protetta che legge il contextvar del tenant: verifica che il set
    # fatto nella dependency async si propaghi al contesto dell'endpoint (log).
    @application.get("/v1/_tenant_ctx")
    def tenant_ctx(ctx: Annotated[CallContext, Depends(get_call_context)]) -> dict:
        return {"tenant_in_ctx": tenant_id_var.get()}

    # Rotta NON protetta che legge il contextvar: verifica l'assenza di bleed
    # tra richieste.
    @application.get("/v1/_tenant_probe")
    def tenant_probe() -> dict:
        return {"tenant_in_ctx": tenant_id_var.get()}

    # Rotta che esplode: verifica il 500 {error, code} con correlation-id.
    @application.get("/v1/_boom")
    def boom() -> dict:
        raise RuntimeError("boom")

    return application


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)
