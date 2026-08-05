"""Mock come provider di runtime per gli ambienti di sviluppo.

Fuori dai test serve un set di provider utilizzabile per qualunque
tenant: questa mappa genera lo scenario demo alla prima richiesta.
La selezione mock/reali avviene in `irec/adapters/providers.py`, con
fail-fast se un ambiente di produzione seleziona i mock.
"""

from datetime import date

from irec.adapters.mock.demo import scenario_demo
from irec.adapters.mock.providers import ScenarioTenant


class DemoScenari(dict[str, ScenarioTenant]):
    """Mappa tenant→scenario che crea lo scenario demo on-demand."""

    def __init__(self, oggi: date):
        super().__init__()
        self._oggi = oggi

    def __missing__(self, tenant_id: str) -> ScenarioTenant:
        scenario = scenario_demo(self._oggi)
        self[tenant_id] = scenario
        return scenario

    def get(  # type: ignore[override]
        self, tenant_id: str, default: ScenarioTenant | None = None
    ) -> ScenarioTenant:
        # `_MockBase._scenario` usa .get(): deve passare dal __missing__.
        return self[tenant_id]
