"""Regole pure di transizione di stato per fatture e posizioni (PRD 2.3-2.4, 5.2).

Funzioni senza IO: decidono lo stato risultante, non lo persistono.
"""

from decimal import Decimal

from irec.domain.enums import StatoFattura, StatoPosizione

# Stati da cui la fattura è uscita dal flusso di sollecito: non tornano indietro
# per effetto di un pagamento parziale o di un ricalcolo.
STATI_TERMINALI: frozenset[StatoFattura] = frozenset(
    {StatoFattura.SALDATA, StatoFattura.INSOLUTO}
)

# Stati in cui una comunicazione programmata NON deve partire: è il controllo
# just-in-time pre-invio (PRD 4.6, US-08).
STATI_NON_SOLLECITABILI: frozenset[StatoFattura] = frozenset(
    {StatoFattura.SALDATA, StatoFattura.PAUSA, StatoFattura.INSOLUTO}
)


class TransizioneNonValida(ValueError):
    """Transizione di stato non ammessa dalle regole di dominio."""


def stato_dopo_pagamento(
    stato_corrente: StatoFattura, importo_residuo: Decimal
) -> StatoFattura:
    """Stato della fattura dopo l'aggiornamento del residuo.

    Residuo azzerato (o negativo, in caso di sovra-pagamento) → Saldata.
    Pagamento parziale → la fattura resta dov'è: il flusso prosegue con
    l'importo aggiornato, salvo pausa manuale (PRD 5.2).

    Solleva TransizioneNonValida se la fattura è già in uno stato terminale
    con residuo positivo (dato incoerente da segnalare, non da correggere).
    """
    if importo_residuo <= 0:
        return StatoFattura.SALDATA
    if stato_corrente in STATI_TERMINALI:
        raise TransizioneNonValida(
            f"fattura in stato {stato_corrente} con residuo {importo_residuo}"
        )
    return stato_corrente


def puo_ricevere_sollecito(stato: StatoFattura) -> bool:
    """Controllo just-in-time: la comunicazione programmata può partire?"""
    return stato not in STATI_NON_SOLLECITABILI


def stato_posizione(stati_fatture: list[StatoFattura]) -> StatoPosizione:
    """La posizione è chiusa solo quando ogni fattura è uscita dal flusso.

    Una posizione senza fatture è considerata aperta: è il caso di un cliente
    appena censito, non di una posizione conclusa.
    """
    if not stati_fatture:
        return StatoPosizione.APERTA
    if all(stato in STATI_TERMINALI for stato in stati_fatture):
        return StatoPosizione.CHIUSA
    return StatoPosizione.APERTA


def residuo_dopo_incasso(importo_residuo: Decimal, importo_incassato: Decimal) -> Decimal:
    """Nuovo residuo, mai negativo: l'eccedenza di un sovra-pagamento non
    diventa un credito verso il debitore in questo modulo."""
    nuovo = importo_residuo - importo_incassato
    return nuovo if nuovo > 0 else Decimal("0.00")
