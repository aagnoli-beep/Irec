"""Regole pure del motore solleciti: canali, consolidamento, escalation.

- Il TIMING è per fattura (offset su T), l'INVIO è per cliente:
  le comunicazioni dovute lo stesso giorno allo stesso cliente sullo
  stesso canale sono unite in un messaggio unico (PRD 2.2, 5.3).
- I canali dipendono dal pacchetto (PRD 1.3); uno step su canale non
  abilitato o senza recapito viene saltato e segnalato, il flusso
  prosegue (PRD 4.6.2).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from irec.domain.calendario import applica_finestra, istante_invio
from irec.domain.enums import CANALI_PER_PACCHETTO, Canale, Pacchetto

# Escalation a Recupero Crediti (PRD 4.9): la fattura esce dal perimetro.
ESCALATION_OFFSET_GIORNI = 45
# Preavviso proattivo il giorno prima: silenzio = consenso (addendum §5.1).
PREAVVISO_ESCALATION_GIORNI = 44

TEMPLATE_MAIL_RECUPERO_CREDITI = "mail_standard_4"
TEMPLATE_MAIL_MANDANTE_ESCALATION = "mail_standard_5"


@dataclass(frozen=True)
class RecapitiCliente:
    """I recapiti che servono a decidere se un canale è utilizzabile."""

    email: str | None = None
    pec: str | None = None
    telefono: str | None = None
    canali_opt_out: frozenset[str] = frozenset()


_RECAPITO_PER_CANALE = {
    Canale.EMAIL: "email",
    Canale.PEC: "pec",
    Canale.WHATSAPP: "telefono",
    Canale.VOICE: "telefono",
}

MOTIVO_CANALE_NON_NEL_PACCHETTO = "canale_non_nel_pacchetto"
MOTIVO_RECAPITO_MANCANTE = "recapito_mancante"
MOTIVO_OPT_OUT = "opt_out"


def motivo_salto(
    canale: Canale, pacchetto: Pacchetto, recapiti: RecapitiCliente
) -> str | None:
    """Perché uno step su questo canale NON va inviato, o None se si invia.

    PRD 4.6.2: lo step saltato non blocca il flusso; il motivo viene
    segnalato all'operatore.
    """
    if canale not in CANALI_PER_PACCHETTO[pacchetto]:
        return MOTIVO_CANALE_NON_NEL_PACCHETTO
    if canale.value in recapiti.canali_opt_out:
        return MOTIVO_OPT_OUT
    if not getattr(recapiti, _RECAPITO_PER_CANALE[canale]):
        return MOTIVO_RECAPITO_MANCANTE
    return None


def recapito_per(canale: Canale, recapiti: RecapitiCliente) -> str:
    valore = getattr(recapiti, _RECAPITO_PER_CANALE[canale])
    assert valore  # garantito da motivo_salto == None
    return str(valore)


def istante_step(scadenza: date, offset_giorni: int) -> datetime:
    """Istante pianificato di uno step: T+offset alle 10:00 italiane,
    già spostato nella prima finestra utile (festivi, weekend)."""
    return applica_finestra(istante_invio(scadenza + timedelta(days=offset_giorni)))


@dataclass(frozen=True)
class InvioDovuto:
    """Una comunicazione pronta all'invio, prima del consolidamento."""

    comunicazione_id: str
    fattura_id: str
    cliente_id: str
    canale: Canale
    template: str
    numero_fattura: str
    importo_residuo: str  # stringa decimale: il messaggio è testo


@dataclass(frozen=True)
class InvioConsolidato:
    """Il messaggio unico per cliente/canale: elenca tutte le fatture
    dovute in questo giro (PRD 2.2)."""

    cliente_id: str
    canale: Canale
    template: str
    invii: tuple[InvioDovuto, ...]

    @property
    def numeri_fattura(self) -> tuple[str, ...]:
        return tuple(invio.numero_fattura for invio in self.invii)


def consolida(invii: list[InvioDovuto]) -> list[InvioConsolidato]:
    """Un solo messaggio per cliente/canale per questo giro di invii.

    Il template del messaggio consolidato è quello dello step più
    avanzato del gruppo (il tono non deve regredire); l'ordine dei
    gruppi è deterministico.
    """
    gruppi: dict[tuple[str, Canale], list[InvioDovuto]] = {}
    for invio in invii:
        gruppi.setdefault((invio.cliente_id, invio.canale), []).append(invio)
    consolidati = []
    for (cliente_id, canale), gruppo in sorted(
        gruppi.items(), key=lambda voce: (voce[0][0], voce[0][1])
    ):
        ordinato = tuple(sorted(gruppo, key=lambda invio: invio.numero_fattura))
        consolidati.append(
            InvioConsolidato(
                cliente_id=cliente_id,
                canale=canale,
                template=gruppo[-1].template,
                invii=ordinato,
            )
        )
    return consolidati


def giorni_da_scadenza(oggi: date, scadenza: date) -> int:
    return (oggi - scadenza).days


def in_escalation(oggi: date, scadenza: date) -> bool:
    """A T+45 la pratica passa a Recupero Crediti (PRD 4.9)."""
    return giorni_da_scadenza(oggi, scadenza) >= ESCALATION_OFFSET_GIORNI


def escalation_imminente(oggi: date, scadenza: date) -> bool:
    """A T+44 il preavviso: domani parte l'escalation (addendum §5.1)."""
    return giorni_da_scadenza(oggi, scadenza) == PREAVVISO_ESCALATION_GIORNI
