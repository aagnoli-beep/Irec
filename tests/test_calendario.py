"""Regole di calendario (PRD 5.1): festivi, giorni utili, finestra oraria."""

from datetime import UTC, date, datetime

import pytest

from irec.domain.calendario import (
    TZ_ITALIA,
    applica_finestra,
    assumi_utc,
    e_festivo,
    e_giorno_utile,
    istante_invio,
    pasqua,
    prossimo_giorno_utile,
)


class TestFestivita:
    @pytest.mark.parametrize(
        ("anno", "atteso"),
        [
            (2024, date(2024, 3, 31)),
            (2025, date(2025, 4, 20)),
            (2026, date(2026, 4, 5)),
            (2027, date(2027, 3, 28)),
        ],
    )
    def test_pasqua(self, anno, atteso):
        assert pasqua(anno) == atteso

    def test_pasquetta_e_festiva(self):
        assert e_festivo(date(2026, 4, 6))

    @pytest.mark.parametrize(
        "giorno",
        [
            date(2026, 1, 1),
            date(2026, 1, 6),
            date(2026, 4, 25),
            date(2026, 5, 1),
            date(2026, 6, 2),
            date(2026, 8, 15),
            date(2026, 11, 1),
            date(2026, 12, 8),
            date(2026, 12, 25),
            date(2026, 12, 26),
        ],
    )
    def test_festivita_nazionali(self, giorno):
        assert e_festivo(giorno)

    def test_un_giorno_qualunque_non_e_festivo(self):
        assert not e_festivo(date(2026, 8, 5))  # mercoledì


class TestGiorniUtili:
    def test_sabato_e_domenica_non_sono_utili(self):
        assert not e_giorno_utile(date(2026, 8, 8))  # sabato
        assert not e_giorno_utile(date(2026, 8, 9))  # domenica

    def test_feriale_non_festivo_e_utile(self):
        assert e_giorno_utile(date(2026, 8, 5))

    def test_prossimo_giorno_utile_salta_weekend_e_festivi(self):
        # Venerdì 25/12/2026 (Natale) → sabato 26 (S.Stefano) → weekend →
        # lunedì 28.
        assert prossimo_giorno_utile(date(2026, 12, 25)) == date(2026, 12, 28)

    def test_prossimo_giorno_utile_di_un_giorno_utile_e_lo_stesso(self):
        assert prossimo_giorno_utile(date(2026, 8, 5)) == date(2026, 8, 5)


class TestFinestraInvio:
    def test_orario_valido_in_giorno_utile_resta_invariato(self):
        pianificato = istante_invio(date(2026, 8, 5))  # 10:00 italiane
        assert applica_finestra(pianificato) == pianificato

    def test_le_18_esatte_sono_ancora_nella_finestra(self):
        alle_18 = datetime(2026, 8, 5, 18, 0, tzinfo=TZ_ITALIA).astimezone(UTC)
        assert applica_finestra(alle_18) == alle_18

    def test_dopo_le_18_slitta_al_giorno_utile_successivo(self):
        """PRD 5.1: nessun invio dopo le 18:00."""
        sera = datetime(2026, 8, 5, 19, 30, tzinfo=TZ_ITALIA)
        spostato = applica_finestra(sera.astimezone(UTC))
        locale = spostato.astimezone(TZ_ITALIA)
        assert locale.date() == date(2026, 8, 6)
        assert locale.hour == 10

    def test_venerdi_sera_slitta_a_lunedi(self):
        venerdi_sera = datetime(2026, 8, 7, 20, 0, tzinfo=TZ_ITALIA)
        locale = applica_finestra(venerdi_sera.astimezone(UTC)).astimezone(TZ_ITALIA)
        assert locale.date() == date(2026, 8, 10)  # lunedì

    def test_festivo_slitta_al_primo_giorno_utile(self):
        """PRD 5.1: nessun invio nei giorni festivi."""
        ferragosto = istante_invio(date(2026, 8, 15))  # sabato e festivo
        locale = applica_finestra(ferragosto).astimezone(TZ_ITALIA)
        assert locale.date() == date(2026, 8, 17)  # lunedì
        assert locale.hour == 10

    def test_catena_natalizia(self):
        """25/12 → 26/12 festivo → weekend → lunedì 28."""
        natale = istante_invio(date(2026, 12, 25))
        locale = applica_finestra(natale).astimezone(TZ_ITALIA)
        assert locale.date() == date(2026, 12, 28)

    def test_il_risultato_e_sempre_utc(self):
        spostato = applica_finestra(istante_invio(date(2026, 8, 15)))
        assert spostato.tzinfo == UTC


class TestAssumiUtc:
    def test_naive_diventa_utc(self):
        naive = datetime(2026, 8, 5, 10, 0)
        assert assumi_utc(naive).tzinfo == UTC

    def test_aware_resta_invariato(self):
        aware = datetime(2026, 8, 5, 10, 0, tzinfo=TZ_ITALIA)
        assert assumi_utc(aware) is aware
