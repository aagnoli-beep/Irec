"""Composizione del brief giornaliero (addendum §5.2). Dominio puro.

Il brief espone solo numeri e conteggi: il *tono* (aprire sereno, positivo
per primo, azioni come invito e non come allarme) lo mette l'LLM di Mind a
partire da questi dati. Qui c'è la struttura: KPI, la quota già recuperata
e le azioni ordinate per priorità con un tetto rigido di 2-3, oltre il
quale il resto confluisce in "e altre N".
"""

from dataclasses import dataclass
from decimal import Decimal

from irec.domain.enums import PRIORITA_NOTIFICHE, TipoNotifica

# Tetto di azioni mostrate nel brief prima del "e altre N" (addendum §5.2).
MAX_AZIONI_BRIEF = 3


@dataclass(frozen=True)
class VoceBrief:
    """Una notifica ridotta a ciò che serve al brief: tipo e quante volte."""

    tipo: TipoNotifica
    quante: int


@dataclass(frozen=True)
class Brief:
    """Il brief giornaliero pronto per essere narrato dall'LLM di Mind.

    Solo numeri e codici: l'LLM ci mette il tono. Gli importi sono
    stringhe decimali (mai float).
    """

    affidato: str
    recuperato: str
    da_recuperare: str
    passato_a_recupero: str
    azioni_principali: tuple[VoceBrief, ...]
    altre_azioni: int
    # Quota di portafoglio già recuperata (0-100): il "positivo per primo".
    percentuale_recuperato: int


def percentuale_recuperato(affidato: Decimal, recuperato: Decimal) -> int:
    """Quota di portafoglio già recuperata (0-100)."""
    if affidato <= 0:
        return 0
    return int((recuperato / affidato) * 100)


def componi_brief(
    affidato: Decimal,
    recuperato: Decimal,
    da_recuperare: Decimal,
    passato_a_recupero: Decimal,
    notifiche_per_tipo: dict[TipoNotifica, int],
) -> Brief:
    """Compone il brief dai KPI e dal conteggio delle notifiche non lette.

    Le azioni sono ordinate per priorità (PRIORITA_NOTIFICHE); oltre il
    tetto, il resto confluisce in `altre_azioni`.
    """
    voci = [
        VoceBrief(tipo=tipo, quante=notifiche_per_tipo[tipo])
        for tipo in PRIORITA_NOTIFICHE
        if notifiche_per_tipo.get(tipo, 0) > 0
    ]
    principali = tuple(voci[:MAX_AZIONI_BRIEF])
    altre = sum(voce.quante for voce in voci[MAX_AZIONI_BRIEF:])
    return Brief(
        affidato=str(affidato),
        recuperato=str(recuperato),
        da_recuperare=str(da_recuperare),
        passato_a_recupero=str(passato_a_recupero),
        azioni_principali=principali,
        altre_azioni=altre,
        percentuale_recuperato=percentuale_recuperato(affidato, recuperato),
    )
