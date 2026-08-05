import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from irec.api.deps import RepositoryDep
from irec.auth.context import CallContext, get_call_context
from irec.errors import AppError
from irec.logging_setup import truncate_tenant

router = APIRouter(prefix="/v1")

logger = logging.getLogger("irec.tenant")

# La cancellazione è irreversibile: Mind deve coniare un token con questo
# scope solo per l'operazione, non per la normale operatività.
SCOPE_CANCELLAZIONE = "irec.tenant.delete"


class CancellazioneTenant(BaseModel):
    tenant_cancellato: bool
    righe_cancellate: dict[str, int]


@router.delete("/tenant", response_model=CancellazioneTenant)
def delete_tenant_data(
    repo: RepositoryDep,
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> CancellazioneTenant:
    """Cancellazione GDPR di tutti i dati del tenant del call-token.

    Il tenant cancellato è sempre quello del token: non è possibile
    cancellare i dati di un altro tenant. Richiede lo scope
    `irec.tenant.delete` nel token firmato da Mind.

    Output: conteggio delle righe rimosse per tabella.
    Errori: 401 auth, 403 entitlement_missing / scope_missing,
    503 database_not_configured.
    """
    if SCOPE_CANCELLAZIONE not in (ctx.scope or "").split():
        raise AppError(
            403,
            "scope_missing",
            f"scope {SCOPE_CANCELLAZIONE} richiesto per la cancellazione",
        )

    # L'audit trail è fra le tabelle cancellate: la prova dell'operazione
    # deve vivere fuori dal database del tenant.
    logger.warning(
        "cancellazione GDPR del tenant",
        extra={"sub": ctx.sub, "jti": ctx.jti, "tenant": truncate_tenant(ctx.tenant_id)},
    )
    conteggi = repo.delete_tenant_data()
    logger.warning("cancellazione GDPR completata", extra={"righe": conteggi})
    return CancellazioneTenant(tenant_cancellato=True, righe_cancellate=conteggi)
