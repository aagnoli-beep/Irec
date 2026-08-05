"""Regole di calendario per gli invii (PRD 5.1). Dominio puro, nessun IO.

Regole confermate dal PRD:
- nessun invio nei giorni festivi: spostato al primo giorno utile
  successivo, mantenendo l'ordine della sequenza;
- nessun invio dopo le 18:00: rinviato alla prima finestra utile (entro
  le 18:00 del giorno utile successivo).

Assunzioni (da confermare col business, documentate qui):
- "giorni utili" = lunedì-venerdì non festivi (il PRD parla di "giorni
  lavorativi");
- le festività sono quelle nazionali italiane + Pasquetta (calcolata);
- l'orologio di riferimento per finestra e festivi è l'ora italiana
  (Europe/Rome); gli istanti sono memorizzati in UTC.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ_ITALIA = ZoneInfo("Europe/Rome")

# Ora di invio pianificata e limite oltre il quale non si invia (PRD 5.1),
# in ora italiana.
ORA_INVIO = time(10, 0)
ORA_LIMITE = time(18, 0)

# Festività nazionali a data fissa: (mese, giorno).
FESTIVITA_FISSE: frozenset[tuple[int, int]] = frozenset(
    {
        (1, 1),  # Capodanno
        (1, 6),  # Epifania
        (4, 25),  # Liberazione
        (5, 1),  # Festa del lavoro
        (6, 2),  # Repubblica
        (8, 15),  # Ferragosto
        (11, 1),  # Ognissanti
        (12, 8),  # Immacolata
        (12, 25),  # Natale
        (12, 26),  # Santo Stefano
    }
)

_SABATO = 5


def pasqua(anno: int) -> date:
    """Domenica di Pasqua (algoritmo di Meeus/Jones/Butcher)."""
    a = anno % 19
    b, c = divmod(anno, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    mese, giorno = divmod(h + length - 7 * m + 114, 31)
    return date(anno, mese, giorno + 1)


def e_festivo(giorno: date) -> bool:
    if (giorno.month, giorno.day) in FESTIVITA_FISSE:
        return True
    return giorno == pasqua(giorno.year) + timedelta(days=1)  # Pasquetta


def e_giorno_utile(giorno: date) -> bool:
    return giorno.weekday() < _SABATO and not e_festivo(giorno)


def prossimo_giorno_utile(giorno: date) -> date:
    while not e_giorno_utile(giorno):
        giorno += timedelta(days=1)
    return giorno


def istante_invio(giorno: date, ora: time = ORA_INVIO) -> datetime:
    """Istante UTC corrispondente a un'ora italiana di un dato giorno."""
    return datetime.combine(giorno, ora, TZ_ITALIA).astimezone(UTC)


def assumi_utc(istante: datetime) -> datetime:
    """Normalizza un datetime letto dal DB: se naive, è UTC.

    Postgres restituisce datetime aware; SQLite (test) li restituisce
    naive — l'assunzione UTC è quella con cui vengono scritti.
    """
    if istante.tzinfo is None:
        return istante.replace(tzinfo=UTC)
    return istante


def applica_finestra(pianificato: datetime) -> datetime:
    """Sposta un istante pianificato dentro la prima finestra utile.

    Valutato in ora italiana: se cade dopo le 18:00 slitta al giorno
    successivo alle ORA_INVIO; se il giorno non è utile slitta al primo
    giorno utile, sempre alle ORA_INVIO. Restituisce UTC.
    """
    locale = assumi_utc(pianificato).astimezone(TZ_ITALIA)
    giorno, ora = locale.date(), locale.time()
    if ora > ORA_LIMITE:
        giorno += timedelta(days=1)
        ora = ORA_INVIO
    giorno_utile = prossimo_giorno_utile(giorno)
    if giorno_utile != giorno:
        ora = ORA_INVIO
    return istante_invio(giorno_utile, ora)
