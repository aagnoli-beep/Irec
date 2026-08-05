"""Porte verso i tre microservizi esterni.

Il dominio e il ciclo giornaliero dipendono SOLO da queste interfacce:
in M2 le implementano i mock (`irec/adapters/mock/`), in M8 gli adapter
reali. Nessun IO qui: solo dataclass e Protocol.

I tre servizi (docs/ARCHITECTURE.md §2):
- **FattureProvider** — microservizio Cassetto Fiscale (AdE/SDI): fatture
  elettroniche + stato del collegamento (delega), da verificare ogni giorno.
- **MovimentiProvider** — microservizio Banche (Fabrick/PSD2): entrate e
  uscite + stato del consenso, che scade e va riautorizzato.
- **Riconciliatore** — microservizio di riconciliazione: riceve fatture e
  movimenti, restituisce fatture pagate / da pagare.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from irec.domain.enums import Canale


class StatoCollegamento(StrEnum):
    """Stato di un collegamento esterno (delega AdE o consenso PSD2)."""

    ATTIVO = "attivo"
    NON_CONFIGURATO = "non_configurato"
    SCADUTO = "scaduto"
    ERRORE = "errore"


@dataclass(frozen=True)
class CollegamentoEsterno:
    """Esito del check quotidiano su un collegamento."""

    stato: StatoCollegamento
    # Per il consenso PSD2: quando andrà riautorizzato. Serve alle
    # notifiche proattive prima della scadenza.
    scade_il: date | None = None
    dettaglio: str | None = None

    @property
    def attivo(self) -> bool:
        return self.stato is StatoCollegamento.ATTIVO


class ErroreCollegamento(Exception):
    """Il collegamento esterno non è utilizzabile: niente dati da questo giro.

    Non è un bug: è lo stato che scatena la notifica proattiva all'utente
    con la guida al rinnovo (delega AdE / consenso PSD2).
    """

    def __init__(self, collegamento: CollegamentoEsterno):
        self.collegamento = collegamento
        super().__init__(f"collegamento {collegamento.stato}")


@dataclass(frozen=True)
class FatturaEsterna:
    """Fattura come arriva dal cassetto fiscale (già decodificata dall'XML).

    Assunzioni del contratto (da validare con le spec reali in M8):
    - la coppia `(piva_cf_debitore, numero)` è univoca nel lotto;
    - gli importi sono in EUR (nessun campo valuta in MVP).
    """

    numero: str
    piva_cf_debitore: str
    denominazione_debitore: str
    data_emissione: date
    data_scadenza: date
    importo: Decimal
    email_debitore: str | None = None
    pec_debitore: str | None = None
    telefono_debitore: str | None = None


@dataclass(frozen=True)
class MovimentoBancario:
    """Movimento come arriva dall'API della banca."""

    id_movimento: str
    data: date
    importo: Decimal
    descrizione: str
    piva_cf_controparte: str | None = None


@dataclass(frozen=True)
class PagamentoRilevato:
    """Un abbinamento movimento→fattura prodotto dal riconciliatore.

    `importo_pagato` può essere inferiore all'importo della fattura
    (pagamento parziale) e un movimento può coprire più fatture.
    """

    numero_fattura: str
    piva_cf_debitore: str
    id_movimento: str
    importo_pagato: Decimal


@dataclass(frozen=True)
class EsitoRiconciliazione:
    """Output del riconciliatore: cosa è stato pagato e cosa resta aperto."""

    pagamenti: tuple[PagamentoRilevato, ...]
    fatture_da_pagare: tuple[FatturaEsterna, ...]
    movimenti_non_riconciliati: tuple[MovimentoBancario, ...]


@runtime_checkable
class FattureProvider(Protocol):
    """Microservizio Cassetto Fiscale (AdE/SDI)."""

    def stato_collegamento(self, tenant_id: str) -> CollegamentoEsterno:
        """Stato della delega AdE del mandante (check quotidiano).

        Solleva ErroreCollegamento se il tenant è sconosciuto al servizio.
        """
        ...

    def recupera_fatture(
        self, tenant_id: str, dal: date, al: date
    ) -> list[FatturaEsterna]:
        """Fatture disponibili nell'intervallo. Solleva ErroreCollegamento
        se la delega non è attiva."""
        ...


@runtime_checkable
class MovimentiProvider(Protocol):
    """Microservizio Banche (Fabrick/PSD2)."""

    def stato_consenso(self, tenant_id: str) -> CollegamentoEsterno:
        """Stato del consenso PSD2 del mandante (check quotidiano).

        Solleva ErroreCollegamento se il tenant è sconosciuto al servizio.
        """
        ...

    def recupera_movimenti(
        self, tenant_id: str, dal: date, al: date
    ) -> list[MovimentoBancario]:
        """Movimenti nell'intervallo. Solleva ErroreCollegamento se il
        consenso è scaduto o revocato."""
        ...


@dataclass(frozen=True)
class MessaggioUscita:
    """Messaggio consolidato verso il debitore (o verso Irec/mandante per
    l'escalation), pronto per il canale di invio."""

    canale: "Canale"
    destinatario: str  # email / PEC / numero, a seconda del canale
    template: str
    denominazione_destinatario: str
    numeri_fattura: tuple[str, ...]
    importo_totale_residuo: str  # stringa decimale


@dataclass(frozen=True)
class EsitoInvio:
    consegnato: bool
    dettaglio: str | None = None


@runtime_checkable
class CanaleInvio(Protocol):
    """Servizio di recapito (email/PEC/WhatsApp/voice).

    La titolarità reale dei canali è un punto aperto (docs/ARCHITECTURE
    §8): mock in M4, adapter reali in M8. Un fallimento di recapito è un
    `EsitoInvio(consegnato=False)`, non un'eccezione.
    """

    def invia(self, tenant_id: str, messaggio: MessaggioUscita) -> EsitoInvio: ...


@runtime_checkable
class Riconciliatore(Protocol):
    """Microservizio di riconciliazione incassi↔fatture."""

    def riconcilia(
        self,
        fatture: list[FatturaEsterna],
        movimenti: list[MovimentoBancario],
    ) -> EsitoRiconciliazione:
        """Abbina i movimenti alle fatture e restituisce l'esito completo."""
        ...
