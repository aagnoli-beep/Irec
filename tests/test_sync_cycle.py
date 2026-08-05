"""Ciclo giornaliero di sincronizzazione end-to-end con i mock (M3).

I due percorsi dichiarati nel criterio di completamento della milestone:
- da "nuova fattura nel cassetto" a "fattura a sistema con schedule";
- da "movimento in banca" a "fattura saldata con solleciti annullati".
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from irec.adapters.db.models import (
    ClienteFinale,
    Fattura,
    Flusso,
    Pagamento,
    Posizione,
)
from irec.adapters.db.repository import TenantRepository
from irec.adapters.db.session import session_scope
from irec.adapters.mock import (
    MockBanca,
    MockCassettoFiscale,
    MockRiconciliatore,
    ScenarioTenant,
)
from irec.adapters.mock.demo import scenario_demo
from irec.domain.enums import (
    StatoComunicazione,
    StatoFattura,
    StatoPosizione,
)
from irec.domain.flusso_default import FLUSSO_DEFAULT
from irec.domain.porte import CollegamentoEsterno, StatoCollegamento
from irec.services.sync import CicloSincronizzazione
from tests.factories import make_mandante

OGGI = date(2026, 8, 5)
TENANT = "tenant-abc"


@pytest.fixture
def scenario() -> ScenarioTenant:
    return scenario_demo(OGGI)


def ciclo_per(scenario: ScenarioTenant) -> CicloSincronizzazione:
    scenari = {TENANT: scenario}
    return CicloSincronizzazione(
        MockCassettoFiscale(scenari, oggi=OGGI),
        MockBanca(scenari, oggi=OGGI),
        MockRiconciliatore(),
        oggi=OGGI,
        lookback_giorni=60,
    )


def prepara_mandante(session_factory) -> None:
    with session_scope(session_factory) as session:
        TenantRepository(session, TENANT).add(make_mandante())


def esegui(session_factory, scenario: ScenarioTenant):
    with session_scope(session_factory) as session:
        repo = TenantRepository(session, TENANT)
        return ciclo_per(scenario).esegui(repo)


class TestImportFatture:
    def test_da_cassetto_a_sistema_con_schedule(self, session_factory, scenario):
        """Il primo percorso della milestone: fattura importata, cliente e
        posizione creati, schedule di solleciti programmato."""
        prepara_mandante(session_factory)
        esito = esegui(session_factory, scenario)

        assert esito.collegamento_ade == StatoCollegamento.ATTIVO
        assert esito.fatture_importate == 4
        assert esito.clienti_creati == 2  # Globex e Initech

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fatture = {fattura.numero: fattura for fattura in repo.list(Fattura)}
            assert set(fatture) == {"32-FA", "33-FA", "34-FA", "35-FA"}

            # Ogni fattura ha lo schedule completo del flusso di default,
            # ancorato alla propria scadenza T.
            fattura = fatture["35-FA"]
            programmate = [
                comunicazione
                for comunicazione in fattura.comunicazioni
                if comunicazione.stato is StatoComunicazione.PROGRAMMATA
            ]
            assert len(programmate) == len(FLUSSO_DEFAULT)
            prima = min(programmate, key=lambda c: c.programmata_per)
            assert prima.programmata_per.date() == fattura.data_scadenza - timedelta(
                days=2
            )

            # Il flusso di default è stato creato una sola volta.
            assert len(repo.list(Flusso)) == 1

    def test_reimport_idempotente(self, session_factory, scenario):
        """PRD 4.1: il reimport dal cassetto non duplica nulla."""
        prepara_mandante(session_factory)
        esegui(session_factory, scenario)
        esito_secondo = esegui(session_factory, scenario)

        assert esito_secondo.fatture_importate == 0
        assert esito_secondo.clienti_creati == 0
        assert esito_secondo.comunicazioni_programmate == 0

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            assert len(repo.list(Fattura)) == 4
            assert len(repo.list(ClienteFinale)) == 2

    def test_fattura_gia_scaduta_e_segnalata(self, session_factory, scenario):
        """PRD 4.1: scadenza già superata all'import = anomalia, non flusso."""
        prepara_mandante(session_factory)
        esito = esegui(session_factory, scenario)
        # Nello scenario demo 32-FA e 33-FA sono già scadute a OGGI.
        assert "fattura_gia_scaduta:32-FA" in esito.anomalie
        assert "fattura_gia_scaduta:33-FA" in esito.anomalie

    def test_senza_mandante_il_ciclo_si_ferma(self, session_factory, scenario):
        esito = esegui(session_factory, scenario)
        assert esito.anomalie == ["mandante_non_censito"]
        assert esito.fatture_importate == 0


class TestDifeseVersoIlRiconciliatore:
    def test_pagamento_su_fattura_inesistente_e_anomalia(self, session_factory):
        """Un riconciliatore che restituisce abbinamenti spuri non deve
        produrre pagamenti: anomalia segnalata, dati intatti."""
        from irec.domain.porte import EsitoRiconciliazione, PagamentoRilevato

        class RiconciliatoreSpurio:
            def riconcilia(self, fatture, movimenti):
                return EsitoRiconciliazione(
                    pagamenti=(
                        PagamentoRilevato(
                            numero_fattura="INESISTENTE",
                            piva_cf_debitore="00000000000",
                            id_movimento="mov-spurio",
                            importo_pagato=Decimal("1.00"),
                        ),
                    ),
                    fatture_da_pagare=tuple(fatture),
                    movimenti_non_riconciliati=(),
                )

        base = scenario_demo(OGGI)
        scenario = ScenarioTenant(fatture=base.fatture[:1], movimenti=base.movimenti[:1])
        prepara_mandante(session_factory)
        scenari = {TENANT: scenario}
        ciclo = CicloSincronizzazione(
            MockCassettoFiscale(scenari, oggi=OGGI),
            MockBanca(scenari, oggi=OGGI),
            RiconciliatoreSpurio(),
            oggi=OGGI,
            lookback_giorni=60,
        )
        with session_scope(session_factory) as session:
            esito = ciclo.esegui(TenantRepository(session, TENANT))

        # (32-FA è anche già scaduta a OGGI: l'anomalia di import è attesa.)
        assert "pagamento_senza_fattura:mov-spurio" in esito.anomalie
        assert esito.pagamenti_registrati == 0
        with session_scope(session_factory) as session:
            assert TenantRepository(session, TENANT).list(Pagamento) == []

    def test_fattura_duplicata_nello_stesso_lotto_importata_una_volta(
        self, session_factory
    ):
        fattura = scenario_demo(OGGI).fatture[0]
        scenario = ScenarioTenant(fatture=[fattura, fattura])
        prepara_mandante(session_factory)
        esito = esegui(session_factory, scenario)

        assert esito.fatture_importate == 1
        assert esito.comunicazioni_programmate == len(FLUSSO_DEFAULT)
        with session_scope(session_factory) as session:
            assert len(TenantRepository(session, TENANT).list(Fattura)) == 1

    def test_pagamento_manuale_preesistente_non_fa_fallire_la_run(
        self, session_factory
    ):
        """Il dedup sulle chiavi: un incasso già registrato a mano non
        viene contato di nuovo e non manda la run in errore."""
        from datetime import date as date_type

        from irec.adapters.db.models import Fattura as FatturaDb
        from irec.domain.enums import OriginePagamento
        from tests.factories import make_pagamento

        base = scenario_demo(OGGI)
        fattura_32 = base.fatture[0]
        scenario = ScenarioTenant(
            fatture=[fattura_32], movimenti=[base.movimenti[0]]  # mov-001 la salda
        )
        prepara_mandante(session_factory)
        esegui(session_factory, ScenarioTenant(fatture=[fattura_32]))  # solo import

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fattura = repo.list(FatturaDb)[0]
            repo.add(
                make_pagamento(
                    fattura.id,
                    importo=Decimal("1220.00"),
                    data_pagamento=date_type(2026, 8, 1),
                    origine=OriginePagamento.MANUALE,
                    chiave_idempotenza="mov-001:32-FA",
                )
            )
            fattura.importo_residuo = Decimal("0.00")
            fattura.stato = StatoFattura.SALDATA

        esito = esegui(session_factory, scenario)
        assert esito.pagamenti_registrati == 0
        assert esito.anomalie == []
        with session_scope(session_factory) as session:
            assert len(TenantRepository(session, TENANT).list(Pagamento)) == 1

    def test_tenant_ignoto_al_servizio_esterno_e_stato_non_configurato(
        self, session_factory
    ):
        """ErroreCollegamento dalle porte di stato → esito, non eccezione."""
        prepara_mandante(session_factory)
        ciclo = CicloSincronizzazione(
            MockCassettoFiscale({}, oggi=OGGI),
            MockBanca({}, oggi=OGGI),
            MockRiconciliatore(),
            oggi=OGGI,
        )
        with session_scope(session_factory) as session:
            esito = ciclo.esegui(TenantRepository(session, TENANT))

        assert esito.collegamento_ade == StatoCollegamento.NON_CONFIGURATO
        assert esito.consenso_psd2 == StatoCollegamento.NON_CONFIGURATO
        assert esito.fatture_importate == 0


class TestCollegamentiCaduti:
    def test_delega_ade_caduta_non_importa_nulla(self, session_factory, scenario):
        scenario.collegamento_ade = CollegamentoEsterno(
            stato=StatoCollegamento.NON_CONFIGURATO
        )
        prepara_mandante(session_factory)
        esito = esegui(session_factory, scenario)

        assert esito.collegamento_ade == StatoCollegamento.NON_CONFIGURATO
        assert esito.fatture_importate == 0
        # La banca resta interrogabile: l'esito riporta entrambi gli stati.
        assert esito.consenso_psd2 == StatoCollegamento.ATTIVO

    def test_consenso_psd2_scaduto_importa_ma_non_incassa(
        self, session_factory, scenario
    ):
        scenario.consenso_psd2 = CollegamentoEsterno(
            stato=StatoCollegamento.SCADUTO, scade_il=OGGI - timedelta(days=1)
        )
        prepara_mandante(session_factory)
        esito = esegui(session_factory, scenario)

        assert esito.fatture_importate == 4
        assert esito.pagamenti_registrati == 0
        assert esito.consenso_psd2 == StatoCollegamento.SCADUTO


class TestRiconciliazione:
    def test_da_movimento_a_fattura_saldata_con_solleciti_annullati(
        self, session_factory, scenario
    ):
        """Il secondo percorso della milestone."""
        prepara_mandante(session_factory)
        esito = esegui(session_factory, scenario)

        assert esito.pagamenti_registrati == 3
        assert esito.fatture_saldate == 2  # 32-FA intera, 33-FA via cumulativo

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fatture = {fattura.numero: fattura for fattura in repo.list(Fattura)}

            saldata = fatture["32-FA"]
            assert saldata.stato is StatoFattura.SALDATA
            assert saldata.importo_residuo == Decimal("0.00")
            # US-08: mai sollecitare chi ha pagato.
            assert all(
                comunicazione.stato is StatoComunicazione.ANNULLATA
                for comunicazione in saldata.comunicazioni
            )

            # 34-FA ha ricevuto un parziale dal bonifico cumulativo: resta
            # in gestione con il residuo aggiornato e lo schedule attivo.
            parziale = fatture["34-FA"]
            assert parziale.stato is StatoFattura.GESTIONE
            assert parziale.importo_residuo == Decimal("300.00")
            assert any(
                comunicazione.stato is StatoComunicazione.PROGRAMMATA
                for comunicazione in parziale.comunicazioni
            )

    def test_ri_esecuzione_non_conta_i_pagamenti_due_volte(
        self, session_factory, scenario
    ):
        """Idempotenza della riconciliazione fra run successive."""
        prepara_mandante(session_factory)
        esegui(session_factory, scenario)
        esito_secondo = esegui(session_factory, scenario)

        assert esito_secondo.pagamenti_registrati == 0
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            pagamenti = repo.list(Pagamento)
            assert len(pagamenti) == 3
            fatture = {fattura.numero: fattura for fattura in repo.list(Fattura)}
            assert fatture["34-FA"].importo_residuo == Decimal("300.00")

    def test_pagamento_manuale_precedente_non_viene_duplicato(
        self, session_factory, scenario
    ):
        """La chiave di idempotenza copre anche la registrazione manuale
        dello stesso incasso (addendum agentico §7)."""
        prepara_mandante(session_factory)
        esegui(session_factory, scenario)

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            chiavi = {pagamento.chiave_idempotenza for pagamento in repo.list(Pagamento)}
        assert "mov-001:32-FA" in chiavi  # stessa chiave che userebbe il manuale

    def test_posizione_chiusa_quando_tutte_le_fatture_escono_dal_flusso(
        self, session_factory
    ):
        """Posizione con una sola fattura, pagata per intero → chiusa."""
        fattura = scenario_demo(OGGI).fatture[0]  # 32-FA, pagata da mov-001
        movimento = scenario_demo(OGGI).movimenti[0]
        scenario = ScenarioTenant(fatture=[fattura], movimenti=[movimento])
        prepara_mandante(session_factory)
        esito = esegui(session_factory, scenario)

        assert esito.posizioni_chiuse == 1
        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            assert repo.list(Posizione)[0].stato is StatoPosizione.CHIUSA

    def test_acconto_poi_saldo_su_run_successive(self, session_factory):
        """Il riconciliatore riceve il RESIDUO come dovuto: un acconto nel
        primo run e il saldo nel secondo chiudono la fattura senza doppi
        conteggi."""
        from irec.domain.porte import MovimentoBancario

        base = scenario_demo(OGGI)
        fattura_34 = next(f for f in base.fatture if f.numero == "34-FA")  # 500.00
        piva = fattura_34.piva_cf_debitore

        def bonifico(id_movimento: str, importo: str) -> MovimentoBancario:
            return MovimentoBancario(
                id_movimento=id_movimento,
                data=OGGI,
                importo=Decimal(importo),
                descrizione="BONIFICO",
                piva_cf_controparte=piva,
            )

        scenario = ScenarioTenant(
            fatture=[fattura_34], movimenti=[bonifico("mov-acconto", "200.00")]
        )
        prepara_mandante(session_factory)
        esegui(session_factory, scenario)

        with session_scope(session_factory) as session:
            fattura = TenantRepository(session, TENANT).list(Fattura)[0]
            assert fattura.stato is StatoFattura.GESTIONE
            assert fattura.importo_residuo == Decimal("300.00")

        scenario.movimenti.append(bonifico("mov-saldo", "300.00"))
        esito = esegui(session_factory, scenario)
        assert esito.pagamenti_registrati == 1  # solo il saldo: l'acconto è noto

        with session_scope(session_factory) as session:
            repo = TenantRepository(session, TENANT)
            fattura = repo.list(Fattura)[0]
            assert fattura.stato is StatoFattura.SALDATA
            assert fattura.importo_residuo == Decimal("0.00")
            importi = sorted(p.importo for p in repo.list(Pagamento))
            assert importi == [Decimal("200.00"), Decimal("300.00")]