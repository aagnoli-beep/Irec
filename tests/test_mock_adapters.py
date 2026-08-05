"""Adapter mock dei tre microservizi: contratto delle porte e scenari."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from irec.adapters.mock import (
    MockBanca,
    MockCassettoFiscale,
    MockRiconciliatore,
    ScenarioTenant,
)
from irec.adapters.mock.demo import scenario_demo
from irec.domain.porte import (
    CollegamentoEsterno,
    ErroreCollegamento,
    FattureProvider,
    MovimentiProvider,
    MovimentoBancario,
    Riconciliatore,
    StatoCollegamento,
)

OGGI = date(2026, 8, 5)
TENANT = "tenant-abc"


def movimento(importo: Decimal, piva: str | None) -> MovimentoBancario:
    return MovimentoBancario(
        id_movimento="mov-x",
        data=OGGI,
        importo=importo,
        descrizione="BONIFICO",
        piva_cf_controparte=piva,
    )


@pytest.fixture
def scenario() -> ScenarioTenant:
    return scenario_demo(OGGI)


@pytest.fixture
def cassetto(scenario) -> MockCassettoFiscale:
    return MockCassettoFiscale({TENANT: scenario}, oggi=OGGI)


@pytest.fixture
def banca(scenario) -> MockBanca:
    return MockBanca({TENANT: scenario}, oggi=OGGI)


class TestContrattoPorte:
    def test_i_mock_implementano_le_porte(self, cassetto, banca):
        """Il dominio dipende dai Protocol: i mock devono rispettarli."""
        assert isinstance(cassetto, FattureProvider)
        assert isinstance(banca, MovimentiProvider)
        assert isinstance(MockRiconciliatore(), Riconciliatore)


class TestStatoCollegamenti:
    def test_collegamento_attivo(self, cassetto, banca):
        assert cassetto.stato_collegamento(TENANT).attivo
        assert banca.stato_consenso(TENANT).attivo

    def test_delega_ade_non_attiva_blocca_il_recupero(self, scenario):
        scenario.collegamento_ade = CollegamentoEsterno(
            stato=StatoCollegamento.NON_CONFIGURATO
        )
        cassetto = MockCassettoFiscale({TENANT: scenario}, oggi=OGGI)
        with pytest.raises(ErroreCollegamento) as exc_info:
            cassetto.recupera_fatture(TENANT, OGGI - timedelta(days=60), OGGI)
        assert exc_info.value.collegamento.stato is StatoCollegamento.NON_CONFIGURATO

    def test_consenso_psd2_scaduto_blocca_il_recupero(self, scenario):
        scenario.consenso_psd2 = CollegamentoEsterno(
            stato=StatoCollegamento.SCADUTO, scade_il=OGGI - timedelta(days=1)
        )
        banca = MockBanca({TENANT: scenario}, oggi=OGGI)
        assert not banca.stato_consenso(TENANT).attivo
        with pytest.raises(ErroreCollegamento):
            banca.recupera_movimenti(TENANT, OGGI - timedelta(days=30), OGGI)

    def test_tenant_sconosciuto_e_un_errore_di_collegamento(self, cassetto):
        with pytest.raises(ErroreCollegamento):
            cassetto.stato_collegamento("tenant-mai-visto")


class TestLatenzaSdi:
    def test_fattura_appena_emessa_non_e_ancora_visibile(self):
        """SLA SDI: i documenti compaiono nel cassetto dopo 2-3 giorni."""
        fresca = replace(scenario_demo(OGGI).fatture[0], data_emissione=OGGI)
        scenario = ScenarioTenant(fatture=[fresca])
        cassetto = MockCassettoFiscale({TENANT: scenario}, oggi=OGGI)

        assert cassetto.recupera_fatture(TENANT, OGGI - timedelta(days=60), OGGI) == []

        # Due giorni dopo, la stessa fattura è visibile.
        cassetto_dopo = MockCassettoFiscale(
            {TENANT: scenario}, oggi=OGGI + timedelta(days=2)
        )
        visibili = cassetto_dopo.recupera_fatture(
            TENANT, OGGI - timedelta(days=60), OGGI + timedelta(days=2)
        )
        assert len(visibili) == 1

    def test_intervallo_di_recupero_rispettato(self, cassetto):
        """La profondità del recupero è parametrizzabile (lookback)."""
        recenti = cassetto.recupera_fatture(TENANT, OGGI - timedelta(days=25), OGGI)
        assert {fattura.numero for fattura in recenti} == {"34-FA", "35-FA"}


class TestRiconciliatore:
    def test_scenario_demo_completo(self, cassetto, banca):
        fatture = cassetto.recupera_fatture(TENANT, OGGI - timedelta(days=60), OGGI)
        movimenti = banca.recupera_movimenti(TENANT, OGGI - timedelta(days=30), OGGI)
        esito = MockRiconciliatore().riconcilia(fatture, movimenti)

        pagamenti = {
            (pagamento.numero_fattura, pagamento.importo_pagato)
            for pagamento in esito.pagamenti
        }
        # 32-FA saldata per intero; il bonifico cumulativo da 1000 copre
        # 800 della 33-FA (più vecchia) e 200 della 34-FA (parziale).
        assert pagamenti == {
            ("32-FA", Decimal("1220.00")),
            ("33-FA", Decimal("800.00")),
            ("34-FA", Decimal("200.00")),
        }
        assert {fattura.numero for fattura in esito.fatture_da_pagare} == {
            "34-FA",
            "35-FA",
        }
        assert [mov.id_movimento for mov in esito.movimenti_non_riconciliati] == [
            "mov-003"
        ]

    def test_movimento_duplicato_processato_una_volta(self, scenario):
        """Il duplicato di mov-002 nello scenario non raddoppia gli incassi."""
        esito = MockRiconciliatore().riconcilia(scenario.fatture, scenario.movimenti)
        incassato_initech = sum(
            pagamento.importo_pagato
            for pagamento in esito.pagamenti
            if pagamento.piva_cf_debitore == "01234509876"
        )
        assert incassato_initech == Decimal("1000.00")

    def test_sovra_pagamento_lascia_l_eccedenza_non_allocata(self):
        fattura = scenario_demo(OGGI).fatture[0]  # 1220.00
        esito = MockRiconciliatore().riconcilia(
            [fattura], [movimento(Decimal("1500.00"), fattura.piva_cf_debitore)]
        )
        assert esito.pagamenti[0].importo_pagato == Decimal("1220.00")
        assert esito.fatture_da_pagare == ()

    def test_movimento_negativo_non_e_un_incasso(self):
        fattura = scenario_demo(OGGI).fatture[0]
        esito = MockRiconciliatore().riconcilia(
            [fattura], [movimento(Decimal("-300.00"), fattura.piva_cf_debitore)]
        )
        assert esito.pagamenti == ()
        assert len(esito.movimenti_non_riconciliati) == 1

    def test_nessun_movimento_tutte_da_pagare(self, scenario):
        esito = MockRiconciliatore().riconcilia(scenario.fatture, [])
        assert esito.pagamenti == ()
        assert len(esito.fatture_da_pagare) == len(scenario.fatture)
