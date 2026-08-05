"""Costruttori di entità per i test. Nessuna logica: solo dati coerenti."""

from datetime import date
from decimal import Decimal

from irec.adapters.db.models import ClienteFinale, Fattura, Mandante, Posizione
from irec.domain.enums import Pacchetto, StatoFattura


def make_mandante(**overrides) -> Mandante:
    valori = {
        "ragione_sociale": "Acme SRL",
        "partita_iva": "01234567890",
        "pacchetto": Pacchetto.VALUE,
        "alias_email": "acme@incassi-intelligenti.it",
    }
    valori.update(overrides)
    return Mandante(**valori)


def make_cliente(mandante_id: str, **overrides) -> ClienteFinale:
    valori = {
        "mandante_id": mandante_id,
        "denominazione": "Globex Corp",
        "piva_cf": "09876543210",
        "email": "amministrazione@globex.example",
    }
    valori.update(overrides)
    return ClienteFinale(**valori)


def make_posizione(cliente_id: str, **overrides) -> Posizione:
    valori = {"cliente_id": cliente_id}
    valori.update(overrides)
    return Posizione(**valori)


def make_fattura(posizione_id: str, cliente_id: str, **overrides) -> Fattura:
    valori = {
        "posizione_id": posizione_id,
        "cliente_id": cliente_id,
        "numero": "32-FA",
        "data_emissione": date(2026, 7, 1),
        "data_scadenza": date(2026, 8, 31),
        "importo": Decimal("1220.00"),
        "importo_residuo": Decimal("1220.00"),
        "stato": StatoFattura.GESTIONE,
    }
    valori.update(overrides)
    return Fattura(**valori)
