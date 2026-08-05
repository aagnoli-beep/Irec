"""Schemi Pydantic del contratto /v1.

Convenzione (docs/ARCHITECTURE §Convenzione di naming): envelope e campi
di protocollo in inglese, dominio in italiano. Gli importi viaggiano come
stringhe decimali (mai float: dominio finanziario).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer

# Importo serializzato come stringa decimale (mai float: dominio finanziario).
# Un solo tipo riusabile, così ogni campo-importo eredita la convenzione senza
# ripetere il serializer.
ImportoStr = Annotated[Decimal, PlainSerializer(str, return_type=str)]


class KpiOut(BaseModel):
    affidato: ImportoStr
    recuperato: ImportoStr
    da_recuperare: ImportoStr
    passato_a_recupero: ImportoStr
    fatture_per_stato: dict[str, int]
    posizioni_aperte: int
    posizioni_chiuse: int


class BucketAgingOut(BaseModel):
    label: str
    importo: ImportoStr
    numero_fatture: int


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
    importo: ImportoStr
    importo_residuo: ImportoStr
    stato: str


class FattureOut(BaseModel):
    items: list[FatturaOut]


class PosizioneOut(BaseModel):
    id: str
    cliente: str
    piva_cf: str
    stato: str
    importo_totale_residuo: ImportoStr
    fatture: list[FatturaOut]


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


# Tetto di sicurezza sull'importo di un pagamento manuale: oltre questo è
# quasi certamente un errore di battitura, e protegge dall'overflow NUMERIC.
IMPORTO_MASSIMO = Decimal("100000000.00")


class PagamentoManualeIn(BaseModel):
    importo: Decimal = Field(gt=0, le=IMPORTO_MASSIMO)
    data_pagamento: date


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
