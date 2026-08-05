"""Azioni con conferma dell'agente (addendum §6.3, livello 2).

La conferma vive nella chat di Mind; qui c'è l'esecuzione, con i permessi
per pacchetto enforced NEL TOOL (addendum §5.3): il limite di pacchetto
risponde con un invito all'upgrade, non con un errore freddo.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from irec.adapters.db.models import (
    ClienteFinale,
    Comunicazione,
    Fattura,
    Flusso,
    FlussoStep,
    Mandante,
    Pagamento,
    Posizione,
)
from irec.adapters.db.repository import TenantRepository
from irec.domain.enums import (
    CANALI_PER_PACCHETTO,
    Canale,
    OriginePagamento,
    Pacchetto,
    StatoComunicazione,
    StatoFattura,
    TipoEvento,
)
from irec.domain.porte import CanaleInvio, MessaggioUscita
from irec.domain.scheduler import (
    istante_step,
    motivo_salto,
    recapiti_di,
    recapito_per,
)
from irec.domain.stati import (
    STATI_CREDITO_APERTO,
    residuo_dopo_incasso,
    stato_dopo_pagamento,
    stato_posizione,
)
from irec.errors import AppError
from irec.services.invii import metti_in_pausa
from irec.services.letture import calcola_kpi

TEMPLATE_REPORT = "report_periodico"

# Personalizzazione del flusso: da Value in su (PRD 1.3).
PACCHETTI_CON_FLUSSO_PERSONALIZZABILE = (Pacchetto.VALUE, Pacchetto.PREMIUM)


def _fattura(repo: TenantRepository, fattura_id: str) -> Fattura:
    fattura = repo.get(Fattura, fattura_id)
    if fattura is None:
        raise AppError(404, "invoice_not_found", "fattura non trovata")
    return fattura


def _mandante(repo: TenantRepository) -> Mandante:
    """Il mandante del tenant. 404 pulito se il provisioning è incompleto."""
    mandanti = repo.list(Mandante)
    if not mandanti:
        raise AppError(404, "mandante_not_found", "mandante non censito")
    return mandanti[0]


def pausa_fattura(
    repo: TenantRepository,
    fattura_id: str,
    fino_a: date | None,
    motivo: str,
    operatore: str,
) -> Fattura:
    """Sospende il flusso (promessa di pagamento, contestazione, manuale)."""
    fattura = _fattura(repo, fattura_id)
    if fattura.stato not in STATI_CREDITO_APERTO:
        raise AppError(
            409, "invoice_not_pausable", f"fattura in stato {fattura.stato}"
        )
    metti_in_pausa(repo, fattura, fino_a, operatore, motivo)
    repo.flush()
    return fattura


def riprendi_fattura(
    repo: TenantRepository, fattura_id: str, operatore: str
) -> Fattura:
    fattura = _fattura(repo, fattura_id)
    if fattura.stato is not StatoFattura.PAUSA:
        raise AppError(409, "invoice_not_paused", f"fattura in stato {fattura.stato}")
    fattura.stato = StatoFattura.GESTIONE
    fattura.pausa_fino_a = None
    repo.log_event(
        TipoEvento.AZIONE_MANUALE,
        entita="fattura",
        entita_id=fattura.id,
        stato_precedente=str(StatoFattura.PAUSA),
        stato_successivo=str(StatoFattura.GESTIONE),
        operatore=operatore,
        dettaglio="ripresa manuale del flusso",
    )
    repo.flush()
    return fattura


def annulla_comunicazione(
    repo: TenantRepository, comunicazione_id: str, operatore: str
) -> Comunicazione:
    comunicazione = repo.get(Comunicazione, comunicazione_id)
    if comunicazione is None:
        raise AppError(404, "communication_not_found", "comunicazione non trovata")
    if comunicazione.stato is not StatoComunicazione.PROGRAMMATA:
        raise AppError(
            409,
            "communication_not_cancellable",
            f"comunicazione in stato {comunicazione.stato}",
        )
    comunicazione.stato = StatoComunicazione.ANNULLATA
    comunicazione.operatore = operatore
    repo.log_event(
        TipoEvento.AZIONE_MANUALE,
        entita="comunicazione",
        entita_id=comunicazione.id,
        stato_precedente=str(StatoComunicazione.PROGRAMMATA),
        stato_successivo=str(StatoComunicazione.ANNULLATA),
        operatore=operatore,
        dettaglio="annullamento manuale",
    )
    repo.flush()
    return comunicazione


def forza_comunicazione(
    repo: TenantRepository,
    comunicazione_id: str,
    operatore: str,
    canale_invio: CanaleInvio,
    adesso: datetime,
) -> Comunicazione:
    """Invio immediato di un singolo step, fuori dalla finestra oraria.

    Restano validi i limiti strutturali: pacchetto, recapito e stato della
    fattura (mai forzare un sollecito su una fattura saldata: PRD 7.3).
    """
    comunicazione = repo.get(Comunicazione, comunicazione_id)
    if comunicazione is None:
        raise AppError(404, "communication_not_found", "comunicazione non trovata")
    if comunicazione.stato is not StatoComunicazione.PROGRAMMATA:
        raise AppError(
            409,
            "communication_not_forceable",
            f"comunicazione in stato {comunicazione.stato}",
        )
    fattura = _fattura(repo, comunicazione.fattura_id)
    if fattura.stato is not StatoFattura.GESTIONE:
        raise AppError(
            409, "invoice_not_active", f"fattura in stato {fattura.stato}"
        )
    mandante = _mandante(repo)
    cliente = repo.get(ClienteFinale, fattura.cliente_id)
    if cliente is None:  # pragma: no cover - FK composite lo impediscono
        raise AppError(404, "client_not_found", "cliente non trovato")
    recapiti = recapiti_di(
        cliente.email, cliente.pec, cliente.telefono, cliente.canali_opt_out
    )
    motivo = motivo_salto(comunicazione.canale, mandante.pacchetto, recapiti)
    if motivo is not None:
        raise AppError(409, "channel_unavailable", motivo)

    risposta = canale_invio.invia(
        repo.tenant_id,
        MessaggioUscita(
            canale=comunicazione.canale,
            destinatario=recapito_per(comunicazione.canale, recapiti),
            template=comunicazione.template,
            denominazione_destinatario=cliente.denominazione,
            numeri_fattura=(fattura.numero,),
            importo_totale_residuo=str(fattura.importo_residuo),
        ),
    )
    comunicazione.stato = (
        StatoComunicazione.INVIATA if risposta.consegnato else StatoComunicazione.FALLITA
    )
    comunicazione.esito_recapito = risposta.dettaglio
    comunicazione.operatore = operatore
    if risposta.consegnato:
        comunicazione.inviata_at = adesso
    repo.log_event(
        TipoEvento.AZIONE_MANUALE,
        entita="comunicazione",
        entita_id=comunicazione.id,
        stato_successivo=str(comunicazione.stato),
        operatore=operatore,
        dettaglio="invio forzato",
    )
    repo.flush()
    return comunicazione


@dataclass
class EsitoPagamentoManuale:
    fattura: Fattura
    gia_registrato: bool
    comunicazioni_annullate: int


def registra_pagamento_manuale(
    repo: TenantRepository,
    fattura_id: str,
    importo: Decimal,
    data_pagamento: date,
    chiave_idempotenza: str,
    operatore: str,
) -> EsitoPagamentoManuale:
    """Registra un incasso a mano (PRD 4.8).

    Idempotente sulla chiave: un retry restituisce l'esito senza doppioni.
    La coesistenza con la riconciliazione automatica è garantita dal
    residuo: il ciclo passa al riconciliatore il residuo aggiornato, quindi
    il movimento bancario che arriverà dopo non verrà ri-allocato.
    """
    if importo <= 0:
        raise AppError(400, "invalid_amount", "importo non positivo")
    fattura = _fattura(repo, fattura_id)

    esistenti = repo.find(
        Pagamento, Pagamento.chiave_idempotenza == chiave_idempotenza
    )
    if esistenti:
        return EsitoPagamentoManuale(
            fattura=fattura, gia_registrato=True, comunicazioni_annullate=0
        )
    if fattura.stato is StatoFattura.SALDATA:
        raise AppError(409, "invoice_already_paid", "fattura già saldata")
    if fattura.stato is StatoFattura.INSOLUTO:
        # Uscita dal perimetro solleciti a T+45: gli incassi si registrano
        # dal servizio Recupero Crediti, non da qui.
        raise AppError(
            409, "invoice_in_recovery", "fattura passata a Recupero Crediti"
        )

    repo.add(
        Pagamento(
            fattura_id=fattura.id,
            importo=importo,
            data_pagamento=data_pagamento,
            origine=OriginePagamento.MANUALE,
            chiave_idempotenza=chiave_idempotenza,
            operatore=operatore,
        )
    )
    stato_precedente = fattura.stato
    fattura.importo_residuo = residuo_dopo_incasso(fattura.importo_residuo, importo)
    fattura.stato = stato_dopo_pagamento(fattura.stato, fattura.importo_residuo)

    annullate = 0
    if fattura.stato is StatoFattura.SALDATA:
        for comunicazione in fattura.comunicazioni:
            if comunicazione.stato is StatoComunicazione.PROGRAMMATA:
                comunicazione.stato = StatoComunicazione.ANNULLATA
                annullate += 1
        posizione = repo.get(Posizione, fattura.posizione_id)
        if posizione is not None:
            stati = [f.stato for f in posizione.fatture]
            posizione.stato = stato_posizione(stati)

    repo.log_event(
        TipoEvento.PAGAMENTO,
        entita="fattura",
        entita_id=fattura.id,
        stato_precedente=str(stato_precedente),
        stato_successivo=str(fattura.stato),
        operatore=operatore,
        dettaglio=f"pagamento manuale, chiave {chiave_idempotenza}",
    )
    repo.flush()
    return EsitoPagamentoManuale(
        fattura=fattura, gia_registrato=False, comunicazioni_annullate=annullate
    )


def aggiorna_recapiti(
    repo: TenantRepository,
    cliente_id: str,
    operatore: str,
    email: str | None = None,
    pec: str | None = None,
    telefono: str | None = None,
    canali_opt_out: list[str] | None = None,
) -> ClienteFinale:
    """Aggiorna i recapiti per sbloccare un canale (PRD 7.2). I campi non
    passati restano invariati."""
    cliente = repo.get(ClienteFinale, cliente_id)
    if cliente is None:
        raise AppError(404, "client_not_found", "cliente non trovato")
    if email is not None:
        cliente.email = email
    if pec is not None:
        cliente.pec = pec
    if telefono is not None:
        cliente.telefono = telefono
    if canali_opt_out is not None:
        canali_validi = {canale.value for canale in Canale}
        non_validi = set(canali_opt_out) - canali_validi
        if non_validi:
            raise AppError(400, "invalid_channel", f"canali sconosciuti: {sorted(non_validi)}")
        cliente.canali_opt_out = canali_opt_out
    repo.log_event(
        TipoEvento.AZIONE_MANUALE,
        entita="cliente",
        entita_id=cliente.id,
        operatore=operatore,
        dettaglio="aggiornamento recapiti",
    )
    repo.flush()
    return cliente


@dataclass
class StepFlusso:
    ordine: int
    offset_giorni: int
    canale: Canale
    template: str


def _valida_flusso(mandante: Mandante, steps: list[StepFlusso]) -> None:
    """Permessi per pacchetto e coerenza degli step (addendum §5.3)."""
    if mandante.pacchetto not in PACCHETTI_CON_FLUSSO_PERSONALIZZABILE:
        # Upsell garbato, non errore freddo.
        raise AppError(
            403,
            "upgrade_required",
            "La personalizzazione del flusso è disponibile dal pacchetto "
            "Value: chiedi pure e ti spiego cosa sblocca.",
        )
    if not steps:
        raise AppError(400, "empty_flow", "il flusso deve avere almeno uno step")
    ordini = [step.ordine for step in steps]
    if len(ordini) != len(set(ordini)):
        raise AppError(400, "duplicate_step_order", "ordini degli step duplicati")
    canali_ammessi = CANALI_PER_PACCHETTO[mandante.pacchetto]
    fuori_pacchetto = {
        step.canale for step in steps if step.canale not in canali_ammessi
    }
    if fuori_pacchetto:
        raise AppError(
            403,
            "upgrade_required",
            f"I canali {sorted(c.value for c in fuori_pacchetto)} non sono nel "
            f"pacchetto {mandante.pacchetto.value}: con un upgrade si sbloccano.",
        )


def _riprogramma_comunicazioni(
    repo: TenantRepository, nuovi_step: list[FlussoStep], adesso: datetime
) -> None:
    """Annulla le programmate del vecchio flusso e crea le nuove SOLO con
    istante futuro: gli step già passati non si recuperano (PRD 4.5)."""
    for fattura in repo.find(
        Fattura, Fattura.stato.in_(list(STATI_CREDITO_APERTO))
    ):
        for comunicazione in fattura.comunicazioni:
            if comunicazione.stato is StatoComunicazione.PROGRAMMATA:
                comunicazione.stato = StatoComunicazione.ANNULLATA
                comunicazione.esito_recapito = "flusso_modificato"
        for riga in nuovi_step:
            istante = istante_step(fattura.data_scadenza, riga.offset_giorni)
            if istante <= adesso:
                continue
            repo.add(
                Comunicazione(
                    fattura_id=fattura.id,
                    step_id=riga.id,
                    canale=riga.canale,
                    template=riga.template,
                    programmata_per=istante,
                )
            )


def sostituisci_flusso(
    repo: TenantRepository,
    steps: list[StepFlusso],
    operatore: str,
    adesso: datetime,
) -> Flusso:
    """Sostituisce il flusso del mandante (US-02, Value/Premium).

    Le modifiche valgono per gli step FUTURI: le comunicazioni già inviate
    restano storicizzate, quelle programmate del vecchio flusso vengono
    annullate e ri-programmate dai nuovi step (solo con data futura).
    """
    mandante = _mandante(repo)
    _valida_flusso(mandante, steps)

    for flusso in repo.list(Flusso):
        flusso.attivo = False
    nuovo = repo.add(Flusso(mandante_id=mandante.id, nome="Flusso personalizzato"))
    repo.flush()
    nuovi_step = [
        repo.add(
            FlussoStep(
                flusso_id=nuovo.id,
                ordine=step.ordine,
                offset_giorni=step.offset_giorni,
                canale=step.canale,
                template=step.template,
            )
        )
        for step in sorted(steps, key=lambda s: s.ordine)
    ]
    repo.flush()

    _riprogramma_comunicazioni(repo, nuovi_step, adesso)
    repo.log_event(
        TipoEvento.AZIONE_MANUALE,
        entita="flusso",
        entita_id=nuovo.id,
        operatore=operatore,
        dettaglio=f"flusso sostituito, {len(nuovi_step)} step",
    )
    repo.flush()
    return nuovo


@dataclass
class EsitoReport:
    inviato: bool
    destinatario_presente: bool


def genera_e_invia_report(
    repo: TenantRepository, canale_invio: CanaleInvio, operatore: str
) -> EsitoReport:
    """Genera il report KPI e lo invia al mandante via email (PRD 4.10)."""
    mandante = _mandante(repo)
    if not mandante.alias_email:
        return EsitoReport(inviato=False, destinatario_presente=False)
    kpi = calcola_kpi(repo)
    risposta = canale_invio.invia(
        repo.tenant_id,
        MessaggioUscita(
            canale=Canale.EMAIL,
            destinatario=mandante.alias_email,
            template=TEMPLATE_REPORT,
            denominazione_destinatario=mandante.ragione_sociale,
            numeri_fattura=(),
            importo_totale_residuo=str(kpi.da_recuperare),
        ),
    )
    repo.log_event(
        TipoEvento.AZIONE_MANUALE,
        entita="mandante",
        entita_id=mandante.id,
        operatore=operatore,
        dettaglio=f"report inviato: {risposta.consegnato}",
    )
    repo.flush()
    return EsitoReport(inviato=risposta.consegnato, destinatario_presente=True)
