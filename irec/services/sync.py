"""Ciclo giornaliero di sincronizzazione (ROADMAP M3, fasi 1-4 del flusso).

Per un tenant: verifica collegamenti → recupero fatture e movimenti →
riconciliazione → applicazione degli esiti al modello dati. Rieseguibile
in sicurezza: fatture già importate e pagamenti già registrati vengono
riconosciuti e saltati (idempotenza).

Il calcolo dello schedule usa il flusso di default (M4 introdurrà regole
di calendario, canali per pacchetto e consolidamento).
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
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
    OriginePagamento,
    StatoComunicazione,
    StatoFattura,
    StatoPosizione,
    TipoEvento,
)
from irec.domain.flusso_default import (
    FLUSSO_DEFAULT,
    NOME_FLUSSO_DEFAULT,
    data_invio_pianificata,
)
from irec.domain.porte import (
    ErroreCollegamento,
    FatturaEsterna,
    FattureProvider,
    MovimentiProvider,
    MovimentoBancario,
    PagamentoRilevato,
    Riconciliatore,
    StatoCollegamento,
)
from irec.domain.stati import (
    puo_ricevere_sollecito,
    residuo_dopo_incasso,
    stato_dopo_pagamento,
    stato_posizione,
)

logger = logging.getLogger("irec.sync")

# Profondità di recupero (lookback): parametrizzabile per chiamata, come
# richiesto dal PRD Cassetto Fiscale §3 ("15, 30 o 60 giorni di storico").
LOOKBACK_GIORNI_DEFAULT = 30


@dataclass
class EsitoSincronizzazione:
    """Riepilogo di una run: solo conteggi e codici, nessuna PII."""

    collegamento_ade: str = StatoCollegamento.NON_CONFIGURATO
    consenso_psd2: str = StatoCollegamento.NON_CONFIGURATO
    fatture_recuperate: int = 0
    fatture_importate: int = 0
    clienti_creati: int = 0
    comunicazioni_programmate: int = 0
    movimenti_recuperati: int = 0
    pagamenti_registrati: int = 0
    fatture_saldate: int = 0
    comunicazioni_annullate: int = 0
    posizioni_chiuse: int = 0
    anomalie: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "collegamento_ade": str(self.collegamento_ade),
            "consenso_psd2": str(self.consenso_psd2),
            "fatture_recuperate": self.fatture_recuperate,
            "fatture_importate": self.fatture_importate,
            "clienti_creati": self.clienti_creati,
            "comunicazioni_programmate": self.comunicazioni_programmate,
            "movimenti_recuperati": self.movimenti_recuperati,
            "pagamenti_registrati": self.pagamenti_registrati,
            "fatture_saldate": self.fatture_saldate,
            "comunicazioni_annullate": self.comunicazioni_annullate,
            "posizioni_chiuse": self.posizioni_chiuse,
            "anomalie": list(self.anomalie),
        }


class CicloSincronizzazione:
    """Orchestratore del ciclo: dipende solo dalle porte e dal repository."""

    def __init__(
        self,
        fatture_provider: FattureProvider,
        movimenti_provider: MovimentiProvider,
        riconciliatore: Riconciliatore,
        oggi: date,
        lookback_giorni: int = LOOKBACK_GIORNI_DEFAULT,
    ):
        self._fatture_provider = fatture_provider
        self._movimenti_provider = movimenti_provider
        self._riconciliatore = riconciliatore
        self._oggi = oggi
        self._lookback = timedelta(days=lookback_giorni)

    def esegui(self, repo: TenantRepository) -> EsitoSincronizzazione:
        esito = EsitoSincronizzazione()

        mandante = self._mandante(repo)
        if mandante is None:
            esito.anomalie.append("mandante_non_censito")
            return esito

        self._importa_fatture(repo, mandante, esito)
        self._riconcilia(repo, esito)
        self._aggiorna_posizioni(repo, esito)

        repo.log_event(
            TipoEvento.SINCRONIZZAZIONE,
            entita="tenant",
            entita_id=repo.tenant_id,
            dettaglio=(
                f"fatture importate: {esito.fatture_importate}, "
                f"pagamenti: {esito.pagamenti_registrati}, "
                f"saldate: {esito.fatture_saldate}, "
                f"anomalie: {len(esito.anomalie)}"
            ),
        )
        return esito

    # --- fase 1-2: collegamenti e recupero fatture ---

    def _mandante(self, repo: TenantRepository) -> Mandante | None:
        mandanti = repo.list(Mandante)
        return mandanti[0] if mandanti else None

    def _importa_fatture(
        self, repo: TenantRepository, mandante: Mandante, esito: EsitoSincronizzazione
    ) -> None:
        try:
            collegamento = self._fatture_provider.stato_collegamento(repo.tenant_id)
        except ErroreCollegamento as errore:
            collegamento = errore.collegamento
        esito.collegamento_ade = collegamento.stato
        if not collegamento.attivo:
            # Il collegamento caduto non è un errore della run: è lo stato
            # che scatenerà la notifica proattiva all'utente (M6).
            return

        fatture = self._fatture_provider.recupera_fatture(
            repo.tenant_id, self._oggi - self._lookback, self._oggi
        )
        esito.fatture_recuperate = len(fatture)
        if not fatture:
            return

        flusso = self._flusso_default(repo, mandante)
        # Stato del tenant caricato UNA volta prima del loop e aggiornato
        # incrementalmente: niente full-scan per ogni fattura del lotto.
        clienti = {cliente.piva_cf: cliente for cliente in repo.list(ClienteFinale)}
        esistenti = {
            (fattura.cliente_id, fattura.numero) for fattura in repo.list(Fattura)
        }
        posizioni_aperte = {
            posizione.cliente_id: posizione
            for posizione in repo.find(
                Posizione, Posizione.stato == StatoPosizione.APERTA
            )
        }

        for esterna in fatture:
            cliente = clienti.get(esterna.piva_cf_debitore)
            if cliente is None:
                cliente = repo.add(
                    ClienteFinale(
                        mandante_id=mandante.id,
                        denominazione=esterna.denominazione_debitore,
                        piva_cf=esterna.piva_cf_debitore,
                        email=esterna.email_debitore,
                        pec=esterna.pec_debitore,
                        telefono=esterna.telefono_debitore,
                    )
                )
                repo.flush()
                clienti[cliente.piva_cf] = cliente
                esito.clienti_creati += 1

            if (cliente.id, esterna.numero) in esistenti:
                continue  # reimport: già a sistema

            posizione = posizioni_aperte.get(cliente.id)
            if posizione is None:
                posizione = repo.add(Posizione(cliente_id=cliente.id))
                repo.flush()
                posizioni_aperte[cliente.id] = posizione
            fattura = repo.add(
                Fattura(
                    posizione_id=posizione.id,
                    cliente_id=cliente.id,
                    numero=esterna.numero,
                    data_emissione=esterna.data_emissione,
                    data_scadenza=esterna.data_scadenza,
                    importo=esterna.importo,
                    importo_residuo=esterna.importo,
                )
            )
            repo.flush()
            esistenti.add((cliente.id, esterna.numero))
            esito.fatture_importate += 1

            if esterna.data_scadenza < self._oggi:
                # PRD 4.1: le fatture entrano non ancora scadute; una
                # scadenza già superata è un'anomalia di dato da segnalare
                # all'operatore, non un caso da gestire in automatico.
                esito.anomalie.append(f"fattura_gia_scaduta:{esterna.numero}")

            esito.comunicazioni_programmate += self._programma_schedule(
                repo, flusso, fattura
            )

            repo.log_event(
                TipoEvento.TRANSIZIONE_STATO,
                entita="fattura",
                entita_id=fattura.id,
                stato_successivo=str(StatoFattura.GESTIONE),
                dettaglio="import dal cassetto fiscale",
            )

    def _flusso_default(self, repo: TenantRepository, mandante: Mandante) -> Flusso:
        """Il flusso standard del mandante, creato al primo ciclo."""
        for flusso in repo.list(Flusso):
            if flusso.attivo:
                return flusso
        flusso = repo.add(Flusso(mandante_id=mandante.id, nome=NOME_FLUSSO_DEFAULT))
        repo.flush()
        for step in FLUSSO_DEFAULT:
            repo.add(
                FlussoStep(
                    flusso_id=flusso.id,
                    ordine=step.ordine,
                    offset_giorni=step.offset_giorni,
                    canale=step.canale,
                    template=step.template,
                )
            )
        repo.flush()
        return flusso

    def _programma_schedule(
        self, repo: TenantRepository, flusso: Flusso, fattura: Fattura
    ) -> int:
        """Una comunicazione programmata per ogni step del flusso.

        Il vincolo `uq_comunicazione_fattura_step` rende l'operazione
        idempotente a livello di database (anti-doppio invio, PRD 5.3).
        """
        programmate = 0
        for step in flusso.step:
            repo.add(
                Comunicazione(
                    fattura_id=fattura.id,
                    step_id=step.id,
                    canale=step.canale,
                    template=step.template,
                    programmata_per=data_invio_pianificata(
                        fattura.data_scadenza, step.offset_giorni
                    ),
                )
            )
            programmate += 1
        repo.flush()
        return programmate

    # --- fase 3-4: movimenti, riconciliazione, applicazione esiti ---

    def _riconcilia(self, repo: TenantRepository, esito: EsitoSincronizzazione) -> None:
        try:
            consenso = self._movimenti_provider.stato_consenso(repo.tenant_id)
        except ErroreCollegamento as errore:
            consenso = errore.collegamento
        esito.consenso_psd2 = consenso.stato
        if not consenso.attivo:
            # Le fatture importate restano valide: salta solo l'incasso.
            return

        movimenti = self._movimenti_provider.recupera_movimenti(
            repo.tenant_id, self._oggi - self._lookback, self._oggi
        )
        esito.movimenti_recuperati = len(movimenti)
        if not movimenti:
            return

        aperte, indice = self._fatture_aperte(repo)
        if not aperte:
            return

        # I pagamenti del tenant, caricati UNA volta per l'intera fase:
        # servono sia alla riduzione dei movimenti sia al dedup in
        # _applica_pagamento (che aggiorna il set incrementalmente).
        pagamenti = repo.list(Pagamento)
        chiavi_registrate = {pagamento.chiave_idempotenza for pagamento in pagamenti}

        movimenti_residui = self._movimenti_non_ancora_allocati(pagamenti, movimenti)
        if not movimenti_residui:
            return

        risultato = self._riconciliatore.riconcilia(aperte, movimenti_residui)
        for pagamento in risultato.pagamenti:
            self._applica_pagamento(repo, indice, chiavi_registrate, pagamento, esito)

    def _movimenti_non_ancora_allocati(
        self, pagamenti: list[Pagamento], movimenti: list[MovimentoBancario]
    ) -> list[MovimentoBancario]:
        """Riduce ogni movimento di quanto già registrato nei run precedenti.

        Il riconciliatore è stateless e il lookback ripresenta i movimenti
        storici: senza questa riduzione un bonifico già interamente
        allocato verrebbe riusato sulle fatture rimaste aperte,
        registrando incassi mai avvenuti.
        """
        residui: list[MovimentoBancario] = []
        for movimento in movimenti:
            allocato = sum(
                (
                    pagamento.importo
                    for pagamento in pagamenti
                    if pagamento.chiave_idempotenza.startswith(
                        f"{movimento.id_movimento}:"
                    )
                ),
                Decimal("0.00"),
            )
            disponibile = movimento.importo - allocato
            if disponibile > 0:
                residui.append(replace(movimento, importo=disponibile))
        return residui

    def _fatture_aperte(
        self, repo: TenantRepository
    ) -> tuple[list[FatturaEsterna], dict[tuple[str, str], Fattura]]:
        """Le fatture ancora da incassare, nel formato del riconciliatore."""
        clienti = {cliente.id: cliente for cliente in repo.list(ClienteFinale)}
        aperte: list[FatturaEsterna] = []
        indice: dict[tuple[str, str], Fattura] = {}
        for fattura in repo.list(Fattura):
            if fattura.stato in (StatoFattura.SALDATA, StatoFattura.INSOLUTO):
                continue
            cliente = clienti[fattura.cliente_id]
            aperte.append(
                FatturaEsterna(
                    numero=fattura.numero,
                    piva_cf_debitore=cliente.piva_cf,
                    denominazione_debitore=cliente.denominazione,
                    data_emissione=fattura.data_emissione,
                    data_scadenza=fattura.data_scadenza,
                    # Il riconciliatore ragiona sul dovuto residuo, non
                    # sull'importo originale: i parziali già registrati
                    # non vanno ri-abbinati.
                    importo=fattura.importo_residuo,
                )
            )
            indice[(cliente.piva_cf, fattura.numero)] = fattura
        return aperte, indice

    def _applica_pagamento(
        self,
        repo: TenantRepository,
        indice: dict[tuple[str, str], Fattura],
        chiavi_registrate: set[str],
        rilevato: PagamentoRilevato,
        esito: EsitoSincronizzazione,
    ) -> None:
        fattura = indice.get((rilevato.piva_cf_debitore, rilevato.numero_fattura))
        if fattura is None:
            esito.anomalie.append(f"pagamento_senza_fattura:{rilevato.id_movimento}")
            return

        # Idempotenza fra le fonti: la chiave identifica l'abbinamento
        # movimento→fattura. Un ri-run della sincronizzazione (o una
        # registrazione manuale dello stesso incasso) non conta due volte.
        chiave = f"{rilevato.id_movimento}:{rilevato.numero_fattura}"
        if chiave in chiavi_registrate:
            return
        chiavi_registrate.add(chiave)

        repo.add(
            Pagamento(
                fattura_id=fattura.id,
                importo=rilevato.importo_pagato,
                data_pagamento=self._oggi,
                origine=OriginePagamento.RICONCILIAZIONE,
                chiave_idempotenza=chiave,
            )
        )
        stato_precedente = fattura.stato
        fattura.importo_residuo = residuo_dopo_incasso(
            fattura.importo_residuo, rilevato.importo_pagato
        )
        fattura.stato = stato_dopo_pagamento(fattura.stato, fattura.importo_residuo)
        esito.pagamenti_registrati += 1

        repo.log_event(
            TipoEvento.PAGAMENTO,
            entita="fattura",
            entita_id=fattura.id,
            stato_precedente=str(stato_precedente),
            stato_successivo=str(fattura.stato),
            dettaglio=f"riconciliazione, chiave {chiave}",
        )

        if not puo_ricevere_sollecito(fattura.stato):
            esito.comunicazioni_annullate += self._annulla_solleciti(repo, fattura)
            if fattura.stato is StatoFattura.SALDATA:
                esito.fatture_saldate += 1

    def _annulla_solleciti(self, repo: TenantRepository, fattura: Fattura) -> int:
        """US-08: mai sollecitare chi ha pagato."""
        annullate = 0
        for comunicazione in fattura.comunicazioni:
            if comunicazione.stato is StatoComunicazione.PROGRAMMATA:
                comunicazione.stato = StatoComunicazione.ANNULLATA
                annullate += 1
        repo.flush()
        return annullate

    def _aggiorna_posizioni(
        self, repo: TenantRepository, esito: EsitoSincronizzazione
    ) -> None:
        # Stati raggruppati in un passaggio unico: evita il lazy-load N+1
        # di posizione.fatture dentro il loop.
        stati_per_posizione: dict[str, list[StatoFattura]] = {}
        for fattura in repo.list(Fattura):
            stati_per_posizione.setdefault(fattura.posizione_id, []).append(
                fattura.stato
            )
        for posizione in repo.list(Posizione):
            stati = stati_per_posizione.get(posizione.id, [])
            nuovo = stato_posizione(stati)
            if nuovo is StatoPosizione.CHIUSA and posizione.stato is not nuovo:
                posizione.stato = nuovo
                esito.posizioni_chiuse += 1
                repo.log_event(
                    TipoEvento.TRANSIZIONE_STATO,
                    entita="posizione",
                    entita_id=posizione.id,
                    stato_precedente=str(StatoPosizione.APERTA),
                    stato_successivo=str(nuovo),
                )
        repo.flush()
