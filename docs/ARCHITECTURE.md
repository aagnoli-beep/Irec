# IREC — Architettura

> Consolida: PRD "Incassi Intelligenti" (Overview, Cassetto Fiscale, Fabrick,
> AI Conversazionale, Addendum Agentico, Gestione Utenze), brief architetturale
> Mind e indicazioni successive del team.

## 1. Principio guida

IREC è un **capability service**, non un bot dentro Mind. Regole non negoziabili:

- Nessun accesso diretto al database di Mind (Supabase). Nessun runtime condiviso.
- Contract-first: la fonte di verità dell'integrazione è `openapi.yaml` (OpenAPI 3.1) concordato con Mind.
- Confine netto: **se IREC cade, Mind continua a funzionare.**

## 2. Componenti

IREC **non implementa** né il recupero dati né la riconciliazione: sono
microservizi esterni che orchestra. IREC possiede l'automazione a valle
(stati, scheduler solleciti, escalation, reporting).

| Componente | Responsabilità | Cosa espone a IREC |
|---|---|---|
| **Mind** (shell) | Auth, chat, UI, entitlement, billing | Invoca IREC via tool-proxy con call-token |
| **MS Cassetto Fiscale (AdE/SDI)** | Recupero fatture elettroniche | API fatture (XML formato AdE) + API stato collegamento (check quotidiano) |
| **MS Banche (Fabrick/PSD2)** | Recupero movimenti bancari | API entrate/uscite + API stato collegamento (consenso PSD2 da riautorizzare periodicamente) |
| **MS Riconciliazione** | Matching incassi↔fatture | Input: movimenti (formato API banca) + fatture (XML AdE) → Output: fatture pagate / da pagare |
| **IREC** (questo repo) | Orchestrazione + automazione recupero crediti | API `/v1` verso Mind; scheduler; stato fatture/posizioni; motore solleciti; audit trail |

## 3. Integrazione con Mind

### 3.1 Superfici

1. **Tool-proxy (primaria, fase 1).** Mind espone al proprio LLM tool
   (`irec_*`) i cui handler chiamano l'API di IREC e narrano il JSON all'utente.
   Lato IREC = solo API.
2. **UI embedded (fase 2).** Vista dati (tabelle/dashboard) servita da IREC
   ed embeddata in Mind (route/iframe).

### 3.2 Autenticazione — il confine

- Ogni chiamata Mind→IREC porta `Authorization: Bearer <call-token>`.
- Call-token **coniato da Mind** (durata ~120s), claim: `sub`, `tenant_id`,
  `entitlement` (es. `"irec:pro"`), `scope`, `aud: "irec"`, `jti`, `exp`.
- Firma asimmetrica: Mind firma con chiave privata, IREC verifica con **JWKS
  pubblico**. IREC non detiene alcun segreto capace di forgiare token.
- IREC (Policy Enforcement Point) verifica firma, `aud == "irec"`, `exp`,
  presenza di `entitlement`; poi ricava `tenant_id` dal token e **filtra ogni
  query per `tenant_id`** (la "RLS di IREC").
- ⚠️ Mai autorizzare in base ai gruppi Keycloak del realm (`CS` è condiviso):
  l'entitlement lo decide Mind e viaggia nel claim.

### 3.3 Resilienza

- Lato Mind: timeout 5–10s + circuit breaker + fallback in chat, mai un 500.
- Lato IREC: `/health` e `/ready`; run lunghe **async** (`202 { run_id }` +
  poll o webhook); **`Idempotency-Key`** su tutte le mutazioni.
- `x-correlation-id`: ricevuto da Mind, ri-emesso in ogni log.
- Log JSON strutturati **senza PII** (`tenant_id` troncato).

## 4. Flusso principale — ciclo giornaliero (per tenant/mandante)

1. **Verifica collegamenti** — API di stato cassetto fiscale + API di stato
   banca. Collegamento caduto (delega AdE, consenso PSD2 scaduto) → notifica
   proattiva con guida al rinnovo. L'autenticazione forte (SPID/CIE, delega
   AdE, credenziali bancarie) resta sempre all'utente su portali esterni.
2. **Recupero dati** — API fatture + API entrate/uscite del cliente.
3. **Riconciliazione** — invia fatture + movimenti al MS Riconciliazione,
   attende l'output (fatture pagate / da pagare).
4. **Aggiornamento stati** —
   - pagamento totale → fattura **Saldata**, annulla solleciti residui
     (US-08: mai sollecitare chi ha pagato);
   - pagamento parziale → resta in **Gestione**, residuo aggiornato;
   - nuova fattura → crea/aggiorna la **Posizione**, genera lo schedule;
   - tutte le fatture saldate → posizione chiusa.
5. **Motore solleciti** (M4, `irec/services/invii.py` + regole pure in
   `irec/domain/scheduler.py` e `calendario.py`) — per fattura attiva, step
   ancorati a T (T−2 … T+35 → T+45) secondo flusso del mandante e pacchetto
   (Entry: email/PEC; Value: +WhatsApp; Premium: +voice). Regole: nessun
   invio nei festivi (nazionali italiani + Pasquetta) né dopo le 18:00 ora
   italiana (finestra spostata al primo giorno utile), consolidamento per
   cliente/canale (un messaggio elenca tutte le fatture dovute), controllo
   just-in-time pre-invio, canali non nel pacchetto o senza recapito
   saltati e segnalati (il flusso prosegue), invio effettivo dietro la porta
   `CanaleInvio` (mock in M4, adapter reali in M8). Escalation a T+45: mail a
   Recupero Crediti + mandante, fattura → Insoluto, con preavviso a T+44.
6. **Eventi che alterano il flusso** — promessa di pagamento → Pausa con
   nuova data e ripresa automatica; contestazione → Pausa + escalation;
   modifica scadenza → ricalcolo step futuri; opt-out → canale disabilitato.
   (Modulo AI conversazionale del PRD: fase successiva.)
7. **Escalation T+45** — preavviso proattivo a T+44 (silenzio = consenso),
   poi mail a IREC Recupero Crediti + mail al mandante, fattura → **Insoluto**.
8. **Reporting** — brief giornaliero in-app (KPI: portafoglio affidato =
   recuperato + da recuperare + passato a recupero crediti), report mensile
   via email al mandante.

## 5. Le tre modalità dell'agente (addendum agentico)

- **Proattiva**: ciclo giornaliero + notifiche (escalation imminente T+44,
  consenso scaduto, risposta debitore, dati in ritardo SLA AdE).
- **Guidata**: onboarding conversazionale dei collegamenti — l'agente fornisce
  link e istruzioni micro-step, l'utente esegue sui portali esterni e
  conferma; la verifica delega AdE (consuma 1 firma Infocert) parte solo su
  conferma esplicita, con rate-limiting **nel tool**.
- **Reattiva**: tool invocati da Mind su richiesta utente.

### Livelli di autonomia (criterio: "cosa succede se l'agente ha capito male?")

| Livello | Azioni | Esempi |
|---|---|---|
| 1 — Autonome | Sola lettura / reversibili a costo zero | portafoglio, posizioni, fatture, storico, prossimi invii, "perché non è partito X" |
| 2 — Con conferma | Impatto reale | pausa/riprendi, forza/annulla invio, registra pagamento manuale, modifica flusso, configura AI, invia report, aggiorna recapiti |
| 3 — Mai l'agente | Esterne o ad alto rischio | SPID/CIE, delega AdE, credenziali bancarie, contestazioni/minacce legali, sconti/condoni |

I permessi per pacchetto vivono **nel tool** (enforcement server-side), non nel
prompt; il limite di pacchetto risponde con upsell garbato, non errore freddo.

## 6. Modello dati (proprietà di IREC, Postgres dedicato)

Entità dal PRD §2 implementate: Mandante, Cliente finale, Posizione, Fattura,
Flusso/Step, Comunicazione, Pagamento, Audit log. Il *Report* del PRD non è
un'entità persistita: è generato al momento dai dati esistenti (M6).
Stati fattura: **Gestione, Pausa, Saldata, Insoluto** (+ etichetta visuale
"Scadenza" pre-scadenza, punto aperto A del PRD).

**Come è garantito l'isolamento (livelli 1-3 decisi in M1, il 4 in M2).**
Quattro livelli indipendenti, così la garanzia non dipende dalla disciplina
di chi scrive il codice:

1. **`TenantRepository`** (`irec/adapters/db/repository.py`): unico accesso ai
   dati, filtra ogni lettura, scrittura e cancellazione per il `tenant_id` del
   call-token.
2. **Guard `before_flush`**: rifiuta qualunque riga in uscita verso un tenant
   diverso, anche se il `tenant_id` è stato alterato dopo l'inserimento o su
   un'entità già caricata.
3. **Foreign key composite `(tenant_id, id)`**: il database rende impossibile
   un arco fra tenant diversi, quindi né una navigazione di relazione né una
   cancellazione a cascata possono attraversare il confine.

4. **RLS Postgres** (`irec/adapters/db/rls.py`, da M2): policy per tabella su
   `current_setting('irec.tenant_id')`, impostata a inizio transazione.
   Fail-closed (variabile assente → nessuna riga) e attiva anche per il
   proprietario delle tabelle (`FORCE`). Copre le query scritte fuori dal
   repository. In produzione il servizio si connette con un ruolo non
   privilegiato: i superuser bypassano la RLS.

In più, una regola di lint (ruff TID251) vieta gli import di SQLAlchemy fuori
da `irec/adapters/db/`: il layering è verificato dalla CI, non affidato alla
disciplina. `user_id` per tabella non è stato introdotto: in MVP ogni mandante
ha un solo account operativo (PRD Gestione utenze §5).

- Audit trail: ogni comunicazione (data/ora, canale, operatore, esito recapito),
  ogni transizione di stato, ogni azione manuale. Storico immutabile.
- Idempotenza pagamenti: `chiave_idempotenza` univoca per tenant, così il
  pagamento registrato a mano e quello rilevato dalla riconciliazione non
  vengono contati due volte.
- Importi `Numeric(14,2)` e quantizzazione al centesimo nel dominio: mai float.
- GDPR: `DELETE /v1/tenant` (richiede scope dedicato), FK con cascade.

### Convenzione di naming

Il **dominio finanziario è in italiano** (`fattura`, `sollecito`, `residuo`,
`mandante`, `posizione`: i termini del PRD, che non hanno traduzione senza
perdita); l'**infrastruttura è in inglese**, helper privati inclusi
(`select`, `add`, `flush`, `delete_tenant_data`, `log_event`, `_find_key`,
`_set_rls_tenant`, `_not_ready_reason`). Nei payload API: campi di dominio
in italiano, campi di protocollo/envelope in inglese (`run_id`, `status`,
`error`).

## 6-bis. Layer applicativo

`irec/services/` orchestra dominio e adapter (chi chiamare, in che ordine,
cosa persistere); le REGOLE stanno in `domain/`, l'IO negli `adapters/`.
Direzione delle dipendenze: `api → services → (domain, adapters)`, mai il
contrario. Il ciclo giornaliero (`services/sync.py`) dipende dalle porte,
non dai mock: la scelta mock/reali avviene in `adapters/providers.py` allo
startup, con fail-fast se un ambiente production seleziona i mock.

## 7. Cosa NON fare (lista nera dal brief)

- ❌ Connettersi al DB di Mind (Supabase).
- ❌ Autorizzare in base ai gruppi del realm Keycloak.
- ❌ Detenere segreti capaci di forgiare token (solo verifica JWKS).
- ❌ Accoppiarsi allo schema interno di Mind — solo `/v1` (OpenAPI).
- ❌ Run sincrone lunghe (usare async + `run_id`).

## 8. Punti aperti

1. **Contratti dei 3 microservizi esterni**: spec esatte (auth, endpoint,
   formati, sync/async, paginazione); in particolare I/O del MS Riconciliazione.
2. **Canali di invio** (email alias, WhatsApp/ManyChat, PEC, voice agent):
   servizi esterni o integrati in IREC?
3. **Deploy**: quale host separato (Fly.io / Render / Cloud Run / VM); Docker già pronto.
   *(Stack: deciso — Python 3.12 + FastAPI + SQLAlchemy/Alembic.)*
4. Ereditati dal PRD (Appendice A): scaricabilità report (A1), cadenza
   notifiche brief/mail/report (A2), scadenze multiple sulla stessa fattura (A3).
5. Retry verifica delega AdE: chi lo fa partire dopo il timer (agente
   automatico vs utente) — da definire con il team tecnico.
