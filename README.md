# IREC — Agente Incassi Intelligenti per Mind

Microservizio-agente che automatizza la gestione del ciclo attivo e dei solleciti
di pagamento per le PMI (mandanti), integrato nella piattaforma agenti **Mind**.

IREC è un **capability service**: Mind resta la shell (autenticazione, chat, UI,
entitlement, billing) e invoca IREC dietro un'API HTTP versionata (`/v1`).
Sviluppo e deploy sono completamente separati da Mind: repo, database e runtime propri.

## Documentazione

| Documento | Contenuto |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architettura, componenti esterni, flusso principale, vincoli di integrazione |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Piano di sviluppo in milestone (M0–M8 + fase 2) |
| [CHANGELOG.md](CHANGELOG.md) | Modifiche al contratto `/v1` e al servizio |
| [docs/reviews/](docs/reviews/) | Report del quality gate e sub-piani di remediation |
| [openapi.yaml](openapi.yaml) | **Bozza** del contratto API `/v1` verso Mind (fonte di verità dell'integrazione, da concordare) |

## Stack

Python 3.12 · FastAPI · Pydantic · SQLAlchemy/Alembic · Postgres · Docker.

## Variabili d'ambiente

| Variabile | Default | Note |
|---|---|---|
| `IREC_ENVIRONMENT` | `dev` | Con `production`, l'assenza di `IREC_JWKS_URL` blocca lo startup (fail-fast). |
| `IREC_JWKS_URL` | — | JWKS pubblico con cui IREC verifica i call-token firmati da Mind. Deve essere `https://` (http solo per localhost). **Se assente**: le rotte protette rispondono `503 auth_not_configured` e `/ready` è 503. |
| `IREC_TOKEN_AUDIENCE` | `irec` | Valore atteso del claim `aud`. |
| `IREC_LOG_LEVEL` | `INFO` | Livello di log (output JSON strutturato). |
| `IREC_DATABASE_URL` | — | Postgres di IREC (usata da M1). |

Le variabili possono essere fornite anche via file `.env` (non committato).

## Sviluppo locale

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest          # test
.venv/bin/ruff check .    # lint
docker compose up         # servizio + Postgres locale
```

Migrazioni del database (richiede `IREC_DATABASE_URL`):

```bash
.venv/bin/alembic upgrade head
```

## Stato del progetto

- ✅ **M0 — Fondazioni**: scaffold FastAPI, auth call-token via JWKS (aud/exp/entitlement/tenant), logging JSON con `x-correlation-id`, errori `{error, code}`, `/health` + `/ready`, Docker, CI.
- ✅ **M1 — Modello dati e persistenza**: schema Postgres delle entità del PRD con migrazioni Alembic, repository con isolamento per tenant, audit trail, cancellazione GDPR (`DELETE /v1/tenant`), `/ready` che verifica il database. Include il backlog di hardening R1 della review M0.
- ▶️ Prossima: **M2 — Adapter dei microservizi esterni (mock-first)** (vedi [docs/ROADMAP.md](docs/ROADMAP.md)).

Decisioni ancora aperte (vedi [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), sezione "Punti aperti"):

- Contratti dei tre microservizi esterni (cassetto fiscale, banche, riconciliazione)
- Titolarità dei canali di invio (email, WhatsApp, PEC, voice)
