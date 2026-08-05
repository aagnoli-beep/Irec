import time
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import jwt

Jwks = dict[str, Any]
Claims = dict[str, Any]

# Solo firma asimmetrica: IREC verifica a chiave pubblica e non deve mai
# detenere un segreto capace di forgiare token (niente HS*).
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]

JWKS_CACHE_TTL_SECONDS = 300
JWKS_FETCH_TIMEOUT_SECONDS = 5.0
# Un kid sconosciuto può forzare un refresh, ma non più spesso di così:
# altrimenti token con kid casuali diventerebbero un amplificatore di
# richieste verso l'endpoint JWKS di Mind.
JWKS_FORCED_REFRESH_MIN_INTERVAL_SECONDS = 60.0

# Il JWKS è l'ancora di fiducia dell'intera auth: su HTTP un MITM potrebbe
# servire chiavi proprie e forgiare token validi per qualunque tenant.
_PLAIN_HTTP_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}


class AuthError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class CallTokenVerifier:
    """Verifica i call-token coniati da Mind contro il JWKS pubblico.

    Controlli: firma (solo algoritmi asimmetrici), `aud == "irec"`, `exp`,
    presenza di `sub` e `tenant_id`. Il claim `entitlement` è verificato a
    valle da `get_call_context` (irec/auth/context.py): l'entitlement lo
    decide Mind e viaggia nel claim — mai dai gruppi del realm Keycloak.
    """

    def __init__(
        self,
        jwks_url: str | None = None,
        static_jwks: Jwks | None = None,
        audience: str = "irec",
    ):
        if jwks_url is None and static_jwks is None:
            raise ValueError("serve jwks_url oppure static_jwks")
        if jwks_url is not None:
            parsed = urlparse(jwks_url)
            if parsed.scheme != "https" and parsed.hostname not in _PLAIN_HTTP_ALLOWED_HOSTS:
                raise ValueError("jwks_url deve essere https:// (http solo per localhost)")
        self._jwks_url = jwks_url
        self._static_jwks = static_jwks
        self._audience = audience
        self._cached_jwks: Jwks | None = None
        self._cached_at: float = 0.0
        self._last_forced_refresh: float = 0.0

    def _fetch_jwks(self) -> Jwks:
        # Il costruttore garantisce jwks_url quando static_jwks è assente.
        assert self._jwks_url is not None
        try:
            response = httpx.get(self._jwks_url, timeout=JWKS_FETCH_TIMEOUT_SECONDS)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthError("jwks_unavailable", "cannot fetch JWKS") from exc
        self._cached_jwks = cast(Jwks, response.json())
        self._cached_at = time.monotonic()
        return self._cached_jwks

    def _jwks(self, force_refresh: bool = False) -> Jwks:
        if self._static_jwks is not None:
            return self._static_jwks
        scaduta = time.monotonic() - self._cached_at > JWKS_CACHE_TTL_SECONDS
        if self._cached_jwks is None or scaduta or force_refresh:
            return self._fetch_jwks()
        return self._cached_jwks

    def _find_key(
        self, jwks: Jwks, kid: str | None
    ) -> "jwt.algorithms.AllowedPublicKeys | None":
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return cast(
                    "jwt.algorithms.AllowedPublicKeys", jwt.PyJWK.from_dict(key).key
                )
        return None

    def _signing_key(self, token: str) -> "jwt.algorithms.AllowedPublicKeys":
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthError("invalid_token", "malformed token") from exc
        kid = header.get("kid")

        key = self._find_key(self._jwks(), kid)
        if key is not None:
            return key

        # Kid sconosciuto con cache ancora valida: può essere una rotazione
        # delle chiavi di Mind. Un solo refresh forzato, rate-limitato, prima
        # di rifiutare — altrimenti si resterebbe in 401 fino a scadenza cache.
        if self._static_jwks is None and self._can_force_refresh():
            self._last_forced_refresh = time.monotonic()
            key = self._find_key(self._jwks(force_refresh=True), kid)
            if key is not None:
                return key
        raise AuthError("unknown_key", "no matching key in JWKS")

    def _can_force_refresh(self) -> bool:
        return (
            time.monotonic() - self._last_forced_refresh
            > JWKS_FORCED_REFRESH_MIN_INTERVAL_SECONDS
        )

    def verify(self, token: str) -> Claims:
        key = self._signing_key(token)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self._audience,
                options={"require": ["exp", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token_expired", "token expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("invalid_audience", "audience mismatch") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("invalid_token", "token verification failed") from exc

        if not claims.get("tenant_id"):
            raise AuthError("missing_tenant", "tenant_id claim missing")
        return claims
