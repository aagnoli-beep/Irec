"""Motore di invio dei solleciti (M4): la parte del ciclo che parla al cliente.

Sequenza per tenant, nell'ordine che protegge il debitore:
1. ripresa delle pause scadute (promessa di pagamento non mantenuta);
2. escalation a T+45 (mail a Recupero Crediti + mandante, fattura →
   Insoluto) e conteggio dei preavvisi T+44 per le notifiche (M6);
3. invii dovuti: controllo just-in-time, canali per pacchetto con salto
   segnalato, consolidamento per cliente/canale, un messaggio per gruppo.

Le REGOLE (calendario, canali, consolidamento, soglie) stanno in
`irec/domain/scheduler.py`; qui c'è il coordinamento con il repository e
il canale di invio.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from irec.adapters.db.models import (
    ClienteFinale,
    Comunicazione,
    Fattura,
    Flusso,
    Mandante,
)
from irec.adapters.db.repository import TenantRepository
from irec.domain.enums import (
    Canale,
    StatoComunicazione,
    StatoFattura,
    TipoEvento,
)
from irec.domain.porte import CanaleInvio, MessaggioUscita
from irec.domain.scheduler import (
    TEMPLATE_MAIL_MANDANTE_ESCALATION,
    TEMPLATE_MAIL_RECUPERO_CREDITI,
    InvioConsolidato,
    InvioDovuto,
    RecapitiCliente,
    consolida,
    escalation_imminente,
    in_escalation,
    motivo_salto,
    recapito_per,
)
from irec.domain.stati import puo_ricevere_sollecito


@dataclass
class EsitoInvii:
    """Conteggi del giro di invii: solo numeri e codici, nessuna PII."""

    pause_riprese: int = 0
    escalation_eseguite: int = 0
    escalation_imminenti: int = 0
    messaggi_inviati: int = 0
    comunicazioni_inviate: int = 0
    comunicazioni_saltate: int = 0
    comunicazioni_fallite: int = 0
    segnalazioni: list[str] = field(default_factory=list)


class MotoreInvii:
    """Esegue un giro di invii per un tenant a un dato istante."""

    def __init__(
        self,
        canale_invio: CanaleInvio,
        adesso: datetime,
        email_recupero_crediti: str,
    ):
        self._canale_invio = canale_invio
        self._adesso = adesso
        self._oggi: date = adesso.date()
        self._email_recupero_crediti = email_recupero_crediti

    def esegui(self, repo: TenantRepository) -> EsitoInvii:
        esito = EsitoInvii()
        self._riprendi_pause_scadute(repo, esito)
        self._gestisci_escalation(repo, esito)
        self._esegui_invii_dovuti(repo, esito)
        return esito

    # --- 1. promesse di pagamento non mantenute (PRD 5.2) ---

    def _riprendi_pause_scadute(
        self, repo: TenantRepository, esito: EsitoInvii
    ) -> None:
        in_pausa = repo.find(Fattura, Fattura.stato == StatoFattura.PAUSA)
        for fattura in in_pausa:
            if fattura.pausa_fino_a is None or fattura.pausa_fino_a > self._oggi:
                continue
            fattura.stato = StatoFattura.GESTIONE
            fattura.pausa_fino_a = None
            esito.pause_riprese += 1
            repo.log_event(
                TipoEvento.TRANSIZIONE_STATO,
                entita="fattura",
                entita_id=fattura.id,
                stato_precedente=str(StatoFattura.PAUSA),
                stato_successivo=str(StatoFattura.GESTIONE),
                dettaglio="promessa di pagamento scaduta senza incasso",
            )
        repo.flush()

    # --- 2. escalation T+45 e preavvisi T+44 (PRD 4.9, addendum 5.1) ---

    def _gestisci_escalation(self, repo: TenantRepository, esito: EsitoInvii) -> None:
        mandanti = repo.list(Mandante)
        mandante = mandanti[0] if mandanti else None
        for fattura in repo.find(Fattura, Fattura.stato == StatoFattura.GESTIONE):
            if escalation_imminente(self._oggi, fattura.data_scadenza):
                # Il preavviso "domani parte l'escalation" è una notifica
                # in-app: viene contato qui e recapitato dal modulo M6.
                esito.escalation_imminenti += 1
                continue
            if not in_escalation(self._oggi, fattura.data_scadenza):
                continue
            self._esegui_escalation(repo, fattura, mandante, esito)
        repo.flush()

    def _esegui_escalation(
        self,
        repo: TenantRepository,
        fattura: Fattura,
        mandante: Mandante | None,
        esito: EsitoInvii,
    ) -> None:
        numeri = (fattura.numero,)
        residuo = str(fattura.importo_residuo)

        self._canale_invio.invia(
            repo.tenant_id,
            MessaggioUscita(
                canale=Canale.EMAIL,
                destinatario=self._email_recupero_crediti,
                template=TEMPLATE_MAIL_RECUPERO_CREDITI,
                denominazione_destinatario="IREC Recupero Crediti",
                numeri_fattura=numeri,
                importo_totale_residuo=residuo,
            ),
        )
        if mandante is not None and mandante.alias_email:
            self._canale_invio.invia(
                repo.tenant_id,
                MessaggioUscita(
                    canale=Canale.EMAIL,
                    destinatario=mandante.alias_email,
                    template=TEMPLATE_MAIL_MANDANTE_ESCALATION,
                    denominazione_destinatario=mandante.ragione_sociale,
                    numeri_fattura=numeri,
                    importo_totale_residuo=residuo,
                ),
            )
        else:
            esito.segnalazioni.append(f"escalation_senza_mail_mandante:{fattura.numero}")

        fattura.stato = StatoFattura.INSOLUTO
        esito.escalation_eseguite += 1
        annullate = 0
        for comunicazione in fattura.comunicazioni:
            if comunicazione.stato is StatoComunicazione.PROGRAMMATA:
                comunicazione.stato = StatoComunicazione.ANNULLATA
                annullate += 1
        repo.log_event(
            TipoEvento.TRANSIZIONE_STATO,
            entita="fattura",
            entita_id=fattura.id,
            stato_precedente=str(StatoFattura.GESTIONE),
            stato_successivo=str(StatoFattura.INSOLUTO),
            dettaglio=f"escalation T+45, {annullate} comunicazioni annullate",
        )

    # --- 3. invii dovuti, con consolidamento (PRD 4.6, 5.3) ---

    def _esegui_invii_dovuti(self, repo: TenantRepository, esito: EsitoInvii) -> None:
        mandanti = repo.list(Mandante)
        if not mandanti:
            return
        pacchetto = mandanti[0].pacchetto
        clienti = {cliente.id: cliente for cliente in repo.list(ClienteFinale)}
        fatture = {fattura.id: fattura for fattura in repo.list(Fattura)}
        dovute = repo.find(
            Comunicazione,
            Comunicazione.stato == StatoComunicazione.PROGRAMMATA,
            Comunicazione.programmata_per <= self._adesso,
        )

        da_inviare: list[InvioDovuto] = []
        comunicazioni: dict[str, Comunicazione] = {}
        for comunicazione in dovute:
            fattura = fatture[comunicazione.fattura_id]
            # Controllo just-in-time (US-08): saldata/insoluta non si
            # sollecita mai; in Pausa lo step resta programmato e ripartirà
            # alla ripresa del flusso.
            if fattura.stato is StatoFattura.PAUSA:
                continue
            if not puo_ricevere_sollecito(fattura.stato):
                comunicazione.stato = StatoComunicazione.ANNULLATA
                continue

            cliente = clienti[fattura.cliente_id]
            recapiti = RecapitiCliente(
                email=cliente.email,
                pec=cliente.pec,
                telefono=cliente.telefono,
                canali_opt_out=frozenset(cliente.canali_opt_out),
            )
            motivo = motivo_salto(comunicazione.canale, pacchetto, recapiti)
            if motivo is not None:
                comunicazione.stato = StatoComunicazione.SALTATA
                comunicazione.esito_recapito = motivo
                esito.comunicazioni_saltate += 1
                esito.segnalazioni.append(
                    f"{motivo}:{comunicazione.canale}:{fattura.numero}"
                )
                continue

            comunicazioni[comunicazione.id] = comunicazione
            da_inviare.append(
                InvioDovuto(
                    comunicazione_id=comunicazione.id,
                    fattura_id=fattura.id,
                    cliente_id=cliente.id,
                    canale=comunicazione.canale,
                    template=comunicazione.template,
                    numero_fattura=fattura.numero,
                    importo_residuo=str(fattura.importo_residuo),
                )
            )

        for gruppo in consolida(da_inviare):
            self._invia_gruppo(repo, clienti[gruppo.cliente_id], gruppo, esito)
        repo.flush()

        for comunicazione in comunicazioni.values():
            if comunicazione.stato is StatoComunicazione.INVIATA:
                repo.log_event(
                    TipoEvento.COMUNICAZIONE,
                    entita="comunicazione",
                    entita_id=comunicazione.id,
                    dettaglio=f"{comunicazione.canale}:{comunicazione.template}",
                )
        repo.flush()

    def _invia_gruppo(
        self,
        repo: TenantRepository,
        cliente: ClienteFinale,
        gruppo: InvioConsolidato,
        esito: EsitoInvii,
    ) -> None:
        recapiti = RecapitiCliente(
            email=cliente.email,
            pec=cliente.pec,
            telefono=cliente.telefono,
            canali_opt_out=frozenset(cliente.canali_opt_out),
        )
        totale = sum(
            (Decimal(invio.importo_residuo) for invio in gruppo.invii), Decimal("0.00")
        )
        risposta = self._canale_invio.invia(
            repo.tenant_id,
            MessaggioUscita(
                canale=gruppo.canale,
                destinatario=recapito_per(gruppo.canale, recapiti),
                template=gruppo.template,
                denominazione_destinatario=cliente.denominazione,
                numeri_fattura=gruppo.numeri_fattura,
                importo_totale_residuo=str(totale),
            ),
        )
        if risposta.consegnato:
            esito.messaggi_inviati += 1
        stato = (
            StatoComunicazione.INVIATA
            if risposta.consegnato
            else StatoComunicazione.FALLITA
        )
        for invio in gruppo.invii:
            comunicazione = repo.get(Comunicazione, invio.comunicazione_id)
            assert comunicazione is not None
            comunicazione.stato = stato
            comunicazione.esito_recapito = risposta.dettaglio
            if risposta.consegnato:
                comunicazione.inviata_at = self._adesso
                esito.comunicazioni_inviate += 1
            else:
                esito.comunicazioni_fallite += 1
                esito.segnalazioni.append(
                    f"invio_fallito:{gruppo.canale}:{invio.numero_fattura}"
                )


def metti_in_pausa(
    repo: TenantRepository,
    fattura: Fattura,
    fino_a: date | None,
    operatore: str | None,
    motivo: str,
) -> None:
    """Sospende il flusso su una fattura (promessa di pagamento,
    contestazione, intervento manuale — PRD 5.2)."""
    stato_precedente = fattura.stato
    fattura.stato = StatoFattura.PAUSA
    fattura.pausa_fino_a = fino_a
    repo.log_event(
        TipoEvento.AZIONE_MANUALE,
        entita="fattura",
        entita_id=fattura.id,
        stato_precedente=str(stato_precedente),
        stato_successivo=str(StatoFattura.PAUSA),
        operatore=operatore,
        dettaglio=motivo,
    )


def ricalcola_schedule(repo: TenantRepository, fattura: Fattura) -> int:
    """Ricalcola gli step futuri dopo una modifica di scadenza (PRD 4.5).

    Aggiorna in place le comunicazioni ancora PROGRAMMATE (l'anti-doppio
    invio resta garantito dal vincolo per step); quelle già inviate
    restano storicizzate.
    """
    from irec.domain.scheduler import istante_step

    step_per_id = {
        step.id: step for flusso in repo.list(Flusso) for step in flusso.step
    }
    ricalcolate = 0
    for comunicazione in fattura.comunicazioni:
        if comunicazione.stato is not StatoComunicazione.PROGRAMMATA:
            continue
        step = step_per_id.get(comunicazione.step_id or "")
        if step is None:
            continue
        comunicazione.programmata_per = istante_step(
            fattura.data_scadenza, step.offset_giorni
        )
        ricalcolate += 1
    repo.flush()
    return ricalcolate
