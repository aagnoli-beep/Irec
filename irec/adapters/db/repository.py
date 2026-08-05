"""Accesso ai dati con isolamento multi-tenant obbligatorio.

Tutto il codice applicativo deve passare da `TenantRepository`: il
`tenant_id` arriva dal call-token e viene applicato a ogni lettura,
scrittura e cancellazione. È la "RLS di IREC" (brief §2-§3).

L'isolamento poggia su tre livelli indipendenti:
1. le query del repository, filtrate per tenant;
2. il guard `before_flush` di questo modulo, che rifiuta ogni riga in
   uscita verso un tenant diverso — anche se il `tenant_id` è stato
   alterato dopo l'`add()` o su un'entità già caricata;
3. le foreign key composite `(tenant_id, id)` dello schema, che rendono
   impossibile a livello di database un arco fra tenant diversi.
"""

from typing import TypeVar

from sqlalchemy import Select, delete, event, inspect, select
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

ModelT = TypeVar("ModelT", bound=TenantScoped)

# Chiave con cui il tenant corrente viene inciso nella sessione, così che
# il guard `before_flush` possa verificarlo senza conoscere il repository.
TENANT_SESSION_KEY = "irec_tenant_id"

# Ordine di cancellazione GDPR: dalle foglie alla radice, così le FK
# reggono anche sui database senza ON DELETE CASCADE attivo.
_ORDINE_CANCELLAZIONE: tuple[type[TenantScoped], ...] = (
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

# Una tabella non elencata resterebbe fuori dal diritto di cancellazione
# mentre la rotta risponde "tenant cancellato": meglio rompere l'import.
_TABELLE_COPERTE = {model.__tablename__ for model in _ORDINE_CANCELLAZIONE}
_TABELLE_MANCANTI = set(Base.metadata.tables) - _TABELLE_COPERTE
if _TABELLE_MANCANTI:
    raise RuntimeError(
        f"cancellazione GDPR incompleta, tabelle non coperte: {sorted(_TABELLE_MANCANTI)}"
    )


class TenantViolation(PermissionError):
    """Tentativo di scrivere o spostare dati fuori dal tenant del call-token."""


@event.listens_for(Session, "before_flush")
def _blocca_scritture_fuori_tenant(session: Session, flush_context, instances) -> None:
    """Ultima barriera prima del flush: nessuna riga esce verso un altro tenant.

    Copre i casi che il controllo in `add()` non vede: `tenant_id`
    assegnato dopo l'add, o modificato su un'entità già caricata.
    """
    atteso = session.info.get(TENANT_SESSION_KEY)
    if atteso is None:
        return
    for entita in list(session.new) + list(session.dirty):
        if not isinstance(entita, TenantScoped):
            continue
        if entita.tenant_id != atteso:
            raise TenantViolation(
                "scrittura fuori dal tenant del call-token"
            )
        storico = inspect(entita).attrs.tenant_id.history
        if storico.deleted and storico.deleted[0] != atteso:
            raise TenantViolation("il tenant_id di una riga esistente non è modificabile")


class TenantRepository:
    """Repository legato a un singolo tenant.

    Non espone la sessione: ogni operazione passa dal filtro sul tenant.
    """

    def __init__(self, session: Session, tenant_id: str):
        if not tenant_id:
            raise ValueError("tenant_id obbligatorio")
        self._session = session
        self._tenant_id = tenant_id
        session.info[TENANT_SESSION_KEY] = tenant_id

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
        """Tutte le righe del modello appartenenti al tenant."""
        return list(self._session.scalars(self.select(model)))

    def add(self, entity: ModelT) -> ModelT:
        """Inserisce forzando il tenant del repository.

        Un tenant_id già valorizzato e diverso è un errore di programmazione,
        non un valore da sovrascrivere in silenzio.
        """
        existing = getattr(entity, "tenant_id", None)
        if existing and existing != self._tenant_id:
            raise TenantViolation(
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
        """Rende visibili gli id generati senza chiudere la transazione."""
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
