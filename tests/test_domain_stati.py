"""Regole pure di stato: dominio finanziario, nessun IO."""

from decimal import Decimal

import pytest

from irec.domain.enums import CANALI_PER_PACCHETTO, Canale, Pacchetto, StatoFattura, StatoPosizione
from irec.domain.stati import (
    TransizioneNonValida,
    puo_ricevere_sollecito,
    residuo_dopo_incasso,
    stato_dopo_pagamento,
    stato_posizione,
)


class TestStatoDopoPagamento:
    def test_pagamento_totale_salda(self):
        assert (
            stato_dopo_pagamento(StatoFattura.GESTIONE, Decimal("0.00"))
            == StatoFattura.SALDATA
        )

    def test_sovra_pagamento_salda(self):
        """Residuo negativo (incasso maggiore del dovuto) → comunque saldata."""
        assert (
            stato_dopo_pagamento(StatoFattura.GESTIONE, Decimal("-5.00"))
            == StatoFattura.SALDATA
        )

    def test_pagamento_parziale_lascia_in_gestione(self):
        assert (
            stato_dopo_pagamento(StatoFattura.GESTIONE, Decimal("100.00"))
            == StatoFattura.GESTIONE
        )

    def test_pagamento_parziale_non_risveglia_una_fattura_in_pausa(self):
        assert (
            stato_dopo_pagamento(StatoFattura.PAUSA, Decimal("100.00"))
            == StatoFattura.PAUSA
        )

    @pytest.mark.parametrize("stato", [StatoFattura.SALDATA, StatoFattura.INSOLUTO])
    def test_stato_terminale_con_residuo_positivo_e_incoerente(self, stato):
        with pytest.raises(TransizioneNonValida):
            stato_dopo_pagamento(stato, Decimal("50.00"))


class TestControlloJustInTime:
    def test_fattura_in_gestione_puo_essere_sollecitata(self):
        assert puo_ricevere_sollecito(StatoFattura.GESTIONE) is True

    @pytest.mark.parametrize(
        "stato", [StatoFattura.SALDATA, StatoFattura.PAUSA, StatoFattura.INSOLUTO]
    )
    def test_chi_ha_pagato_o_e_fermo_non_riceve_solleciti(self, stato):
        assert puo_ricevere_sollecito(stato) is False


class TestStatoPosizione:
    def test_posizione_senza_fatture_resta_aperta(self):
        assert stato_posizione([]) == StatoPosizione.APERTA

    def test_una_fattura_ancora_in_gestione_tiene_aperta_la_posizione(self):
        stati = [StatoFattura.SALDATA, StatoFattura.GESTIONE]
        assert stato_posizione(stati) == StatoPosizione.APERTA

    def test_tutte_saldate_chiude_la_posizione(self):
        stati = [StatoFattura.SALDATA, StatoFattura.SALDATA]
        assert stato_posizione(stati) == StatoPosizione.CHIUSA

    def test_saldate_e_insolute_chiudono_la_posizione(self):
        """Anche l'insoluto è uscito dal flusso: la posizione non è più lavorabile."""
        stati = [StatoFattura.SALDATA, StatoFattura.INSOLUTO]
        assert stato_posizione(stati) == StatoPosizione.CHIUSA


class TestResiduo:
    def test_incasso_parziale(self):
        assert residuo_dopo_incasso(Decimal("1000.00"), Decimal("300.00")) == Decimal(
            "700.00"
        )

    def test_incasso_esatto_azzera(self):
        assert residuo_dopo_incasso(Decimal("1000.00"), Decimal("1000.00")) == Decimal(
            "0.00"
        )

    def test_sovra_pagamento_non_genera_residuo_negativo(self):
        assert residuo_dopo_incasso(Decimal("100.00"), Decimal("150.00")) == Decimal(
            "0.00"
        )

    def test_precisione_decimale_sui_centesimi(self):
        """Mai float: 0.1 + 0.2 non deve introdurre errori di arrotondamento."""
        assert residuo_dopo_incasso(Decimal("0.30"), Decimal("0.10")) == Decimal("0.20")


class TestCanaliPerPacchetto:
    def test_entry_non_ha_whatsapp_ne_voice(self):
        canali = CANALI_PER_PACCHETTO[Pacchetto.ENTRY]
        assert Canale.WHATSAPP not in canali
        assert Canale.VOICE not in canali

    def test_value_aggiunge_whatsapp_ma_non_voice(self):
        canali = CANALI_PER_PACCHETTO[Pacchetto.VALUE]
        assert Canale.WHATSAPP in canali
        assert Canale.VOICE not in canali

    def test_premium_ha_tutti_i_canali(self):
        assert CANALI_PER_PACCHETTO[Pacchetto.PREMIUM] == frozenset(Canale)
