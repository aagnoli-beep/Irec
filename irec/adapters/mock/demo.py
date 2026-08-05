"""Scenario dimostrativo: un mandante realistico per sviluppo e test M3/M4.

Copre i casi che il ciclo giornaliero deve gestire: fattura pagata per
intero, pagamento parziale, un bonifico che copre due fatture, fattura
senza alcun incasso, movimento non abbinabile e bonifico duplicato.
"""

from datetime import date, timedelta
from decimal import Decimal

from irec.adapters.mock.providers import ScenarioTenant
from irec.domain.porte import FatturaEsterna, MovimentoBancario


def scenario_demo(oggi: date) -> ScenarioTenant:
    """Scenario ancorato a `oggi`, così le scadenze restano significative."""
    globex = "09876543210"
    initech = "01234509876"
    fatture = [
        # Pagata per intero da mov-001.
        FatturaEsterna(
            numero="32-FA",
            piva_cf_debitore=globex,
            denominazione_debitore="Globex Corp",
            data_emissione=oggi - timedelta(days=40),
            data_scadenza=oggi - timedelta(days=10),
            importo=Decimal("1220.00"),
            email_debitore="amministrazione@globex.example",
        ),
        # Coperte entrambe dal bonifico cumulativo mov-002 (FIFO):
        # la prima per intero, la seconda solo in parte.
        FatturaEsterna(
            numero="33-FA",
            piva_cf_debitore=initech,
            denominazione_debitore="Initech SRL",
            data_emissione=oggi - timedelta(days=35),
            data_scadenza=oggi - timedelta(days=5),
            importo=Decimal("800.00"),
            email_debitore="conta@initech.example",
        ),
        FatturaEsterna(
            numero="34-FA",
            piva_cf_debitore=initech,
            denominazione_debitore="Initech SRL",
            data_emissione=oggi - timedelta(days=20),
            data_scadenza=oggi + timedelta(days=10),
            importo=Decimal("500.00"),
            email_debitore="conta@initech.example",
        ),
        # Nessun incasso: resta interamente da pagare.
        FatturaEsterna(
            numero="35-FA",
            piva_cf_debitore=globex,
            denominazione_debitore="Globex Corp",
            data_emissione=oggi - timedelta(days=15),
            data_scadenza=oggi + timedelta(days=15),
            importo=Decimal("2440.00"),
            email_debitore="amministrazione@globex.example",
        ),
    ]
    movimenti = [
        MovimentoBancario(
            id_movimento="mov-001",
            data=oggi - timedelta(days=8),
            importo=Decimal("1220.00"),
            descrizione="BONIFICO GLOBEX CORP FT 32-FA",
            piva_cf_controparte=globex,
        ),
        MovimentoBancario(
            id_movimento="mov-002",
            data=oggi - timedelta(days=3),
            importo=Decimal("1000.00"),
            descrizione="BONIFICO INITECH SRL SALDO FATTURE",
            piva_cf_controparte=initech,
        ),
        # Duplicato di mov-002 nello stesso lotto: da processare una volta.
        MovimentoBancario(
            id_movimento="mov-002",
            data=oggi - timedelta(days=3),
            importo=Decimal("1000.00"),
            descrizione="BONIFICO INITECH SRL SALDO FATTURE",
            piva_cf_controparte=initech,
        ),
        # Non abbinabile: controparte sconosciuta.
        MovimentoBancario(
            id_movimento="mov-003",
            data=oggi - timedelta(days=2),
            importo=Decimal("99.00"),
            descrizione="POS ESERCIZIO SCONOSCIUTO",
            piva_cf_controparte=None,
        ),
    ]
    return ScenarioTenant(fatture=fatture, movimenti=movimenti)
