from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from irec.api.deps import RepositoryDep
from irec.auth.context import CallContext, get_call_context

router = APIRouter(prefix="/v1")


class CancellazioneTenant(BaseModel):
    tenant_cancellato: bool
    righe_cancellate: dict[str, int]


@router.delete("/tenant", response_model=CancellazioneTenant)
def cancella_tenant(
    repo: RepositoryDep,
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> CancellazioneTenant:
    """Cancellazione GDPR di tutti i dati del tenant del call-token.

    Il tenant cancellato è sempre quello del token: non è possibile
    cancellare i dati di un altro tenant.

    Output: conteggio delle righe rimosse per tabella.
    Errori: 401/403 auth, 503 database_not_configured.
    """
    conteggi = repo.cancella_tenant()
    return CancellazioneTenant(tenant_cancellato=True, righe_cancellate=conteggi)
