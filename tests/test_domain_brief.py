"""Composizione del brief giornaliero (dominio puro)."""

from decimal import Decimal

from irec.domain.brief import MAX_AZIONI_BRIEF, componi_brief, percentuale_recuperato
from irec.domain.enums import TipoNotifica


def test_positivo_per_primo_percentuale():
    assert percentuale_recuperato(Decimal("1000.00"), Decimal("700.00")) == 70
    assert percentuale_recuperato(Decimal("0.00"), Decimal("0.00")) == 0


def test_azioni_ordinate_per_priorita():
    brief = componi_brief(
        Decimal("1000"),
        Decimal("400"),
        Decimal("600"),
        Decimal("0"),
        {
            TipoNotifica.DATO_IN_RITARDO: 1,
            TipoNotifica.ESCALATION_IMMINENTE: 2,
        },
    )
    tipi = [voce.tipo for voce in brief.azioni_principali]
    # L'escalation imminente ha priorità sul dato in ritardo.
    assert tipi[0] is TipoNotifica.ESCALATION_IMMINENTE


def test_tetto_azioni_e_altre():
    conteggio = {
        TipoNotifica.ESCALATION_IMMINENTE: 3,
        TipoNotifica.CONSENSO_PSD2: 1,
        TipoNotifica.COLLEGAMENTO_ADE: 1,
        TipoNotifica.ESCALATION_ESEGUITA: 2,
        TipoNotifica.DATO_IN_RITARDO: 4,
    }
    brief = componi_brief(
        Decimal("1000"), Decimal("0"), Decimal("1000"), Decimal("0"), conteggio
    )
    assert len(brief.azioni_principali) == MAX_AZIONI_BRIEF
    # Oltre il tetto: escalation_eseguita (2) + dato_in_ritardo (4) = 6.
    assert brief.altre_azioni == 6


def test_nessuna_azione():
    brief = componi_brief(
        Decimal("500"), Decimal("500"), Decimal("0"), Decimal("0"), {}
    )
    assert brief.azioni_principali == ()
    assert brief.altre_azioni == 0
    assert brief.recuperato == "500"


def test_importi_come_stringhe():
    brief = componi_brief(
        Decimal("1220.00"), Decimal("0"), Decimal("1220.00"), Decimal("0"), {}
    )
    assert brief.affidato == "1220.00"
    assert isinstance(brief.affidato, str)
