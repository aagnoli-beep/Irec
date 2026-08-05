"""Flusso di sollecito di default, ancorato alla scadenza T (PRD 3.2).

È la sequenza standard assegnata a ogni mandante che non ha un flusso
personalizzato (i pacchetti Value/Premium potranno modificarla, M4/M5).
Gli step coprono le fasi 4-12 del processo; l'escalation a T+45
(fase 13) non è uno step di invio ma un trigger separato (M4).

Nessun IO: solo dati e calcolo puro delle date pianificate.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from irec.domain.enums import Canale


@dataclass(frozen=True)
class StepDefault:
    ordine: int
    offset_giorni: int  # rispetto a T (negativo = prima della scadenza)
    canale: Canale
    template: str


# PRD 3.2, fasi 4-12. La fase 11 (T+30, "WA o Agente vocale") usa WhatsApp
# come canale di default; la regola dinamica è del pacchetto Premium (M4).
FLUSSO_DEFAULT: tuple[StepDefault, ...] = (
    StepDefault(1, -2, Canale.EMAIL, "promemoria_scadenza"),
    StepDefault(2, 3, Canale.EMAIL, "sollecito_1"),
    StepDefault(3, 6, Canale.WHATSAPP, "sollecito_2"),
    StepDefault(4, 9, Canale.VOICE, "sollecito_3"),
    StepDefault(5, 15, Canale.EMAIL, "sollecito_4"),
    StepDefault(6, 18, Canale.VOICE, "sollecito_5"),
    StepDefault(7, 25, Canale.PEC, "sollecito_6"),
    StepDefault(8, 30, Canale.WHATSAPP, "sollecito_7"),
    StepDefault(9, 35, Canale.PEC, "sollecito_8"),
)

# A T+45 la pratica esce dal perimetro: mail a Recupero Crediti e al
# mandante, fattura → Insoluto (PRD 4.9). Gestita in M4.
ESCALATION_OFFSET_GIORNI = 45

# Ora di invio pianificata. M4 introdurrà le regole di calendario vere:
# niente festivi, finestra <= 18:00, consolidamento per cliente/giorno.
ORA_INVIO_DEFAULT = time(10, 0, tzinfo=UTC)

NOME_FLUSSO_DEFAULT = "Flusso standard IREC"


def data_invio_pianificata(scadenza: date, offset_giorni: int) -> datetime:
    """Data/ora pianificata di uno step per una fattura con scadenza T."""
    giorno = scadenza + timedelta(days=offset_giorni)
    return datetime.combine(giorno, ORA_INVIO_DEFAULT)
