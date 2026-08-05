"""Test su JWKS remoto, contextvar del tenant, error path 500 e readiness
(review M0, finding H1–H3 e security-M2/M3)."""

import json
import logging

import httpx
import pytest

from irec.auth.verifier import AuthError, CallTokenVerifier
from irec.config import Settings
from irec.logging_setup import JsonFormatter, tenant_id_var
from irec.main import create_app

# --- jwks_url: enforcement https e indisponibilità ---


def test_jwks_url_http_rejected():
    with pytest.raises(ValueError):
        CallTokenVerifier(jwks_url="http://mind.example.com/jwks")


def test_jwks_url_localhost_http_allowed():
    CallTokenVerifier(jwks_url="http://localhost:8080/jwks")


def test_jwks_unavailable_returns_503(client, app, make_token, monkeypatch):
    """JWKS irraggiungibile → 503 jwks_unavailable con correlation-id, non 500."""

    def _unreachable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", _unreachable)
    app.state.verifier = CallTokenVerifier(jwks_url="https://mind.example.com/jwks")

    response = client.get(
        "/v1/_whoami",
        headers={"Authorization": f"Bearer {make_token()}", "x-correlation-id": "corr-jwks"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "jwks_unavailable"
    assert response.headers["x-correlation-id"] == "corr-jwks"


def test_jwks_fetch_is_cached(jwks, make_token, monkeypatch):
    """Due verify entro il TTL → una sola chiamata HTTP al JWKS."""
    calls = []

    def _fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json=jwks, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _fake_get)
    verifier = CallTokenVerifier(jwks_url="https://mind.example.com/jwks")
    verifier.verify(make_token())
    verifier.verify(make_token())
    assert len(calls) == 1


def test_jwks_error_maps_to_auth_error(make_token, monkeypatch):
    def _unreachable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", _unreachable)
    verifier = CallTokenVerifier(jwks_url="https://mind.example.com/jwks")
    with pytest.raises(AuthError) as exc_info:
        verifier.verify(make_token())
    assert exc_info.value.code == "jwks_unavailable"


# --- tenant nel contextvar (log) ---


def test_tenant_contextvar_reaches_endpoint(client, make_token):
    """Il set del tenant nella dependency async arriva al contesto dell'endpoint."""
    response = client.get(
        "/v1/_tenant_ctx", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert response.status_code == 200
    assert response.json()["tenant_in_ctx"] == "tenant-abc"


def test_tenant_contextvar_no_bleed_between_requests(client, make_token):
    """Una richiesta anonima dopo una autenticata non eredita il tenant."""
    client.get("/v1/_tenant_ctx", headers={"Authorization": f"Bearer {make_token()}"})
    response = client.get("/v1/_tenant_probe")
    assert response.json()["tenant_in_ctx"] is None


def test_json_formatter_truncates_tenant():
    """Garanzia no-PII: il tenant nei log è troncato a 8 caratteri."""
    token = tenant_id_var.set("tenant-abcdefghij")
    try:
        record = logging.LogRecord("irec", logging.INFO, __file__, 1, "hello", None, None)
        payload = json.loads(JsonFormatter().format(record))
    finally:
        tenant_id_var.reset(token)
    assert payload["tenant"] == "tenant-a"
    assert "tenant-abcdefghij" not in json.dumps(payload)


# --- error path 500 ---


def test_unhandled_error_shape_and_correlation(client, caplog):
    """500 nel formato {error, code}, con correlation-id e con eccezione loggata."""
    with caplog.at_level(logging.ERROR, logger="irec"):
        response = client.get("/v1/_boom", headers={"x-correlation-id": "corr-boom"})
    assert response.status_code == 500
    assert response.json() == {"error": "internal server error", "code": "internal"}
    assert response.headers["x-correlation-id"] == "corr-boom"
    assert any(record.exc_info for record in caplog.records)


def test_correlation_id_on_401(client):
    response = client.get("/v1/_whoami", headers={"x-correlation-id": "corr-err"})
    assert response.status_code == 401
    assert response.headers["x-correlation-id"] == "corr-err"


# --- readiness e startup ---


def test_ready_503_without_verifier():
    application = create_app(Settings(jwks_url=None, database_url=None))
    from fastapi.testclient import TestClient

    with TestClient(application) as bare_client:
        response = bare_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["reason"] == "auth_not_configured"


def test_production_requires_jwks_url():
    with pytest.raises(RuntimeError):
        create_app(Settings(environment="production", jwks_url=None))
