"""Regole di calendario per gli invii (PRD 5.1): festivi, weekend, finestra."""

from datetime import UTC, date, datetime

import pytest

from irec.domain.calendario import (
    TZ_ITALIA,
    applica_finestra,
    e_festivo,
    e_giorno_utile,
    istante_invio,
    pasqua,
    prossimo_giorno_utile,
)


class TestFestivi:
    def test_pasqua_nota(self):
        assert pasqua(2026) == date(2026, 4, 5)
        assert pasqua(2027) == date(2027, 3, 28)

    def test_pasquetta_e_festiva(self):
        assert e_festivo(date(2026, 4, 6)) is True  # lunedì dell'Angelo

    @pytest.mark.parametrize(
        "giorno",
        [date(2026, 1, 1), date(2026, 8, 15), date(2026, 12, 25), date(2026, 4, 25)],
    )
    def test_festivita_fisse(self, giorno):
        assert e_festivo(giorno) is True

    def test_giorno_feriale_normale(self):
        assert e_festivo(date(2026, 3, 10)) is False


class TestGiornoUtile:
    def test_sabato_e_domenica_non_utili(self):
        assert e_giorno_utile(date(2026, 8, 8)) is False  # sabato
        assert e_giorno_utile(date(2026, 8, 9)) is False  # domenica

    def test_feriale_e_utile(self):
        assert e_giorno_utile(date(2026, 8, 10)) is True  # lunedì

    def test_festivo_infrasettimanale_non_utile(self):
        assert e_giorno_utile(date(2026, 12, 25)) is False  # Natale, venerdì

    def test_prossimo_giorno_utile_salta_weekend_e_festivi(self):
        # Ven 25/12 (Natale) → sab 26 (S.Stefano+weekend), dom 27 → lun 28.
        assert prossimo_giorno_utile(date(2026, 12, 25)) == date(2026, 12, 28)


class TestFinestraOraria:
    def test_invio_in_orario_resta(self):
        # Lunedì 10:00 italiane: già dentro la finestra.
        pianificato = istante_invio(date(2026, 8, 10))
        assert applica_finestra(pianificato) == pianificato

    def test_dopo_le_18_slitta_al_giorno_dopo(self):
        # Lunedì 20:00 italiane → martedì 10:00 italiane.
        sera = datetime(2026, 8, 10, 20, 0, tzinfo=TZ_ITALIA).astimezone(UTC)
        risultato = applica_finestra(sera).astimezone(TZ_ITALIA)
        assert risultato.date() == date(2026, 8, 11)
        assert risultato.hour == 10

    def test_sabato_slitta_a_lunedi(self):
        sabato = istante_invio(date(2026, 8, 8))
        risultato = applica_finestra(sabato).astimezone(TZ_ITALIA)
        assert risultato.date() == date(2026, 8, 10)  # lunedì

    def test_venerdi_sera_slitta_a_lunedi(self):
        # Venerdì dopo le 18 → sabato (non utile) → lunedì.
        ven_sera = datetime(2026, 8, 7, 19, 0, tzinfo=TZ_ITALIA).astimezone(UTC)
        risultato = applica_finestra(ven_sera).astimezone(TZ_ITALIA)
        assert risultato.date() == date(2026, 8, 10)
        assert risultato.hour == 10

    def test_risultato_sempre_utc(self):
        assert applica_finestra(istante_invio(date(2026, 8, 10))).tzinfo == UTC
