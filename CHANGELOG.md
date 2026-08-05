# Changelog

Ogni modifica al contratto `/v1` (`openapi.yaml`) va annotata qui: il
contratto è la fonte di verità dell'integrazione con Mind e cambia solo in
modo additivo.

## [Non rilasciato]

### Contratto `/v1` — versione `1.0.0-draft.1` (bozza, non ancora concordata con Mind)

- Implementati `POST /v1/reconciliations` (202 + `run_id`, `Idempotency-Key`
  obbligatoria con retry che restituisce la stessa run) e
  `GET /v1/reconciliations/{run_id}`; contratto allineato all'esito reale
  della run (conteggi e codici anomalia, `status` `queued/running/
  completed/failed`, esempi inclusi). Un run_id altrui risponde 404.

- `DELETE /v1/tenant` richiede lo scope `irec.tenant.delete` e dichiara il
  `503`; documentato il `code` `scope_missing` fra gli errori 403.
- Gli importi di `Invoice` viaggiano come stringhe decimali invece che come
  `number`: un double perderebbe la precisione al centesimo.
- Il filtro `status` di `/v1/invoices` usa `scaduta` (filtro derivato) al
  posto di `overdue`, allineandosi agli stati del dominio.
- Aggiunto `DELETE /v1/tenant`: cancellazione GDPR dei dati del tenant del
  call-token. Risponde con il conteggio delle righe rimosse per tabella.
- Corretto il base-path di `/health` e `/ready`: sono endpoint operativi
  serviti alla root, non sotto `/v1` (server override per-path).
- Documentati i body di risposta di `/health` e `/ready`.
- Bozza iniziale: `/v1/ingest`, `/v1/reconciliations`,
  `/v1/reconciliations/{run_id}`, `/v1/invoices`, `/v1/aging`, `/v1/usage`.

### Servizio

- **M3** — ciclo giornaliero di sincronizzazione (`irec/services/sync.py`):
  verifica collegamenti → import fatture (clienti/posizioni/schedule dal
  flusso di default, reimport idempotente, anomalia su fatture già scadute)
  → riconciliazione (pagamenti idempotenti per movimento+fattura, movimenti
  ridotti di quanto già allocato nei run precedenti) → stati e chiusura
  posizioni, tutto nell'audit trail. Run asincrone su tabella `sync_run`
  (migrazione `ed1582b4f518`, con RLS). Selettore provider mock/reali con
  fail-fast in production; check del ruolo database (superuser = RLS
  inerte): warning in dev, blocco in production; compose con ruolo
  applicativo non privilegiato e servizio su loopback.
- **M2** — porte dei tre microservizi esterni (`irec/domain/porte.py`) e
  adapter mock pilotabili per scenario (`irec/adapters/mock/`): collegamenti
  non attivi, latenza SDI, pagamenti parziali, bonifici cumulativi FIFO,
  movimenti duplicati. RLS Postgres su tutte le tabelle come quarta rete
  dell'isolamento tenant (migrazione `a4b1c9d2e7f0`, fail-closed, verificata
  con un ruolo non privilegiato). Lint di layering (SQLAlchemy vietato fuori
  da `adapters/db/`), `mypy --strict` e soglia di coverage in CI. Naming:
  dominio in italiano, infrastruttura in inglese (`delete_tenant_data`,
  `log_event`).

- **M1** — modello dati Postgres (mandante, cliente finale, posizione,
  fattura, flusso/step, comunicazione, pagamento, audit log), migrazioni
  Alembic, repository con isolamento per tenant, `/ready` che verifica il
  database. Isolamento su tre livelli: repository, guard `before_flush` e
  foreign key composite `(tenant_id, id)`. Importi quantizzati al centesimo.
  La suite di persistenza gira su SQLite e su Postgres.
- **M0** — fondazioni: verifica del call-token via JWKS, logging JSON senza
  PII con `x-correlation-id`, errori `{error, code}`, `/health` e `/ready`,
  Docker, CI.
