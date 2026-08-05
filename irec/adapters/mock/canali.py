"""Mock del canale di invio (implementa `CanaleInvio`).

Registra i messaggi inviati (per le assertion dei test e per lo sviluppo)
e permette di simulare fallimenti di recapito per canale.
"""

from dataclasses import dataclass, field

from irec.domain.porte import EsitoInvio, MessaggioUscita


@dataclass
class MessaggioRegistrato:
    tenant_id: str
    messaggio: MessaggioUscita


@dataclass
class MockCanaleInvio:
    """Canale che consegna sempre, salvo i canali dichiarati in
    `canali_in_errore` (che falliscono senza sollevare eccezioni,
    come da contratto della porta)."""

    canali_in_errore: set[str] = field(default_factory=set)
    inviati: list[MessaggioRegistrato] = field(default_factory=list)

    def invia(self, tenant_id: str, messaggio: MessaggioUscita) -> EsitoInvio:
        if messaggio.canale in self.canali_in_errore:
            return EsitoInvio(consegnato=False, dettaglio="errore_canale_simulato")
        self.inviati.append(MessaggioRegistrato(tenant_id=tenant_id, messaggio=messaggio))
        return EsitoInvio(consegnato=True, dettaglio="consegnato")
