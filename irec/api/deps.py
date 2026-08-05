from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.auth.context import CallContext, get_call_context
from irec.errors import AppError


def get_repository(
    request: Request,
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> Iterator[TenantRepository]:
    """Repository già legato al tenant del call-token.

    È l'unico punto in cui il tenant entra nel layer dati: nessun handler
    deve costruire un repository con un tenant preso da altrove.

    Errori: 503 database_not_configured.
    """
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise AppError(503, "database_not_configured", "database not configured")
    with session_scope(factory) as session:
        yield TenantRepository(session, ctx.tenant_id)


RepositoryDep = Annotated[TenantRepository, Depends(get_repository)]
