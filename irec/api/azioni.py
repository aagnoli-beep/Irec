"""Rotte di azione con conferma /v1 (addendum §6.3, livello 2).

Ogni azione richiede lo scope `irec.write` nel call-token: la conferma
dell'utente avviene nella chat di Mind, che conia il token con quello
scope solo dopo il "sì". I permessi per pacchetto sono enforced nei
servizi (`irec/services/azioni.py`), non qui.
"""

from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Request

from irec.api.deps import RepositoryDep
from irec.api.schemas import (
    FatturaStatoOut,
    FlussoIn,
    OkOut,
    PagamentoManualeIn,
    PagamentoManualeOut,
    PausaIn,
    RecapitiIn,
    ReportOut,
)
from irec.auth.context import CallContext, get_call_context
from irec.domain.enums import Canale
from irec.domain.porte import CanaleInvio
from irec.errors import AppError
from irec.services import azioni as svc

router = APIRouter(prefix="/v1")

SCOPE_WRITE = "irec.write"


def require_write(
    ctx: Annotated[CallContext, Depends(get_call_context)],
) -> CallContext:
    """Le azioni richiedono lo scope di scrittura firmato da Mind."""
    if SCOPE_WRITE not in (ctx.scope or "").split():
        raise AppError(403, "scope_missing", f"scope {SCOPE_WRITE} richiesto")
    return ctx


WriteContext = Annotated[CallContext, Depends(require_write)]


def _canale_invio(request: Request) -> CanaleInvio:
    providers = getattr(request.app.state, "providers", None)
    if providers is None:
        raise AppError(503, "providers_not_configured", "external providers not configured")
    return cast(CanaleInvio, providers.canale_invio)


@router.post("/invoices/{invoice_id}/pause", response_model=FatturaStatoOut)
def pause_invoice(
    invoice_id: str, body: PausaIn, repo: RepositoryDep, ctx: WriteContext
) -> FatturaStatoOut:
    """Sospende il flusso su una fattura. Azione con conferma."""
    fattura = svc.pausa_fattura(repo, invoice_id, body.fino_a, body.motivo, ctx.sub)
    return FatturaStatoOut(id=fattura.id, stato=fattura.stato.value)


@router.post("/invoices/{invoice_id}/resume", response_model=FatturaStatoOut)
def resume_invoice(
    invoice_id: str, repo: RepositoryDep, ctx: WriteContext
) -> FatturaStatoOut:
    """Riprende il flusso su una fattura in pausa. Azione con conferma."""
    fattura = svc.riprendi_fattura(repo, invoice_id, ctx.sub)
    return FatturaStatoOut(id=fattura.id, stato=fattura.stato.value)


@router.post("/communications/{communication_id}/cancel", response_model=OkOut)
def cancel_communication(
    communication_id: str, repo: RepositoryDep, ctx: WriteContext
) -> OkOut:
    """Annulla un singolo step programmato. Azione con conferma."""
    svc.annulla_comunicazione(repo, communication_id, ctx.sub)
    return OkOut()


@router.post("/communications/{communication_id}/force", response_model=OkOut)
def force_communication(
    communication_id: str, request: Request, repo: RepositoryDep, ctx: WriteContext
) -> OkOut:
    """Forza l'invio immediato di uno step. Azione con conferma."""
    svc.forza_comunicazione(
        repo, communication_id, ctx.sub, _canale_invio(request), datetime.now(UTC)
    )
    return OkOut()


@router.post("/invoices/{invoice_id}/payments", response_model=PagamentoManualeOut)
def register_payment(
    invoice_id: str,
    body: PagamentoManualeIn,
    repo: RepositoryDep,
    ctx: WriteContext,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PagamentoManualeOut:
    """Registra un pagamento manuale (idempotente). Azione con conferma.

    `Idempotency-Key` obbligatoria (header): un retry con la stessa chiave
    restituisce l'esito senza registrare due volte l'incasso.
    """
    if not idempotency_key:
        raise AppError(400, "missing_idempotency_key", "Idempotency-Key header required")
    esito = svc.registra_pagamento_manuale(
        repo, invoice_id, body.importo, body.data_pagamento, idempotency_key, ctx.sub
    )
    return PagamentoManualeOut(
        fattura_id=esito.fattura.id,
        stato=esito.fattura.stato.value,
        gia_registrato=esito.gia_registrato,
        comunicazioni_annullate=esito.comunicazioni_annullate,
    )


@router.patch("/clients/{client_id}/contacts", response_model=OkOut)
def update_contacts(
    client_id: str, body: RecapitiIn, repo: RepositoryDep, ctx: WriteContext
) -> OkOut:
    """Aggiorna i recapiti di un cliente per sbloccare un canale."""
    svc.aggiorna_recapiti(
        repo,
        client_id,
        ctx.sub,
        email=body.email,
        pec=body.pec,
        telefono=body.telefono,
        canali_opt_out=body.canali_opt_out,
    )
    return OkOut()


@router.put("/flow", response_model=OkOut)
def replace_flow(
    body: FlussoIn, repo: RepositoryDep, ctx: WriteContext
) -> OkOut:
    """Sostituisce il flusso del mandante (Value/Premium). Azione con conferma."""
    steps = []
    for step in body.steps:
        try:
            canale = Canale(step.canale)
        except ValueError as exc:
            raise AppError(400, "invalid_channel", f"canale sconosciuto: {step.canale}") from exc
        steps.append(
            svc.StepFlusso(
                ordine=step.ordine,
                offset_giorni=step.offset_giorni,
                canale=canale,
                template=step.template,
            )
        )
    svc.sostituisci_flusso(repo, steps, ctx.sub, datetime.now(UTC))
    return OkOut()


@router.post("/report", response_model=ReportOut)
def send_report(
    request: Request, repo: RepositoryDep, ctx: WriteContext
) -> ReportOut:
    """Genera e invia il report al mandante via email. Azione con conferma."""
    esito = svc.genera_e_invia_report(repo, _canale_invio(request), ctx.sub)
    return ReportOut(
        inviato=esito.inviato, destinatario_presente=esito.destinatario_presente
    )
