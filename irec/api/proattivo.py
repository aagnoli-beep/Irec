"""Rotte proattive /v1 (M6): brief giornaliero e notifiche in polling."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from irec.adapters.db.models import Notifica
from irec.api.azioni import WriteContext
from irec.api.deps import RepositoryDep
from irec.domain.calendario import assumi_utc
from irec.services.notifiche import marca_lette, notifiche_da_consegnare
from irec.services.reporting import componi_brief_giornaliero

router = APIRouter(prefix="/v1")


class VoceBriefOut(BaseModel):
    tipo: str
    quante: int


class BriefOut(BaseModel):
    affidato: str
    recuperato: str
    da_recuperare: str
    passato_a_recupero: str
    percentuale_recuperato: int
    azioni_principali: list[VoceBriefOut]
    altre_azioni: int


class NotificaOut(BaseModel):
    id: str
    tipo: str
    riferimento: str
    dettaglio: str | None
    created_at: datetime


class NotificheOut(BaseModel):
    items: list[NotificaOut]


class AckIn(BaseModel):
    ids: list[str]


class AckOut(BaseModel):
    marcate: int


@router.get("/brief", response_model=BriefOut)
def get_brief(repo: RepositoryDep) -> BriefOut:
    """Brief giornaliero: KPI + azioni proposte (max 3). Lettura autonoma.

    Solo numeri e codici: il tono lo mette l'LLM di Mind (addendum §5.2)."""
    brief = componi_brief_giornaliero(repo)
    return BriefOut(
        affidato=brief.affidato,
        recuperato=brief.recuperato,
        da_recuperare=brief.da_recuperare,
        passato_a_recupero=brief.passato_a_recupero,
        percentuale_recuperato=brief.percentuale_recuperato,
        azioni_principali=[
            VoceBriefOut(tipo=voce.tipo.value, quante=voce.quante)
            for voce in brief.azioni_principali
        ],
        altre_azioni=brief.altre_azioni,
    )


@router.get("/notifications", response_model=NotificheOut)
def get_notifications(repo: RepositoryDep) -> NotificheOut:
    """Notifiche non ancora lette, dalla più recente. Lettura autonoma.

    Mind le legge in polling e conferma la ricezione con POST /ack."""
    return NotificheOut(
        items=[_out(n) for n in notifiche_da_consegnare(repo)]
    )


@router.post("/notifications/ack", response_model=AckOut)
def ack_notifications(body: AckIn, repo: RepositoryDep, ctx: WriteContext) -> AckOut:
    """Marca come lette le notifiche indicate. Conferma di ricezione.

    È una mutazione: richiede lo scope `irec.write` firmato da Mind."""
    return AckOut(marcate=marca_lette(repo, body.ids))


def _out(notifica: Notifica) -> NotificaOut:
    return NotificaOut(
        id=notifica.id,
        tipo=notifica.tipo.value,
        riferimento=notifica.riferimento,
        dettaglio=notifica.dettaglio,
        created_at=assumi_utc(notifica.created_at),
    )
