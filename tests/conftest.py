import base64
import time
import uuid
from typing import Annotated

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient

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
            "scope": "irec.read irec.write",
            "aud": "irec",
            "jti": uuid.uuid4().hex,
            "exp": int(time.time()) + 120,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": KID})

    return _make


@pytest.fixture
def app(jwks):
    application = create_app(Settings(jwks_url=None, database_url=None))
    application.state.verifier = CallTokenVerifier(static_jwks=jwks, audience="irec")

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
