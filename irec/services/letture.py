"""Letture per i tool autonomi dell'agente (addendum §6.2, livello 1).

KPI del portafoglio (PRD 4.10: affidato = recuperato + da recuperare +
passato a recupero crediti), aging, spiegazione delle comunicazioni
("perché non è partito X") e consumo per il billing.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from irec.adapters.db.models import (
    Comunicazione,
    Fattura,
    Posizione,
    SyncRun,
)
from irec.adapters.db.repository import TenantRepository
from irec.domain.calendario import assumi_utc
from irec.domain.enums import StatoComunicazione, StatoFattura, StatoPosizione

# Bucket di aging del credito scaduto, in giorni dalla scadenza.
AGING_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-30", 0, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("90+", 91, None),
)
ETICHETTA_A_SCADERE = "a_scadere"


@dataclass
class KpiPortafoglio:
    """I quattro KPI del PRD: l'affidato si scompone negli altri tre."""

    affidato: Decimal = Decimal("0.00")
    recuperato: Decimal = Decimal("0.00")
    da_recuperare: Decimal = Decimal("0.00")
    passato_a_recupero: Decimal = Decimal("0.00")
    fatture_per_stato: dict[str, int] = field(default_factory=dict)
    posizioni_aperte: int = 0
    posizioni_chiuse: int = 0


def calcola_kpi(repo: TenantRepository) -> KpiPortafoglio:
    kpi = KpiPortafoglio(
        fatture_per_stato={stato.value: 0 for stato in StatoFattura}
    )
    for fattura in repo.list(Fattura):
        kpi.affidato += fattura.importo
        kpi.recuperato += fattura.importo - fattura.importo_residuo
        if fattura.stato is StatoFattura.INSOLUTO:
            kpi.passato_a_recupero += fattura.importo_residuo
        else:
            kpi.da_recuperare += fattura.importo_residuo
        kpi.fatture_per_stato[fattura.stato.value] += 1
    for posizione in repo.list(Posizione):
        if posizione.stato is StatoPosizione.APERTA:
            kpi.posizioni_aperte += 1
        else:
            kpi.posizioni_chiuse += 1
    return kpi


@dataclass
class BucketAging:
    label: str
    importo: Decimal
    numero_fatture: int


def calcola_aging(repo: TenantRepository, oggi: date) -> list[BucketAging]:
    """Aging del credito ancora da incassare (Gestione/Pausa), per giorni
    di scaduto; le fatture non ancora scadute finiscono in `a_scadere`."""
    bucket = {label: BucketAging(label, Decimal("0.00"), 0) for label, _, _ in AGING_BUCKETS}
    bucket[ETICHETTA_A_SCADERE] = BucketAging(ETICHETTA_A_SCADERE, Decimal("0.00"), 0)

    for fattura in repo.list(Fattura):
        if fattura.stato not in (StatoFattura.GESTIONE, StatoFattura.PAUSA):
            continue
        giorni = (oggi - fattura.data_scadenza).days
        if giorni < 0:
            destinazione = bucket[ETICHETTA_A_SCADERE]
        else:
            destinazione = bucket[AGING_BUCKETS[-1][0]]
            for label, minimo, massimo in AGING_BUCKETS:
                if massimo is not None and minimo <= giorni <= massimo:
                    destinazione = bucket[label]
                    break
        destinazione.importo += fattura.importo_residuo
        destinazione.numero_fatture += 1

    return [bucket[ETICHETTA_A_SCADERE]] + [bucket[label] for label, _, _ in AGING_BUCKETS]


def spiegazione_comunicazione(
    comunicazione: Comunicazione, fattura: Fattura, adesso: datetime
) -> str:
    """Codice che risponde a "perché non è partito questo sollecito?".

    Codici (narrati poi dall'LLM di Mind): inviata, programmata,
    in_attesa_ripresa (fattura in pausa), in_coda (dovuta, run non ancora
    passata), saltata:<motivo>, annullata_fattura_saldata,
    annullata_escalation, annullata, recapito_fallito.
    """
    stato = comunicazione.stato
    if stato is StatoComunicazione.INVIATA:
        return "inviata"
    if stato is StatoComunicazione.FALLITA:
        return "recapito_fallito"
    if stato is StatoComunicazione.SALTATA:
        return f"saltata:{comunicazione.esito_recapito}"
    if stato is StatoComunicazione.ANNULLATA:
        if fattura.stato is StatoFattura.SALDATA:
            return "annullata_fattura_saldata"
        if fattura.stato is StatoFattura.INSOLUTO:
            return "annullata_escalation"
        return "annullata"
    # PROGRAMMATA
    if fattura.stato is StatoFattura.PAUSA:
        return "in_attesa_ripresa"
    if assumi_utc(comunicazione.programmata_per) > adesso:
        return "programmata"
    return "in_coda"


@dataclass
class ConsumoPeriodo:
    """Metriche di consumo per il billing (aggregate da Mind)."""

    fatture_gestite: int = 0
    messaggi_inviati: int = 0
    run_eseguite: int = 0


def calcola_consumo(
    repo: TenantRepository, dal: date | None, al: date | None
) -> ConsumoPeriodo:
    def nel_periodo(istante: datetime | None) -> bool:
        if istante is None:
            return False
        giorno = assumi_utc(istante).astimezone(UTC).date()
        if dal is not None and giorno < dal:
            return False
        return not (al is not None and giorno > al)

    consumo = ConsumoPeriodo()
    consumo.fatture_gestite = sum(
        1 for fattura in repo.list(Fattura) if nel_periodo(fattura.created_at)
    )
    consumo.messaggi_inviati = sum(
        1
        for comunicazione in repo.list(Comunicazione)
        if comunicazione.stato is StatoComunicazione.INVIATA
        and nel_periodo(comunicazione.inviata_at)
    )
    consumo.run_eseguite = sum(
        1 for run in repo.list(SyncRun) if nel_periodo(run.conclusa_at)
    )
    return consumo
