from enum import StrEnum


class Pacchetto(StrEnum):
    """Livello di servizio del mandante: governa canali e personalizzazione."""

    ENTRY = "entry"
    VALUE = "value"
    PREMIUM = "premium"


class Canale(StrEnum):
    EMAIL = "email"
    PEC = "pec"
    WHATSAPP = "whatsapp"
    VOICE = "voice"


# Canali abilitati per pacchetto (PRD 1.3): Entry email/PEC, Value +WhatsApp,
# Premium +agente vocale.
CANALI_PER_PACCHETTO: dict[Pacchetto, frozenset[Canale]] = {
    Pacchetto.ENTRY: frozenset({Canale.EMAIL, Canale.PEC}),
    Pacchetto.VALUE: frozenset({Canale.EMAIL, Canale.PEC, Canale.WHATSAPP}),
    Pacchetto.PREMIUM: frozenset(
        {Canale.EMAIL, Canale.PEC, Canale.WHATSAPP, Canale.VOICE}
    ),
}


class StatoFattura(StrEnum):
    """Stati operativi della fattura (PRD 2.4)."""

    GESTIONE = "gestione"
    PAUSA = "pausa"
    SALDATA = "saldata"
    INSOLUTO = "insoluto"


class StatoPosizione(StrEnum):
    """La posizione è chiusa quando tutte le sue fatture sono saldate (PRD 2.3)."""

    APERTA = "aperta"
    CHIUSA = "chiusa"


class StatoComunicazione(StrEnum):
    """Stati di una comunicazione (PRD 4.6): saltata = canale non
    utilizzabile (pacchetto/recapito/opt-out), il flusso prosegue."""

    PROGRAMMATA = "programmata"
    INVIATA = "inviata"
    ANNULLATA = "annullata"
    FALLITA = "fallita"
    SALTATA = "saltata"


class OriginePagamento(StrEnum):
    """Da dove arriva un pagamento: serve per l'idempotenza fra le due fonti."""

    RICONCILIAZIONE = "riconciliazione"
    MANUALE = "manuale"


class StatoRun(StrEnum):
    """Stati di una run di sincronizzazione (contratto /v1: valori inglesi)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TipoEvento(StrEnum):
    """Eventi dell'audit trail (PRD 5.4): storico immutabile."""

    TRANSIZIONE_STATO = "transizione_stato"
    AZIONE_MANUALE = "azione_manuale"
    COMUNICAZIONE = "comunicazione"
    PAGAMENTO = "pagamento"
    SINCRONIZZAZIONE = "sincronizzazione"
