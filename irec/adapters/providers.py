"""Selezione dei provider dei tre microservizi esterni.

`IREC_PROVIDERS=mock` (default) usa i mock con lo scenario demo — solo
per sviluppo. `IREC_PROVIDERS=reali` userà gli adapter veri (M8).
Un ambiente di produzione che seleziona i mock è un errore di deploy:
fail-fast allo startup, mai dati finti spacciati per veri.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from irec.adapters.mock.providers import MockBanca, MockCassettoFiscale, MockRiconciliatore
from irec.adapters.mock.runtime import DemoScenari
from irec.config import Settings
from irec.domain.porte import FattureProvider, MovimentiProvider, Riconciliatore


@dataclass(frozen=True)
class ProviderSet:
    fatture: FattureProvider
    movimenti: MovimentiProvider
    riconciliatore: Riconciliatore


def build_providers(settings: Settings) -> ProviderSet:
    if settings.providers == "mock":
        if settings.environment == "production":
            raise RuntimeError(
                "IREC_PROVIDERS=mock non è ammesso in production: "
                "configurare gli adapter reali"
            )
        oggi = datetime.now(UTC).date()
        scenari = DemoScenari(oggi)
        return ProviderSet(
            fatture=MockCassettoFiscale(scenari, oggi=oggi),
            movimenti=MockBanca(scenari, oggi=oggi),
            riconciliatore=MockRiconciliatore(),
        )
    # L'unico altro valore ammesso dal Literal della config è "reali".
    raise NotImplementedError(
        "adapter reali dei microservizi non ancora implementati (M8)"
    )
