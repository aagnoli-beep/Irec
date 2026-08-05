"""Generazione e consegna delle notifiche proattive (M6, addendum §6.4).

Le notifiche nascono durante il ciclo giornaliero (escalation imminente,
collegamenti caduti, dati in ritardo) e restano in coda finché Mind non le
legge in polling (`GET /v1/notifications`). La deduplica per `chiave`
evita che la stessa situazione generi una notifica a ogni ciclo.
"""

from datetime import UTC, date, datetime

from irec.adapters.db.models import Fattura, Notifica
from irec.adapters.db.repository import TenantRepository
from irec.domain.enums import StatoFattura, TipoNotifica
from irec.domain.porte import CollegamentoEsterno, StatoCollegamento
from irec.domain.scheduler import escalation_imminente


def _chiave(tipo: TipoNotifica, riferimento: str) -> str:
    return f"{tipo.value}:{riferimento}"


def emetti(
    repo: TenantRepository,
    tipo: TipoNotifica,
    riferimento: str,
    dettaglio: str | None = None,
) -> Notifica | None:
    """Crea una notifica se non ne esiste già una viva con la stessa chiave.

    Restituisce None se già in coda (deduplica). Una notifica con la stessa
    chiave già letta viene "resuscitata" (la situazione si ripresenta): il
    vincolo `uq_notifica_tenant_chiave` tiene una riga sola per situazione.
    """
    chiave = _chiave(tipo, riferimento)
    for notifica in repo.find(Notifica, Notifica.chiave == chiave):
        if notifica.letta_at is None:
            return None  # già in coda
        # Esiste ma è stata letta: la situazione è tornata → riportala in coda.
        notifica.letta_at = None
        notifica.dettaglio = dettaglio
        return notifica
    return repo.add(
        Notifica(tipo=tipo, riferimento=riferimento, chiave=chiave, dettaglio=dettaglio)
    )


def notifica_collegamento(
    repo: TenantRepository,
    tipo: TipoNotifica,
    collegamento: CollegamentoEsterno,
) -> None:
    """Notifica un collegamento caduto (delega AdE o consenso PSD2).

    Il riferimento è il tipo di collegamento (uno solo per tenant), così
    la deduplica tiene finché lo stato non torna attivo e la notifica
    viene marcata come risolta.
    """
    if collegamento.attivo:
        _risolvi(repo, tipo, tipo.value)
        return
    dettaglio = collegamento.stato.value
    if collegamento.scade_il is not None:
        dettaglio = f"{collegamento.stato.value}:{collegamento.scade_il.isoformat()}"
    emetti(repo, tipo, riferimento=tipo.value, dettaglio=dettaglio)


def _risolvi(repo: TenantRepository, tipo: TipoNotifica, riferimento: str) -> None:
    """Marca come letta una notifica la cui situazione è rientrata."""
    chiave = _chiave(tipo, riferimento)
    for notifica in repo.find(Notifica, Notifica.chiave == chiave):
        if notifica.letta_at is None:
            notifica.letta_at = datetime.now(UTC)


def genera_notifiche_escalation(repo: TenantRepository, oggi: date) -> int:
    """Preavvisi T+44: per ogni fattura che domani andrà in escalation,
    una notifica (deduplicata per fattura)."""
    generate = 0
    for fattura in repo.find(Fattura, Fattura.stato == StatoFattura.GESTIONE):
        if escalation_imminente(oggi, fattura.data_scadenza):
            if emetti(
                repo,
                TipoNotifica.ESCALATION_IMMINENTE,
                riferimento=fattura.id,
                dettaglio="domani passa a Recupero Crediti",
            ):
                generate += 1
    return generate


def notifiche_da_consegnare(repo: TenantRepository) -> list[Notifica]:
    """Le notifiche non ancora lette, dalla più recente."""
    non_lette = [n for n in repo.list(Notifica) if n.letta_at is None]
    return sorted(non_lette, key=lambda n: n.created_at, reverse=True)


def marca_lette(repo: TenantRepository, ids: list[str]) -> int:
    """Marca come lette le notifiche indicate (Mind conferma la ricezione)."""
    adesso = datetime.now(UTC)
    marcate = 0
    for notifica in repo.list(Notifica):
        if notifica.id in ids and notifica.letta_at is None:
            notifica.letta_at = adesso
            marcate += 1
    repo.flush()
    return marcate


def conteggio_per_tipo(repo: TenantRepository) -> dict[TipoNotifica, int]:
    """Notifiche non lette raggruppate per tipo (per il brief)."""
    conteggio: dict[TipoNotifica, int] = {}
    for notifica in repo.list(Notifica):
        if notifica.letta_at is None:
            conteggio[notifica.tipo] = conteggio.get(notifica.tipo, 0) + 1
    return conteggio


COLLEGAMENTO_RISOLTO = CollegamentoEsterno(stato=StatoCollegamento.ATTIVO)
