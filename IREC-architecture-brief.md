# IREC — Brief architetturale (microservizio-agente per Mind)

> IREC = *Incassi/pagamenti REconciliation + Crediti*: riconciliazione incassi↔pagamenti,
> stato fatture (incassate / pagate / scadute), aging, supporto recupero crediti.
> Sarà un "agente" attivabile/disattivabile per abbonamento dentro **Mind**, ma
> **sviluppato e deployato separatamente** (repo, DB, runtime propri).

---

## 0. Principio guida

IREC è un **capability service**, NON un bot dentro Mind. Mind resta la *shell*
(autenticazione, chat, UI, entitlement, billing); IREC fornisce la capacità
dietro un'**API HTTP versionata** (`/v1`). Regole non negoziabili:

- **Nessun accesso diretto al database di Mind** (Supabase). Nessun runtime condiviso.
- Contract-first: la fonte di verità dell'integrazione è un file **`openapi.yaml`** (OpenAPI 3.1) concordato con Mind.
- Confine netto → **se IREC cade, Mind continua a funzionare** (obiettivo esplicito).

---

## 1. Come Mind invoca IREC (due superfici)

1. **Tool-proxy (primaria).** Mind espone al proprio LLM di chat dei *tool*
   (`irec_overdue_invoices`, `irec_reconcile`, `irec_aging`, …) i cui handler
   chiamano l'API di IREC e restituiscono JSON che l'LLM narra all'utente.
   L'utente "parla" con l'agente IREC dentro la chat di Mind. → Lato IREC = solo API.
2. **UI embedded (opzionale, fase 2).** Una vista dati (tabelle/dashboard)
   servita da IREC ed embeddata in Mind (route/iframe), per ciò che non sta in
   una bolla di chat. Stesso modello del bottone "Gestione ticket" → Odoo.

Per un prodotto di riconciliazione servono entrambe, ma **parti dalla 1**.

---

## 2. Autenticazione (il confine) — token firmato da Mind, verificato da IREC

- Ogni chiamata Mind→IREC porta `Authorization: Bearer <call-token>`.
- Il **call-token è coniato da Mind** (durata breve, ~120s) con claim:
  `sub` (user id), `tenant_id`, `entitlement` (es. `"irec:pro"`), `scope`,
  `aud: "irec"`, `jti`, `exp`. **Firma asimmetrica**: Mind firma con chiave
  privata, IREC verifica con **JWKS pubblico** → IREC non detiene alcun segreto
  capace di forgiare token.
- IREC (Policy Enforcement Point) verifica: firma, `aud == "irec"`, `exp`,
  e **presenza del claim `entitlement`**. Poi ricava `tenant_id` dal token e
  **filtra OGNI query per `tenant_id`** — è la RLS di IREC.
- ⚠️ **Non fidarti dei gruppi Keycloak del realm** per autorizzare: il realm `CS`
  è condiviso con altre app. L'entitlement lo **decide Mind** e viaggia nel claim.

*Alternativa "riusa il plumbing Odoo":* client Keycloak dedicato `irec-service`
+ `client_credentials` + `token-exchange` (`requested_subject = userId`); IREC
verifica il JWT contro il JWKS di Keycloak **con pin di `azp`/`aud`**. Funziona,
ma l'entitlement resta comunque responsabilità di Mind (claim o call a Mind).

---

## 3. Isolamento dati (tenant)

- **Database Postgres proprio di IREC.** Ogni tabella con `tenant_id` (+ `user_id`
  dove serve). Valuta RLS Postgres anche in IREC.
- Ingest dati finanziari: upload file (CSV / XLSX / `camt.053` estratti conto)
  e/o connettori (Odoo, Fatture in Cloud, banca). **IREC possiede ingestion e storage.**
- GDPR: endpoint di cancellazione tenant/utente (o consumo di eventi di
  cancellazione da Mind); FK con cascade.

---

## 4. Resilienza (Mind non si rompe se IREC cade)

- Lato **Mind**: timeout stretto (5–10s) + **circuit breaker** + fallback come
  messaggio chat normale ("IREC momentaneamente non disponibile"), mai un 500.
- Lato **IREC**: endpoint `/health` e `/ready`; run lunghe **async**
  (`202 { run_id }`, poi poll o webhook); **`Idempotency-Key`** sulle mutazioni
  (retry sicuri, niente doppia riconciliazione).

---

## 5. Contratto API — esempio `/v1` (definiscilo in `openapi.yaml`)

| Metodo | Path | Scopo |
|---|---|---|
| POST | `/v1/ingest` | Carica dataset (fatture, incassi, pagamenti). `Idempotency-Key`. |
| POST | `/v1/reconciliations` | Avvia una run → `202 { run_id }`. |
| GET | `/v1/reconciliations/{run_id}` | Stato + risultato (match / unmatched / confidence). |
| GET | `/v1/invoices?status=overdue&as_of=YYYY-MM-DD` | Fatture scadute/non incassate. |
| GET | `/v1/aging` | Buckets di aging del credito. |
| GET | `/v1/usage` | Consumo per il billing (Mind lo aggrega). |
| GET | `/health`, `/ready` | Liveness / readiness. |

- Errori JSON `{ error, code }`; `400` client, `402/403` entitlement, `5xx` server.
- Header `x-correlation-id`: ricevi da Mind e ri-emetti nei log.
- Versioning `/v1`; solo cambi additivi; policy di deprecation.

---

## 6. Stack e layout consigliati per IREC

- **Python 3.12 + FastAPI + Pydantic + Postgres (SQLAlchemy/Alembic) + pandas.**
  pandas è ideale per matching, aging buckets, fuzzy-matching incassi↔fatture.
  *(Alternativa per uniformità col team Mind: TypeScript + Fastify + Drizzle.)*
- **Docker**, deploy su host separato (Fly.io / Render / Cloud Run / VM) — **non** su Supabase.
- Layout:
  ```
  irec-service/
    openapi.yaml            # contratto = fonte di verità
    api/                    # rotte FastAPI (dipende da domain)
    domain/                 # MOTORE riconciliazione PURO e testabile (no IO)
    adapters/               # db, connettori esterni (Odoo, banca…)
    auth/                   # verifica call-token (JWKS) + tenant scoping
    infra/                  # docker, migrazioni, ci
    tests/                  # test sul motore puro dal giorno 1
  ```
- Log JSON strutturati **senza PII** (`tenant_id` troncato) + `correlation_id`.

---

## 7. Cosa NON fare (lista nera)

- ❌ Connettersi al DB di Mind (Supabase) direttamente.
- ❌ Autorizzare in base ai gruppi del realm Keycloak (realm condiviso) — usa il claim `entitlement`.
- ❌ Detenere in IREC un segreto capace di forgiare token (verifica solo a chiave pubblica/JWKS).
- ❌ Accoppiarsi allo schema interno di Mind — parla SOLO via `/v1` (OpenAPI).
- ❌ Run sincrone lunghe che tengono aperta la connessione (usa async + run_id).

---

## 8. Cosa serve DA Mind (lavoro lato Mind, non IREC)

- Chiave di firma dei call-token (o client Keycloak `irec-service`).
- Adapter: tool `_shared/tools/irec-*.ts` (o edge function `irec-proxy`) che
  conia il token e chiama IREC con timeout + circuit breaker.
- Entitlement: IREC come capability gated per categoria/abbonamento
  (`user_categories`) + kill-switch in `platform_settings`.
- Billing: ingest periodico di `GET /v1/usage`.
- Un **registro "external agents"** riusabile, così il prossimo microservizio-agente
  si aggancia con lo stesso contratto (obiettivo modularità).
