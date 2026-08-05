"""Accesso ai dati con isolamento multi-tenant obbligatorio.

Tutto il codice applicativo deve passare da `TenantRepository`: il
`tenant_id` arriva dal call-token e viene applicato a ogni lettura,
scrittura e cancellazione. È la "RLS di IREC" (brief §2-§3).
"""

from typing import TypeVar

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session

from irec.adapters.db.models import (
    AuditLog,
    Base,
    ClienteFinale,
    Comunicazione,
    Fattura,
    Flusso,
    FlussoStep,
    Mandante,
    Pagamento,
    Posizione,
    TenantScoped,
)
from irec.domain.enums import TipoEvento

ModelT = TypeVar("ModelT", bound=Base)

# Ordine di cancellazione GDPR: dalle foglie alla radice, così le FK
# reggono anche sui database senza ON DELETE CASCADE attivo.
_ORDINE_CANCELLAZIONE: tuple[type[Base], ...] = (
    AuditLog,
    Pagamento,
    Comunicazione,
    FlussoStep,
    Flusso,
    Fattura,
    Posizione,
    ClienteFinale,
    Mandante,
)


class TenantRepository:
    """Repository legato a un singolo tenant.

    Non espone la sessione: ogni operazione passa dal filtro sul tenant.
    """

    def __init__(self, session: Session, tenant_id: str):
        if not tenant_id:
            raise ValueError("tenant_id obbligatorio")
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def select(self, model: type[ModelT]) -> Select[tuple[ModelT]]:
        """Query di partenza già filtrata per tenant. Unico modo di leggere."""
        return select(model).where(model.tenant_id == self._tenant_id)

    def get(self, model: type[ModelT], entity_id: str) -> ModelT | None:
        """Lettura per id: un id di un altro tenant restituisce None."""
        return self._session.scalars(
            self.select(model).where(model.id == entity_id)
        ).one_or_none()

    def list(self, model: type[ModelT]) -> list[ModelT]:
        return list(self._session.scalars(self.select(model)))

    def add(self, entity: TenantScoped) -> TenantScoped:
        """Inserisce forzando il tenant del repository.

        Un tenant_id già valorizzato e diverso è un errore di programmazione,
        non un valore da sovrascrivere in silenzio.
        """
        existing = getattr(entity, "tenant_id", None)
        if existing and existing != self._tenant_id:
            raise ValueError(
                "tentativo di scrivere su un tenant diverso da quello del token"
            )
        entity.tenant_id = self._tenant_id
        self._session.add(entity)
        return entity

    def log_evento(
        self,
        tipo: TipoEvento,
        entita: str,
        entita_id: str,
        *,
        stato_precedente: str | None = None,
        stato_successivo: str | None = None,
        operatore: str | None = None,
        dettaglio: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditLog:
        """Aggiunge una riga all'audit trail (append-only)."""
        evento = AuditLog(
            tipo=tipo,
            entita=entita,
            entita_id=entita_id,
            stato_precedente=stato_precedente,
            stato_successivo=stato_successivo,
            operatore=operatore,
            dettaglio=dettaglio,
            correlation_id=correlation_id,
        )
        self.add(evento)
        return evento

    def flush(self) -> None:
        self._session.flush()

    def cancella_tenant(self) -> dict[str, int]:
        """Cancellazione GDPR di tutti i dati del tenant.

        Restituisce il conteggio per tabella. È l'unica operazione che
        rimuove righe di audit: serve al diritto di cancellazione.
        """
        conteggi: dict[str, int] = {}
        for model in _ORDINE_CANCELLAZIONE:
            result = self._session.execute(
                delete(model).where(model.tenant_id == self._tenant_id)
            )
            conteggi[model.__tablename__] = result.rowcount or 0
        return conteggi
