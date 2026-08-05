"""Mock dei tre microservizi, pilotabili per scenario.

Ogni tenant ha uno `ScenarioTenant` che descrive lo stato dei collegamenti,
le fatture nel cassetto e i movimenti in banca. I mock riproducono i
comportamenti reali che il ciclo giornaliero deve saper gestire:

- collegamento AdE non attivo / consenso PSD2 scaduto → `ErroreCollegamento`;
- latenza SDI: una fattura emessa da meno di N giorni di calendario non è
  ancora visibile nel cassetto (SLA AdE 2-3 giorni, PRD Cassetto §3);
- pagamenti parziali, un movimento che copre più fatture (FIFO per
  scadenza), movimenti non abbinabili, movimenti duplicati nello stesso
  lotto (processati una volta sola).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from irec.domain.porte import (
    CollegamentoEsterno,
    ErroreCollegamento,
    EsitoRiconciliazione,
    FatturaEsterna,
    MovimentoBancario,
    PagamentoRilevato,
    StatoCollegamento,
)

COLLEGAMENTO_ATTIVO = CollegamentoEsterno(stato=StatoCollegamento.ATTIVO)

# SLA di ricezione dello SDI: i documenti compaiono nel cassetto dopo
# 2-3 giorni dall'emissione (PRD Integrazione Cassetto Fiscale §3).
LATENZA_SDI_GIORNI_DEFAULT = 2


@dataclass
class ScenarioTenant:
    """Stato del mondo esterno per un tenant, come lo vedono i mock."""

    collegamento_ade: CollegamentoEsterno = COLLEGAMENTO_ATTIVO
    consenso_psd2: CollegamentoEsterno = COLLEGAMENTO_ATTIVO
    fatture: list[FatturaEsterna] = field(default_factory=list)
    movimenti: list[MovimentoBancario] = field(default_factory=list)
    latenza_sdi_giorni: int = LATENZA_SDI_GIORNI_DEFAULT


class _MockBase:
    def __init__(self, scenari: dict[str, ScenarioTenant], oggi: date):
        self._scenari = scenari
        # Il "presente" è iniettato: i mock devono essere deterministici.
        self.oggi = oggi

    def _scenario(self, tenant_id: str) -> ScenarioTenant:
        scenario = self._scenari.get(tenant_id)
        if scenario is None:
            raise ErroreCollegamento(
                CollegamentoEsterno(
                    stato=StatoCollegamento.NON_CONFIGURATO,
                    dettaglio="tenant sconosciuto al servizio esterno",
                )
            )
        return scenario


class MockCassettoFiscale(_MockBase):
    """Mock del microservizio AdE/SDI (implementa `FattureProvider`)."""

    def stato_collegamento(self, tenant_id: str) -> CollegamentoEsterno:
        return self._scenario(tenant_id).collegamento_ade

    def recupera_fatture(
        self, tenant_id: str, dal: date, al: date
    ) -> list[FatturaEsterna]:
        scenario = self._scenario(tenant_id)
        if not scenario.collegamento_ade.attivo:
            raise ErroreCollegamento(scenario.collegamento_ade)
        visibili_fino_a = self.oggi - timedelta(days=scenario.latenza_sdi_giorni)
        return [
            fattura
            for fattura in scenario.fatture
            if dal <= fattura.data_emissione <= al
            and fattura.data_emissione <= visibili_fino_a
        ]


class MockBanca(_MockBase):
    """Mock del microservizio bancario Fabrick/PSD2 (implementa `MovimentiProvider`)."""

    def stato_consenso(self, tenant_id: str) -> CollegamentoEsterno:
        return self._scenario(tenant_id).consenso_psd2

    def recupera_movimenti(
        self, tenant_id: str, dal: date, al: date
    ) -> list[MovimentoBancario]:
        scenario = self._scenario(tenant_id)
        if not scenario.consenso_psd2.attivo:
            raise ErroreCollegamento(scenario.consenso_psd2)
        return [
            movimento
            for movimento in scenario.movimenti
            if dal <= movimento.data <= al
        ]


class MockRiconciliatore:
    """Mock del microservizio di riconciliazione (implementa `Riconciliatore`).

    Logica volutamente semplice ma con la stessa forma dell'output reale:
    abbina per P.IVA della controparte e alloca l'importo del movimento
    alle fatture aperte di quel debitore in ordine di scadenza (FIFO,
    tie-break sul numero fattura per determinismo).

    Regole di bordo, pinnate dai test:
    - un movimento senza P.IVA abbinabile o con importo <= 0 resta
      non riconciliato (un importo negativo è uno storno, non un incasso);
    - un movimento duplicato (stesso id) nello stesso lotto è processato
      una volta sola, qualunque sia il suo contenuto;
    - una fattura con importo <= 0 non ha nulla da incassare: compare in
      `fatture_da_pagare` così non svanisce dall'esito (invariante: ogni
      fattura in input è nell'esito, o pagata per intero o da pagare);
    - vale l'assunzione delle porte: `(piva_cf_debitore, numero)` univoco.
    """

    def riconcilia(
        self,
        fatture: list[FatturaEsterna],
        movimenti: list[MovimentoBancario],
    ) -> EsitoRiconciliazione:
        residui: dict[tuple[str, str], Decimal] = {
            (fattura.piva_cf_debitore, fattura.numero): fattura.importo
            for fattura in fatture
        }
        per_debitore: dict[str, list[FatturaEsterna]] = {}
        for fattura in sorted(fatture, key=lambda f: (f.data_scadenza, f.numero)):
            per_debitore.setdefault(fattura.piva_cf_debitore, []).append(fattura)

        pagamenti: list[PagamentoRilevato] = []
        non_riconciliati: list[MovimentoBancario] = []
        visti: set[str] = set()

        for movimento in movimenti:
            if movimento.id_movimento in visti:
                continue
            visti.add(movimento.id_movimento)

            aperte = [
                fattura
                for fattura in per_debitore.get(movimento.piva_cf_controparte or "", [])
                if residui[(fattura.piva_cf_debitore, fattura.numero)] > 0
            ]
            if not aperte or movimento.importo <= 0:
                non_riconciliati.append(movimento)
                continue

            da_allocare = movimento.importo
            for fattura in aperte:
                if da_allocare <= 0:
                    break
                chiave = (fattura.piva_cf_debitore, fattura.numero)
                quota = min(da_allocare, residui[chiave])
                residui[chiave] -= quota
                da_allocare -= quota
                pagamenti.append(
                    PagamentoRilevato(
                        numero_fattura=fattura.numero,
                        piva_cf_debitore=fattura.piva_cf_debitore,
                        id_movimento=movimento.id_movimento,
                        importo_pagato=quota,
                    )
                )

        da_pagare = tuple(
            fattura
            for fattura in fatture
            if residui[(fattura.piva_cf_debitore, fattura.numero)] > 0
            or fattura.importo <= 0
        )
        return EsitoRiconciliazione(
            pagamenti=tuple(pagamenti),
            fatture_da_pagare=da_pagare,
            movimenti_non_riconciliati=tuple(non_riconciliati),
        )
