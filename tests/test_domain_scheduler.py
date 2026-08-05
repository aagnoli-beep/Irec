"""Regole del motore solleciti: canali per pacchetto, consolidamento, soglie."""

from datetime import date

import pytest

from irec.domain.enums import Canale, Pacchetto
from irec.domain.scheduler import (
    MOTIVO_CANALE_NON_NEL_PACCHETTO,
    MOTIVO_OPT_OUT,
    MOTIVO_RECAPITO_MANCANTE,
    InvioDovuto,
    RecapitiCliente,
    consolida,
    escalation_imminente,
    in_escalation,
    motivo_salto,
)


def invio(cliente: str, canale: Canale, numero: str, template: str = "t") -> InvioDovuto:
    return InvioDovuto(
        comunicazione_id=f"c-{numero}",
        fattura_id=f"f-{numero}",
        cliente_id=cliente,
        canale=canale,
        template=template,
        numero_fattura=numero,
        importo_residuo="100.00",
    )


class TestCanaliPerPacchetto:
    def test_entry_non_puo_whatsapp(self):
        recapiti = RecapitiCliente(telefono="+39333")
        assert (
            motivo_salto(Canale.WHATSAPP, Pacchetto.ENTRY, recapiti)
            == MOTIVO_CANALE_NON_NEL_PACCHETTO
        )

    def test_value_puo_whatsapp_con_telefono(self):
        recapiti = RecapitiCliente(telefono="+39333")
        assert motivo_salto(Canale.WHATSAPP, Pacchetto.VALUE, recapiti) is None

    def test_recapito_mancante_salta(self):
        recapiti = RecapitiCliente(email=None)
        assert (
            motivo_salto(Canale.EMAIL, Pacchetto.ENTRY, recapiti)
            == MOTIVO_RECAPITO_MANCANTE
        )

    def test_opt_out_salta(self):
        recapiti = RecapitiCliente(email="a@b.it", canali_opt_out=frozenset({"email"}))
        assert motivo_salto(Canale.EMAIL, Pacchetto.ENTRY, recapiti) == MOTIVO_OPT_OUT

    def test_voice_solo_premium(self):
        recapiti = RecapitiCliente(telefono="+39333")
        assert motivo_salto(Canale.VOICE, Pacchetto.VALUE, recapiti) is not None
        assert motivo_salto(Canale.VOICE, Pacchetto.PREMIUM, recapiti) is None


class TestConsolidamento:
    def test_stesso_cliente_stesso_canale_un_solo_gruppo(self):
        invii = [
            invio("cli-1", Canale.EMAIL, "10-FA"),
            invio("cli-1", Canale.EMAIL, "11-FA"),
        ]
        gruppi = consolida(invii)
        assert len(gruppi) == 1
        assert gruppi[0].numeri_fattura == ("10-FA", "11-FA")

    def test_canali_diversi_gruppi_diversi(self):
        invii = [
            invio("cli-1", Canale.EMAIL, "10-FA"),
            invio("cli-1", Canale.PEC, "10-FA"),
        ]
        assert len(consolida(invii)) == 2

    def test_clienti_diversi_gruppi_diversi(self):
        invii = [
            invio("cli-1", Canale.EMAIL, "10-FA"),
            invio("cli-2", Canale.EMAIL, "20-FA"),
        ]
        assert len(consolida(invii)) == 2

    def test_template_del_gruppo_e_quello_dell_ultimo_step(self):
        """Il tono non deve regredire: si usa il template più avanzato."""
        invii = [
            invio("cli-1", Canale.EMAIL, "10-FA", template="sollecito_1"),
            invio("cli-1", Canale.EMAIL, "11-FA", template="sollecito_4"),
        ]
        assert consolida(invii)[0].template == "sollecito_4"

    def test_ordine_deterministico(self):
        invii = [
            invio("cli-2", Canale.EMAIL, "20-FA"),
            invio("cli-1", Canale.EMAIL, "10-FA"),
        ]
        gruppi = consolida(invii)
        assert [g.cliente_id for g in gruppi] == ["cli-1", "cli-2"]


class TestSoglieEscalation:
    def test_t45_e_escalation(self):
        scadenza = date(2026, 7, 1)
        assert in_escalation(date(2026, 8, 15), scadenza) is True  # +45

    def test_prima_di_t45_non_e_escalation(self):
        scadenza = date(2026, 7, 1)
        assert in_escalation(date(2026, 8, 14), scadenza) is False  # +44

    def test_t44_e_imminente(self):
        scadenza = date(2026, 7, 1)
        assert escalation_imminente(date(2026, 8, 14), scadenza) is True

    @pytest.mark.parametrize("giorni", [43, 45])
    def test_solo_t44_e_imminente(self, giorni):
        from datetime import timedelta

        scadenza = date(2026, 7, 1)
        assert (
            escalation_imminente(scadenza + timedelta(days=giorni), scadenza) is False
        )
