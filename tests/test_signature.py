"""Test sulla verifica di FIRMA del call-token (review M0, finding B1).

Sono la garanzia di non-regressione sul guardrail auth: una modifica che
disabilitasse la verifica (verify_signature=False, allowlist algoritmi
allentata) deve far fallire questi test.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import KID


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _claims() -> dict:
    return {
        "sub": "user-123",
        "tenant_id": "tenant-abc",
        "entitlement": "irec:pro",
        "aud": "irec",
        "jti": uuid.uuid4().hex,
        "exp": int(time.time()) + 120,
    }


def test_token_signed_with_different_key_same_kid(client):
    """Token forgiato con un'ALTRA chiave RSA ma stesso kid → 401."""
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), attacker_key, algorithm="RS256", headers={"kid": KID})
    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_hs256_algorithm_confusion(client, rsa_key):
    """Token HS256 firmato usando la chiave PUBBLICA come segreto HMAC → 401.

    Attacco classico di algorithm-confusion: costruito a mano perché PyJWT
    lato client rifiuta di crearlo.
    """
    public_pem = rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    payload = _b64url(json.dumps(_claims()).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    token = f"{header}.{payload}.{signature}"

    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_alg_none(client):
    """Token con alg=none e firma vuota → 401."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT", "kid": KID}).encode())
    payload = _b64url(json.dumps(_claims()).encode())
    token = f"{header}.{payload}."

    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_unknown_kid(client, rsa_key):
    """Token firmato con la chiave giusta ma kid non nel JWKS → 401 unknown_key."""
    token = jwt.encode(_claims(), rsa_key, algorithm="RS256", headers={"kid": "altro-kid"})
    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "unknown_key"


@pytest.mark.parametrize("scheme", ["Basic dXNlcjpwYXNz", "Token abc"])
def test_non_bearer_scheme(client, scheme):
    response = client.get("/v1/_whoami", headers={"Authorization": scheme})
    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"
