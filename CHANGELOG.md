# Changelog

Ogni modifica al contratto `/v1` (`openapi.yaml`) va annotata qui: il
contratto è la fonte di verità dell'integrazione con Mind e cambia solo in
modo additivo.

## [Non rilasciato]

### Contratto `/v1` — versione `1.0.0-draft.1` (bozza, non ancora concordata con Mind)

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

- **M1** — modello dati Postgres (mandante, cliente finale, posizione,
  fattura, flusso/step, comunicazione, pagamento, audit log), migrazioni
  Alembic, repository con isolamento per tenant, `/ready` che verifica il
  database. Isolamento su tre livelli: repository, guard `before_flush` e
  foreign key composite `(tenant_id, id)`. Importi quantizzati al centesimo.
  La suite di persistenza gira su SQLite e su Postgres.
- **M0** — fondazioni: verifica del call-token via JWKS, logging JSON senza
  PII con `x-correlation-id`, errori `{error, code}`, `/health` e `/ready`,
  Docker, CI.
