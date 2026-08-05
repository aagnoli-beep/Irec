"""Rotte di lettura autonoma /v1 (addendum §6.2)."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from irec.adapters.db.models import ClienteFinale, Comunicazione, Fattura, Posizione
from irec.api.deps import RepositoryDep
from irec.api.schemas import (
    AgingOut,
    BucketAgingOut,
    ComunicazioneOut,
    FatturaOut,
    FattureOut,
    KpiOut,
    PosizioneOut,
    ProssimiInviiOut,
    SpiegazioneOut,
    StoricoOut,
    UsageOut,
)
from irec.domain.calendario import assumi_utc
from irec.domain.enums import StatoComunicazione, StatoFattura
from irec.errors import AppError
from irec.services.letture import (
    calcola_aging,
    calcola_consumo,
    calcola_kpi,
    spiegazione_comunicazione,
)

router = APIRouter(prefix="/v1")

STATI_SCADUTA = (StatoFattura.GESTIONE, StatoFattura.PAUSA)


def _clienti(repo: RepositoryDep) -> dict[str, ClienteFinale]:
    return {cliente.id: cliente for cliente in repo.list(ClienteFinale)}


def _fattura_out(fattura: Fattura, cliente: ClienteFinale) -> FatturaOut:
    return FatturaOut(
        id=fattura.id,
        numero=fattura.numero,
        cliente=cliente.denominazione,
        piva_cf=cliente.piva_cf,
        data_emissione=fattura.data_emissione,
        data_scadenza=fattura.data_scadenza,
        importo=fattura.importo,
        importo_residuo=fattura.importo_residuo,
        stato=fattura.stato.value,
    )


def _com_out(com: Comunicazione) -> ComunicazioneOut:
    return ComunicazioneOut(
        id=com.id,
        canale=com.canale.value,
        template=com.template,
        stato=com.stato.value,
        programmata_per=assumi_utc(com.programmata_per),
        inviata_at=assumi_utc(com.inviata_at) if com.inviata_at else None,
        esito_recapito=com.esito_recapito,
    )


@router.get("/portfolio", response_model=KpiOut)
def get_portfolio(repo: RepositoryDep) -> KpiOut:
    """KPI del portafoglio: affidato = recuperato + da recuperare + passato
    a recupero crediti (PRD 4.10). Lettura autonoma."""
    kpi = calcola_kpi(repo)
    return KpiOut(
        affidato=kpi.affidato,
        recuperato=kpi.recuperato,
        da_recuperare=kpi.da_recuperare,
        passato_a_recupero=kpi.passato_a_recupero,
        fatture_per_stato=kpi.fatture_per_stato,
        posizioni_aperte=kpi.posizioni_aperte,
        posizioni_chiuse=kpi.posizioni_chiuse,
    )


@router.get("/aging", response_model=AgingOut)
def get_aging(
    repo: RepositoryDep,
    as_of: Annotated[date | None, Query()] = None,
) -> AgingOut:
    """Bucket di aging del credito da incassare. Lettura autonoma."""
    oggi = as_of or datetime.now(UTC).date()
    buckets = calcola_aging(repo, oggi)
    return AgingOut(
        as_of=oggi,
        buckets=[
            BucketAgingOut(
                label=b.label, importo=b.importo, numero_fatture=b.numero_fatture
            )
            for b in buckets
        ],
    )


@router.get("/invoices", response_model=FattureOut)
def get_invoices(
    repo: RepositoryDep,
    status: Annotated[str | None, Query()] = None,
    as_of: Annotated[date | None, Query()] = None,
) -> FattureOut:
    """Elenco fatture, filtrabile per stato. `scaduta` è un filtro derivato
    (data_scadenza passata e fattura non ancora saldata). Lettura autonoma."""
    clienti = _clienti(repo)
    oggi = as_of or datetime.now(UTC).date()

    selezionate: Sequence[Fattura]
    if status == "scaduta":
        selezionate = [
            f
            for f in repo.list(Fattura)
            if f.stato in STATI_SCADUTA and f.data_scadenza < oggi
        ]
    elif status is not None:
        try:
            stato = StatoFattura(status)
        except ValueError as exc:
            raise AppError(400, "invalid_status", f"stato sconosciuto: {status}") from exc
        selezionate = repo.find(Fattura, Fattura.stato == stato)
    else:
        selezionate = repo.list(Fattura)

    ordinate = sorted(selezionate, key=lambda f: (f.data_scadenza, f.numero))
    return FattureOut(
        items=[_fattura_out(f, clienti[f.cliente_id]) for f in ordinate]
    )


@router.get("/positions/{position_id}", response_model=PosizioneOut)
def get_position(position_id: str, repo: RepositoryDep) -> PosizioneOut:
    """Dettaglio di una posizione con le sue fatture. Lettura autonoma."""
    posizione = repo.get(Posizione, position_id)
    if posizione is None:
        raise AppError(404, "position_not_found", "posizione non trovata")
    clienti = _clienti(repo)
    cliente = clienti[posizione.cliente_id]
    fatture = sorted(posizione.fatture, key=lambda f: (f.data_scadenza, f.numero))
    totale = sum((f.importo_residuo for f in fatture), Decimal("0.00"))
    return PosizioneOut(
        id=posizione.id,
        cliente=cliente.denominazione,
        piva_cf=cliente.piva_cf,
        stato=posizione.stato.value,
        importo_totale_residuo=totale,
        fatture=[_fattura_out(f, cliente) for f in fatture],
    )


@router.get("/invoices/{invoice_id}/history", response_model=StoricoOut)
def get_invoice_history(invoice_id: str, repo: RepositoryDep) -> StoricoOut:
    """Cronologia delle comunicazioni di una fattura. Lettura autonoma."""
    fattura = repo.get(Fattura, invoice_id)
    if fattura is None:
        raise AppError(404, "invoice_not_found", "fattura non trovata")
    comunicazioni = sorted(
        fattura.comunicazioni, key=lambda c: assumi_utc(c.programmata_per)
    )
    return StoricoOut(
        fattura_id=fattura.id, comunicazioni=[_com_out(c) for c in comunicazioni]
    )


@router.get("/invoices/{invoice_id}/next", response_model=ProssimiInviiOut)
def get_next_communications(invoice_id: str, repo: RepositoryDep) -> ProssimiInviiOut:
    """Step programmati futuri di una fattura. Lettura autonoma."""
    fattura = repo.get(Fattura, invoice_id)
    if fattura is None:
        raise AppError(404, "invoice_not_found", "fattura non trovata")
    prossimi = sorted(
        (
            c
            for c in fattura.comunicazioni
            if c.stato is StatoComunicazione.PROGRAMMATA
        ),
        key=lambda c: assumi_utc(c.programmata_per),
    )
    return ProssimiInviiOut(
        fattura_id=fattura.id, prossimi=[_com_out(c) for c in prossimi]
    )


@router.get(
    "/communications/{communication_id}/explain", response_model=SpiegazioneOut
)
def explain_communication(communication_id: str, repo: RepositoryDep) -> SpiegazioneOut:
    """Perché una comunicazione è (o non è) partita. Lettura autonoma."""
    com = repo.get(Comunicazione, communication_id)
    if com is None:
        raise AppError(404, "communication_not_found", "comunicazione non trovata")
    fattura = repo.get(Fattura, com.fattura_id)
    if fattura is None:  # pragma: no cover - FK composite lo impediscono
        raise AppError(404, "invoice_not_found", "fattura non trovata")
    codice = spiegazione_comunicazione(com, fattura, datetime.now(UTC))
    return SpiegazioneOut(comunicazione_id=com.id, codice=codice)


@router.get("/usage", response_model=UsageOut)
def get_usage(
    repo: RepositoryDep,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> UsageOut:
    """Consumo per il billing (Mind lo aggrega). Lettura autonoma."""
    consumo = calcola_consumo(repo, from_, to)
    return UsageOut(
        tenant_id=repo.tenant_id,
        period_from=from_,
        period_to=to,
        metrics={
            "fatture_gestite": consumo.fatture_gestite,
            "messaggi_inviati": consumo.messaggi_inviati,
            "run_eseguite": consumo.run_eseguite,
        },
    )
