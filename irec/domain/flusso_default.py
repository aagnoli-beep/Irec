"""Flusso di sollecito di default, ancorato alla scadenza T (PRD 3.2).

È la sequenza standard assegnata a ogni mandante che non ha un flusso
personalizzato (i pacchetti Value/Premium potranno modificarla, M4/M5).
Gli step coprono le fasi 4-12 del processo; l'escalation a T+45
(fase 13) non è uno step di invio ma un trigger separato (M4).

Nessun IO: solo dati e calcolo puro delle date pianificate.
"""

from dataclasses import dataclass
from datetime import date, datetime

from irec.domain.enums import Canale
from irec.domain.scheduler import istante_step


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

NOME_FLUSSO_DEFAULT = "Flusso standard IREC"


def data_invio_pianificata(scadenza: date, offset_giorni: int) -> datetime:
    """Data/ora pianificata di uno step per una fattura con scadenza T.

    Applica già le regole di calendario (festivi, weekend, finestra
    oraria): delega a `irec.domain.scheduler.istante_step`.
    """
    return istante_step(scadenza, offset_giorni)
