# Changelog

Ogni modifica al contratto `/v1` (`openapi.yaml`) va annotata qui: il
contratto è la fonte di verità dell'integrazione con Mind e cambia solo in
modo additivo.

## [Non rilasciato]

### Contratto `/v1` — versione `1.0.0-draft.1` (bozza, non ancora concordata con Mind)

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
  database.
- **M0** — fondazioni: verifica del call-token via JWKS, logging JSON senza
  PII con `x-correlation-id`, errori `{error, code}`, `/health` e `/ready`,
  Docker, CI.
