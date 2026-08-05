# Changelog

Ogni modifica al contratto `/v1` (`openapi.yaml`) va annotata qui: il
contratto è la fonte di verità dell'integrazione con Mind e cambia solo in
modo additivo.

## [Non rilasciato]

### Contratto `/v1` — versione `1.0.0-draft.1` (bozza, non ancora concordata con Mind)

- **M6** — endpoint proattivi: `GET /v1/brief` (KPI + azioni proposte, max 3,
  tono a carico dell'LLM di Mind), `GET /v1/notifications` (coda non letta,
  polling) e `POST /v1/notifications/ack` (conferma ricezione). L'esito della
  run espone `notifiche_generate`. Consegna via polling; il webhook resta da
  concordare col team Mind.

- **M5** — implementati gli endpoint dei tool dell'agente: letture autonome
  (`GET /v1/portfolio`, `/aging`, `/invoices`, `/positions/{id}`,
  `/invoices/{id}/history`, `/invoices/{id}/next`,
  `/communications/{id}/explain`, `/usage`) e azioni con conferma, tutte con
  scope `irec.write` (`POST /v1/invoices/{id}/pause`, `/resume`,
  `/communications/{id}/cancel`, `/force`, `/invoices/{id}/payments`,
  `PATCH /v1/clients/{id}/contacts`, `PUT /v1/flow`, `POST /v1/report`).
  Permessi per pacchetto enforced nel servizio (Entry che personalizza il
  flusso → `403 upgrade_required` con invito garbato). Nuova risposta
  `Conflict` per gli stati incompatibili. Il contratto è ora verificato
  contro l'implementazione da `tests/test_contract_conformance.py` (path +
  metodo); `/ingest` resta l'unico endpoint dichiarato ma non implementato
  (valutazione M8). `Idempotency-Key` del pagamento manuale via header;
  importi come stringhe decimali su tutti gli endpoint (aging incluso).

- Implementati `POST /v1/reconciliations` (202 + `run_id`, `Idempotency-Key`
  obbligatoria con retry che restituisce la stessa run) e
  `GET /v1/reconciliations/{run_id}`; contratto allineato all'esito reale
  della run (conteggi e codici anomalia, `status` `queued/running/
  completed/failed`, esempi inclusi). Un run_id altrui risponde 404.
- Convenzione di lingua del contratto: envelope in inglese — i campi della
  run sono `result`/`error` (era `risultato`/`errore`); il dominio dentro
  `result` resta in italiano.
- `POST /v1/reconciliations`: `409 run_in_progress` con una run attiva per
  il tenant; `Idempotency-Key` limitata a 128 caratteri
  (`400 invalid_idempotency_key`); race di retry concorrenti risolta con la
  stessa run. 403 dichiarato anche sul GET; `providers_not_configured` nel
  catalogo dei 503.

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

- **M6** — proattività e reporting: brief giornaliero (`irec/domain/brief.py`
  puro, tono ibrido con positivo per primo e tetto di 2-3 azioni;
  `irec/services/reporting.py` lo unisce ai KPI) e notifiche proattive
  (`irec/services/notifiche.py`): escalation imminente T+44, consenso PSD2 da
  rinnovare, delega AdE caduta — generate nel ciclo giornaliero, deduplicate
  per chiave (la stessa situazione non rigenera; una notifica letta viene
  resuscitata se la situazione torna), consegnate in polling. Tabella
  `notifica` con RLS (migrazione `26814ec71749`).
- **M5** — API `/v1` complete per i tool di Mind: letture
  (`irec/services/letture.py`: KPI, aging, spiegazione comunicazioni,
  consumo) e azioni con conferma (`irec/services/azioni.py`: pausa/riprendi,
  annulla/forza invio, pagamento manuale idempotente, aggiorna recapiti,
  sostituisci flusso, report), con i permessi per pacchetto enforced nel
  servizio. Verifica automatica contratto↔implementazione in CI.
- **M4** — motore solleciti (`irec/services/invii.py`, regole pure in
  `irec/domain/scheduler.py` e `calendario.py`): calendario italiano
  (festivi nazionali + Pasquetta, finestra ≤18:00 ora italiana, invii
  spostati al primo giorno utile), consolidamento per cliente/canale (un
  messaggio elenca le fatture dovute), canali per pacchetto con salto
  segnalato (`stato saltata`, migrazione `b7c3d9e1f402`), controllo
  just-in-time, escalation T+45 (mail a Recupero Crediti + mandante,
  fattura → Insoluto) con preavviso T+44, ripresa delle pause su promessa
  scaduta, ricalcolo schedule su modifica scadenza. Invio effettivo dietro
  la porta `CanaleInvio` (mock in M4, reale in M8). L'esito della run
  espone i conteggi della fase invii.
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
