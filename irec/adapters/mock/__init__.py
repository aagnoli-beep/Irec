"""Implementazioni mock dei tre microservizi esterni (M2, mock-first).

Permettono di sviluppare e testare il ciclo giornaliero (M3) e il motore
solleciti (M4) senza le spec reali. In M8 verranno sostituite dagli
adapter veri senza toccare il dominio: il contratto sono le porte in
`irec/domain/porte.py`.
"""

from irec.adapters.mock.providers import (
    MockBanca,
    MockCassettoFiscale,
    MockRiconciliatore,
    ScenarioTenant,
)

__all__ = [
    "MockBanca",
    "MockCassettoFiscale",
    "MockRiconciliatore",
    "ScenarioTenant",
]
