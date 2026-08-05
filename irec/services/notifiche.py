"""Generazione e consegna delle notifiche proattive (M6, addendum §6.4).

Le notifiche nascono durante il ciclo giornaliero (escalation imminente,
collegamenti caduti) e restano in coda finché Mind non le legge in polling
(`GET /v1/notifications`). La deduplica per `chiave` evita che la stessa
situazione generi una notifica a ogni ciclo.

I filtri "non letta" e "per id" sono spinti al DB con `repo.find` (usano
l'indice `ix_notifica_tenant_letta`), non scanditi in Python.
"""

from datetime import UTC, date, datetime
from enum import Enum, auto

from irec.adapters.db.models import Fattura, Notifica
from irec.adapters.db.repository import TenantRepository
from irec.domain.enums import StatoFattura, TipoNotifica
from irec.domain.porte import CollegamentoEsterno
from irec.domain.scheduler import escalation_imminente

# Dettagli in prosa: sono destinati all'utente via l'LLM di Mind, non al
# parsing. Costanti per non spargere magic string.
DETTAGLIO_ESCALATION_IMMINENTE = "domani passa a Recupero Crediti"


class EsitoEmissione(Enum):
    """Cosa ha fatto `emetti`: distingue una notifica nuova da una risorta."""

    CREATA = auto()
    RESUSCITATA = auto()
    GIA_IN_CODA = auto()


def _chiave(tipo: TipoNotifica, riferimento: str) -> str:
    return f"{tipo.value}:{riferimento}"


def emetti(
    repo: TenantRepository,
    tipo: TipoNotifica,
    riferimento: str,
    dettaglio: str | None = None,
) -> EsitoEmissione:
    """Mette in coda una notifica, deduplicando per chiave.

    - se ce n'è già una viva con la stessa chiave → `GIA_IN_CODA`;
    - se ce n'è una già letta → la "resuscita" (`RESUSCITATA`): la
      situazione è tornata, e il vincolo `uq_notifica_tenant_chiave`
      impone una sola riga per situazione;
    - altrimenti la crea (`CREATA`).
    """
    chiave = _chiave(tipo, riferimento)
    for notifica in repo.find(Notifica, Notifica.chiave == chiave):
        if notifica.letta_at is None:
            return EsitoEmissione.GIA_IN_CODA
        notifica.letta_at = None
        notifica.dettaglio = dettaglio
        return EsitoEmissione.RESUSCITATA
    repo.add(
        Notifica(tipo=tipo, riferimento=riferimento, chiave=chiave, dettaglio=dettaglio)
    )
    return EsitoEmissione.CREATA


def notifica_collegamento(
    repo: TenantRepository,
    tipo: TipoNotifica,
    collegamento: CollegamentoEsterno,
) -> None:
    """Notifica un collegamento caduto (delega AdE o consenso PSD2).

    Il riferimento è il tipo di collegamento (uno solo per tenant), così
    la deduplica tiene finché lo stato non torna attivo, e allora la
    notifica viene marcata come risolta.
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
    for notifica in repo.find(
        Notifica, Notifica.chiave == chiave, Notifica.viva()
    ):
        notifica.letta_at = datetime.now(UTC)


def genera_notifiche_escalation(repo: TenantRepository, oggi: date) -> int:
    """Preavvisi T+44: per ogni fattura che domani andrà in escalation, una
    notifica (deduplicata per fattura). Conta solo quelle nuove."""
    generate = 0
    for fattura in repo.find(Fattura, Fattura.stato == StatoFattura.GESTIONE):
        if escalation_imminente(oggi, fattura.data_scadenza):
            esito = emetti(
                repo,
                TipoNotifica.ESCALATION_IMMINENTE,
                riferimento=fattura.id,
                dettaglio=DETTAGLIO_ESCALATION_IMMINENTE,
            )
            if esito is EsitoEmissione.CREATA:
                generate += 1
    return generate


def notifiche_da_consegnare(repo: TenantRepository) -> list[Notifica]:
    """Le notifiche non ancora lette, dalla più recente."""
    non_lette = repo.find(Notifica, Notifica.viva())
    return sorted(non_lette, key=lambda n: n.created_at, reverse=True)


def marca_lette(repo: TenantRepository, ids: list[str]) -> int:
    """Marca come lette le notifiche indicate (Mind conferma la ricezione).

    Solo le notifiche del proprio tenant e non ancora lette: un id di un
    altro tenant o già letto è un no-op (idempotente)."""
    if not ids:
        return 0
    adesso = datetime.now(UTC)
    marcate = 0
    for notifica in repo.find(
        Notifica, Notifica.id.in_(ids), Notifica.viva()
    ):
        notifica.letta_at = adesso
        marcate += 1
    repo.flush()
    return marcate


def conteggio_per_tipo(repo: TenantRepository) -> dict[TipoNotifica, int]:
    """Notifiche non lette raggruppate per tipo (per il brief)."""
    conteggio: dict[TipoNotifica, int] = {}
    for notifica in repo.find(Notifica, Notifica.viva()):
        conteggio[notifica.tipo] = conteggio.get(notifica.tipo, 0) + 1
    return conteggio
