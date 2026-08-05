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
| `IREC_ENVIRONMENT` | `dev` | Con `production`, l'assenza di `IREC_JWKS_URL` **o** di `IREC_DATABASE_URL` blocca lo startup (fail-fast): mai un deploy vivo con auth o dati non operativi. |
| `IREC_JWKS_URL` | — | JWKS pubblico con cui IREC verifica i call-token firmati da Mind. Deve essere `https://` (http solo per localhost). **Se assente**: le rotte protette rispondono `503 auth_not_configured` e `/ready` è 503. |
| `IREC_TOKEN_AUDIENCE` | `irec` | Valore atteso del claim `aud`. |
| `IREC_LOG_LEVEL` | `INFO` | Livello di log (output JSON strutturato). |
| `IREC_DATABASE_URL` | — | Postgres di IREC. **Se assente**: le rotte dati rispondono `503 database_not_configured` e `/ready` è 503. Il ruolo deve essere NON privilegiato (superuser = RLS inerte: warning in dev, blocco in production). |
| `IREC_PROVIDERS` | `mock` | Provider dei microservizi esterni: `mock` (sviluppo, scenario demo) o `reali` (M8). In `production` i mock bloccano lo startup. |
| `IREC_APP_DB_PASSWORD` | `irec_app_dev_only` | Solo per `docker compose`: password del ruolo applicativo `irec_app`. |
| `IREC_DB_PASSWORD` | `irec_dev_only` | Solo per `docker compose`: password del Postgres locale. |
| `IREC_TEST_DATABASE_URL` | — | Solo per i test: Postgres su cui rieseguire la suite di persistenza. Se assente, quei test girano solo su SQLite. |

Le variabili possono essere fornite anche via file `.env` (non committato).

## Sviluppo locale

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest          # test
.venv/bin/ruff check .    # lint (con regola di layering TID251)
.venv/bin/mypy            # type check (strict, come in CI)
docker compose up         # servizio + Postgres locale
```

La CI esegue anche la coverage con soglia: `pytest --cov=irec --cov-fail-under=85`.

I test di persistenza girano sia su SQLite (veloce) sia su Postgres, se
`IREC_TEST_DATABASE_URL` è configurata — in CI lo è sempre. Per eseguirli
anche in locale su Postgres:

```bash
IREC_TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:5432/irec_test .venv/bin/pytest
```

Migrazioni del database (richiede `IREC_DATABASE_URL`). Attenzione: le
migrazioni usano l'utente **admin** proprietario delle tabelle (`irec` nel
compose), non il ruolo applicativo `irec_app` con cui gira il servizio:

```bash
.venv/bin/alembic upgrade head
```

## Stato del progetto

- ✅ **M0 — Fondazioni**: scaffold FastAPI, auth call-token via JWKS (aud/exp/entitlement/tenant), logging JSON con `x-correlation-id`, errori `{error, code}`, `/health` + `/ready`, Docker, CI.
- ✅ **M1 — Modello dati e persistenza**: schema Postgres delle entità del PRD con migrazioni Alembic, repository con isolamento per tenant, audit trail, cancellazione GDPR (`DELETE /v1/tenant`), `/ready` che verifica il database. Include il backlog di hardening R1 della review M0.
- ✅ **M2 — Adapter dei microservizi esterni (mock-first)**: porte in [irec/domain/porte.py](irec/domain/porte.py) (`FattureProvider`, `MovimentiProvider`, `Riconciliatore`) e mock pilotabili per scenario in [irec/adapters/mock/](irec/adapters/mock/) (collegamenti caduti, latenza SDI, pagamenti parziali, bonifici cumulativi, duplicati). Più RLS Postgres come quarta rete di isolamento, lint di layering, mypy strict e coverage in CI.
- ✅ **M3 — Ciclo giornaliero di sincronizzazione**: [irec/services/sync.py](irec/services/sync.py) orchestra collegamenti → import fatture (con schedule dal flusso di default) → riconciliazione → stati, tutto idempotente e rieseguibile. Run asincrone via `POST /v1/reconciliations` (202 + `run_id`, `Idempotency-Key`). Provider selezionati da `IREC_PROVIDERS` con fail-fast se production seleziona i mock.
- ✅ **M4 — Motore solleciti** (il cuore del prodotto): [irec/services/invii.py](irec/services/invii.py) con le regole pure in [irec/domain/scheduler.py](irec/domain/scheduler.py) e [irec/domain/calendario.py](irec/domain/calendario.py). Calendario italiano (festivi + Pasquetta, finestra ≤18:00), consolidamento per cliente/canale, canali per pacchetto con salto segnalato, controllo just-in-time, escalation T+45 con preavviso T+44, ripresa delle pause su promessa scaduta. Invio dietro la porta `CanaleInvio` (mock in M4).
- ✅ **M5 — API `/v1` per Mind**: gli endpoint dei tool dell'agente — letture autonome (portafoglio/KPI, aging, fatture, posizioni, storico, prossimi invii, "perché non è partito X", usage) in [irec/api/letture.py](irec/api/letture.py) e azioni con conferma (pausa/riprendi, annulla/forza invio, pagamento manuale, recapiti, flusso, report) in [irec/api/azioni.py](irec/api/azioni.py), con scope `irec.write` e permessi per pacchetto (upsell garbato, non errore freddo). Contratto verificato contro l'implementazione da [tests/test_contract_conformance.py](tests/test_contract_conformance.py).
- ✅ **M6 — Proattività e reporting**: brief giornaliero ([irec/domain/brief.py](irec/domain/brief.py) + `GET /v1/brief`, tono a carico dell'LLM di Mind) e notifiche proattive ([irec/services/notifiche.py](irec/services/notifiche.py): escalation imminente T+44, consenso PSD2, delega AdE) generate nel ciclo giornaliero, deduplicate, consegnate in polling via `GET /v1/notifications` + `POST /v1/notifications/ack`.
- ▶️ Prossima: **M7 — Onboarding guidato** (vedi [docs/ROADMAP.md](docs/ROADMAP.md)).

Decisioni ancora aperte (vedi [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), sezione "Punti aperti"):

- Contratti dei tre microservizi esterni (cassetto fiscale, banche, riconciliazione)
- Titolarità dei canali di invio (email, WhatsApp, PEC, voice)
