"""Schemi Pydantic del contratto /v1.

Convenzione (docs/ARCHITECTURE §Convenzione di naming): envelope e campi
di protocollo in inglese, dominio in italiano. Gli importi viaggiano come
stringhe decimali (mai float: dominio finanziario).
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, field_serializer


class ImportoMixin:
    @staticmethod
    def _dec(value: Decimal) -> str:
        return str(value)


class KpiOut(BaseModel):
    affidato: Decimal
    recuperato: Decimal
    da_recuperare: Decimal
    passato_a_recupero: Decimal
    fatture_per_stato: dict[str, int]
    posizioni_aperte: int
    posizioni_chiuse: int

    @field_serializer("affidato", "recuperato", "da_recuperare", "passato_a_recupero")
    def _ser(self, value: Decimal) -> str:
        return str(value)


class BucketAgingOut(BaseModel):
    label: str
    importo: Decimal
    numero_fatture: int

    @field_serializer("importo")
    def _ser(self, value: Decimal) -> str:
        return str(value)


class AgingOut(BaseModel):
    as_of: date
    buckets: list[BucketAgingOut]


class FatturaOut(BaseModel):
    id: str
    numero: str
    cliente: str
    piva_cf: str
    data_emissione: date
    data_scadenza: date
    importo: Decimal
    importo_residuo: Decimal
    stato: str

    @field_serializer("importo", "importo_residuo")
    def _ser(self, value: Decimal) -> str:
        return str(value)


class FattureOut(BaseModel):
    items: list[FatturaOut]


class PosizioneOut(BaseModel):
    id: str
    cliente: str
    piva_cf: str
    stato: str
    importo_totale_residuo: Decimal
    fatture: list[FatturaOut]

    @field_serializer("importo_totale_residuo")
    def _ser(self, value: Decimal) -> str:
        return str(value)


class ComunicazioneOut(BaseModel):
    id: str
    canale: str
    template: str
    stato: str
    programmata_per: datetime
    inviata_at: datetime | None
    esito_recapito: str | None


class StoricoOut(BaseModel):
    fattura_id: str
    comunicazioni: list[ComunicazioneOut]


class ProssimiInviiOut(BaseModel):
    fattura_id: str
    prossimi: list[ComunicazioneOut]


class SpiegazioneOut(BaseModel):
    comunicazione_id: str
    codice: str


class UsageOut(BaseModel):
    tenant_id: str
    period_from: date | None
    period_to: date | None
    metrics: dict[str, int]


# --- azioni (input) ---


class PausaIn(BaseModel):
    fino_a: date | None = None
    motivo: str = "sospensione manuale"


class PagamentoManualeIn(BaseModel):
    importo: Decimal
    data_pagamento: date
    idempotency_key: str


class RecapitiIn(BaseModel):
    email: str | None = None
    pec: str | None = None
    telefono: str | None = None
    canali_opt_out: list[str] | None = None


class StepIn(BaseModel):
    ordine: int
    offset_giorni: int
    canale: str
    template: str


class FlussoIn(BaseModel):
    steps: list[StepIn]


# --- azioni (output) ---


class FatturaStatoOut(BaseModel):
    id: str
    stato: str


class PagamentoManualeOut(BaseModel):
    fattura_id: str
    stato: str
    gia_registrato: bool
    comunicazioni_annullate: int


class ReportOut(BaseModel):
    inviato: bool
    destinatario_presente: bool


class OkOut(BaseModel):
    ok: bool = True
