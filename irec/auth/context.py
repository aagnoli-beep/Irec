from dataclasses import dataclass
from typing import Annotated, cast

import anyio.to_thread
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from irec.auth.verifier import AuthError, CallTokenVerifier
from irec.errors import AppError
from irec.logging_setup import tenant_id_var

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CallContext:
    """Identità della chiamata Mind→IREC. Ogni query DB va filtrata per tenant_id.

    Il tenant per lo scoping dati deve venire SEMPRE da qui, mai dal
    contextvar dei log né da campi controllati dal client.
    """

    sub: str
    tenant_id: str
    entitlement: str
    scope: str | None
    jti: str | None


def get_verifier(request: Request) -> CallTokenVerifier:
    verifier = getattr(request.app.state, "verifier", None)
    if verifier is None:
        raise AppError(503, "auth_not_configured", "token verifier not configured")
    return cast(CallTokenVerifier, verifier)


async def get_call_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[CallTokenVerifier, Depends(get_verifier)],
) -> CallContext:
    """Verifica il call-token e restituisce il CallContext.

    Errori: 401 missing_token / invalid_token / token_expired / invalid_audience /
    unknown_key / missing_tenant; 403 entitlement_missing; 503 auth_not_configured /
    jwks_unavailable.
    """
    if credentials is None:
        raise AppError(401, "missing_token", "missing bearer token")
    try:
        # La verifica (con eventuale fetch JWKS bloccante) gira in un thread;
        # la dependency resta async così il set del contextvar avviene nel
        # contesto della request e arriva ai log dell'endpoint.
        claims = await anyio.to_thread.run_sync(verifier.verify, credentials.credentials)
    except AuthError as exc:
        status = 503 if exc.code == "jwks_unavailable" else 401
        raise AppError(status, exc.code, exc.message) from exc

    entitlement = claims.get("entitlement")
    if not entitlement:
        raise AppError(403, "entitlement_missing", "no IREC entitlement for this user")

    tenant_id_var.set(claims["tenant_id"])
    return CallContext(
        sub=claims["sub"],
        tenant_id=claims["tenant_id"],
        entitlement=entitlement,
        scope=claims.get("scope"),
        jti=claims.get("jti"),
    )
