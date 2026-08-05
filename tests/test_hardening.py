"""Item di hardening del sub-piano R1 (review M0)."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from irec.auth.verifier import AuthError, CallTokenVerifier
from irec.errors import register_error_handlers
from irec.middleware import CorrelationIdMiddleware


class TestValidazioneCorrelationId:
    @pytest.mark.parametrize(
        "valore",
        [
            "a" * 65,  # troppo lungo
            "corr id",  # spazio
            "corr\nid",  # newline
            "<script>",  # caratteri non ammessi
        ],
    )
    def test_correlation_id_malformato_viene_sostituito(self, client, valore):
        response = client.get("/health", headers={"x-correlation-id": valore})
        assert response.headers["x-correlation-id"] != valore
        assert len(response.headers["x-correlation-id"]) == 32

    @pytest.mark.parametrize("valore", ["corr-42", "a" * 64, "run_1.2-3"])
    def test_correlation_id_valido_viene_mantenuto(self, client, valore):
        response = client.get("/health", headers={"x-correlation-id": valore})
        assert response.headers["x-correlation-id"] == valore


class TestRefreshJwksSuRotazione:
    def test_kid_sconosciuto_forza_un_refresh(self, jwks, rsa_key, make_token, monkeypatch):
        """Alla rotazione delle chiavi di Mind non si resta in 401 fino al TTL."""
        risposte = [{"keys": []}, jwks]
        chiamate = []

        def _fake_get(url, **kwargs):
            chiamate.append(url)
            return httpx.Response(
                200, json=risposte[min(len(chiamate) - 1, 1)], request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(httpx, "get", _fake_get)
        verifier = CallTokenVerifier(jwks_url="https://mind.example.com/jwks")

        claims = verifier.verify(make_token())

        assert claims["tenant_id"] == "tenant-abc"
        assert len(chiamate) == 2  # cache vuota + refresh forzato sul kid mancante

    def test_refresh_forzato_e_rate_limitato(self, make_token, monkeypatch):
        """Kid casuali non devono amplificare le richieste verso Mind."""
        chiamate = []

        def _fake_get(url, **kwargs):
            chiamate.append(url)
            return httpx.Response(200, json={"keys": []}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", _fake_get)
        verifier = CallTokenVerifier(jwks_url="https://mind.example.com/jwks")

        for _ in range(5):
            with pytest.raises(AuthError, match="no matching key"):
                verifier.verify(make_token())

        # Prima verifica: fetch iniziale + un refresh forzato. Le successive
        # riusano la cache senza rinfrescare (finestra di rate-limit).
        assert len(chiamate) == 2


class TestErroriValidazione:
    def test_parametro_non_valido_risponde_400(self):
        """Il contratto prevede 400 {error, code}, non il 422 di default."""
        app = FastAPI()
        app.add_middleware(CorrelationIdMiddleware)
        register_error_handlers(app)

        @app.get("/eco")
        def eco(n: int) -> dict:
            return {"n": n}

        with TestClient(app) as client:
            response = client.get("/eco?n=non-un-numero")

        assert response.status_code == 400
        assert response.json() == {"error": "invalid request", "code": "validation_error"}
