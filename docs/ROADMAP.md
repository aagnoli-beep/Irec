# IREC — Roadmap di sviluppo

Principio: i contratti dei 3 microservizi esterni (cassetto fiscale, banche,
riconciliazione) e dei canali di invio non sono ancora definiti. Il dominio si
sviluppa quindi contro **interfacce + adapter mock**; gli adapter reali si
innestano in M8 senza toccare la logica. Ogni milestone produce qualcosa di
testabile e lascia il servizio deployabile.

---

## M0 — Fondazioni del servizio

Scaffold del progetto nello stack scelto, Docker, CI (lint + test), config via
env, logging JSON strutturato senza PII con `x-correlation-id`, formato errori
`{error, code}`, endpoint `/health` e `/ready`.
**Auth dal giorno 1**: middleware di verifica call-token via JWKS (chiavi di
test), controllo `aud`/`exp`/`entitlement`, estrazione `tenant_id`.

✅ *Fatta quando:* il container parte, `/health` risponde, una richiesta con
token di test valido passa e una senza token viene rifiutata; CI verde.

## M1 — Modello dati e persistenza

Schema Postgres con migrazioni: mandante, cliente finale, posizione, fattura
(stati: Gestione/Pausa/Saldata/Insoluto), flusso/step, comunicazione,
pagamento, audit trail (transizioni di stato + azioni manuali, immutabile).
Ogni tabella con `tenant_id`; scoping automatico per tenant su ogni query
(valutare RLS Postgres). Endpoint GDPR di cancellazione tenant (FK cascade).

✅ *Fatta quando:* migrazioni ripetibili, test che dimostrano l'isolamento tra
tenant e la storicizzazione delle transizioni di stato.

## M2 — Adapter dei microservizi esterni (mock-first)

Definizione delle **porte** (interfacce) verso i 3 MS: `FattureProvider`
(fatture XML AdE + stato collegamento), `MovimentiProvider` (entrate/uscite +
stato consenso PSD2), `Riconciliatore` (fatture+movimenti → pagate/da pagare).
Implementazioni mock con dataset realistici e scenari (collegamento caduto,
pagamento parziale, latenza SDI 2-3 gg). Modello di stato dei collegamenti per
tenant (delega AdE, consenso PSD2 con scadenza).

✅ *Fatta quando:* i mock coprono gli scenari chiave e il dominio dipende solo
dalle interfacce, mai da un client concreto.

## M3 — Ciclo giornaliero di sincronizzazione

Ciclo per tenant: verifica collegamenti → recupero fatture e
movimenti → invio al riconciliatore → applicazione esiti (Saldata + annullo
solleciti residui; parziale con residuo aggiornato; nuove fatture → posizione
+ schedule; posizione chiusa). Idempotenza pagamenti (manuale vs
riconciliazione automatica, no doppio conteggio). Run asincrone con `run_id`.

✅ *Fatta quando:* test end-to-end sul ciclo con i mock: da "nuova fattura nel
cassetto" a "fattura a sistema con schedule", e da "movimento in banca" a
"fattura saldata con solleciti annullati".

*Nota (esito M3):* il ciclo parte on-demand via `POST /v1/reconciliations`
(tool di Mind o operatore). La schedulazione automatica quotidiana — chi
invoca il ciclo ogni giorno per ogni tenant — sarà definita con
l'infrastruttura di deploy (M8): cron esterno, scheduler della piattaforma o
job interno.

## M4 — Motore solleciti

Il cuore del prodotto. Calcolo dello schedule per fattura (offset su T: T−2 …
T+35), flusso default + flussi personalizzati per mandante, canali per
pacchetto (Entry: email/PEC; Value: +WA; Premium: +voice) con fallback su
canale non abilitato o recapito mancante. Regole calendario: festivi, finestra
≤18:00. Consolidamento per cliente/giorno/canale. Controllo just-in-time
pre-invio e anti-doppio invio. Eventi: pausa/ripresa, promessa di pagamento
(pausa con nuova data e ripresa automatica), ricalcolo su modifica scadenza,
opt-out canale. Escalation: preavviso T+44, a T+45 mail a Recupero Crediti +
mandante e fattura → Insoluto. Invio effettivo dietro interfaccia
`CanaleInvio` (mock finché la titolarità dei canali non è definita).

✅ *Fatta quando:* suite di test sul motore puro (calendario, consolidamento,
just-in-time, ricalcoli, T+45) — è la milestone con più test di tutte.

## M5 — API `/v1` per Mind (modalità reattiva)

Implementazione del contratto `openapi.yaml` ed estensione con gli endpoint a
supporto dei tool dell'addendum (§6): letture autonome (portafoglio/KPI,
posizione, fatture, storico solleciti, prossimi invii, "perché non è partito
X", stato onboarding) e azioni (pausa/riprendi, forza/annulla invio, registra
pagamento manuale, modifica flusso, aggiorna recapiti, genera/invia report).
`Idempotency-Key` sulle mutazioni. **Permessi per pacchetto enforced
server-side nel tool** con risposta di upsell garbato (non errore freddo).
`GET /v1/usage` per il billing.

✅ *Fatta quando:* il servizio implementa fedelmente `openapi.yaml` (verifica
automatica contratto↔implementazione in CI) e i permessi per pacchetto sono
coperti da test.

## M6 — Proattività e reporting

Brief giornaliero (KPI: portafoglio affidato = recuperato + da recuperare +
passato a recupero; tono calmo, max 2-3 azioni proposte; nota "aggiornato
all'ultima sincronizzazione delle ore X"). Notifiche: escalation imminente
(T+44), consenso PSD2 scaduto, risposta debitore da gestire, dati in ritardo
SLA AdE. Report mensile al mandante. Meccanismo di consegna notifiche verso
Mind da concordare (webhook/endpoint di polling).

✅ *Fatta quando:* il brief si genera correttamente da dati mock e le
notifiche scattano nei test di scenario.

## M7 — Onboarding guidato

Tool conversazionali: avvia collegamento cassetto (link + vincolo provider
unico), verifica delega AdE con **conferma esplicita, rate-limiting nel tool e
memoria dell'ultima richiesta** (ogni verifica costa 1 firma Infocert), avvia
collegamento Fabrick (consenso PSD2 90 gg), stato onboarding, configurazione
flusso iniziale (Value/Premium).

✅ *Fatta quando:* la sequenza di onboarding è percorribile end-to-end con i
mock e il rate-limiting impedisce verifiche ripetute.

## M8 — Integrazione reale e hardening

Sostituzione dei mock con gli adapter reali (contratti dei 3 MS + canali di
invio), integrazione end-to-end con Mind (call-token reale, prove di
resilienza: timeout, circuit breaker lato Mind, IREC spento → Mind vivo),
deploy Docker su host scelto (staging → produzione), security review, verifica
GDPR, osservabilità.

✅ *Fatta quando:* un utente reale su Mind interroga IREC in chat e il ciclo
giornaliero gira su dati reali in staging.

---

## Quality gate (a ogni milestone)

A fine di ogni milestone si lanciano i reviewer di `.claude/agents/` (security,
architecture, code-quality, test, docs → tech-lead-synthesizer) sul lavoro
svolto. Il report e il sub-piano di remediation R<n> vanno in `docs/reviews/`;
gli item bloccanti si risolvono subito, gli altri confluiscono nella milestone
successiva.

| Milestone | Stato | Review |
|---|---|---|
| M0 — Fondazioni | ✅ | [2026-08-05-M0.md](reviews/2026-08-05-M0.md) — R0 e R1 eseguiti |
| M1 — Modello dati | ✅ | [2026-08-05-M1.md](reviews/2026-08-05-M1.md) — R2 eseguito |
| M2 — Adapter mock | ✅ | [2026-08-05-M2.md](reviews/2026-08-05-M2.md) — R4 eseguito |
| M3 — Ciclo giornaliero | ✅ | [2026-08-05-M3.md](reviews/2026-08-05-M3.md) — R6 eseguito |
| M4 — Motore solleciti | ✅ | [2026-08-05-M4.md](reviews/2026-08-05-M4.md) — R7 eseguito |
| M5 — API `/v1` per Mind | ✅ | [2026-08-05-M5.md](reviews/2026-08-05-M5.md) |
| M6 — Proattività e reporting | ▶️ prossima | — |

## Fase 2 (fuori perimetro MVP)

- UI embedded in Mind (tabelle/dashboard via route/iframe).
- Modulo AI conversazionale per le risposte dei debitori (L1/L2/L3).
- Multi-account per mandante; dashboard KPI interattiva.

## Dipendenze esterne (sbloccano M8, non bloccano M0–M7)

| Dipendenza | Da chi | Serve per |
|---|---|---|
| Spec API dei 3 microservizi | Team microservizi | Adapter reali (M8) |
| Titolarità canali invio (email/WA/PEC/voice) | Team | Adapter `CanaleInvio` reali (M8) |
| Chiave firma call-token / JWKS + adapter tool lato Mind | Team Mind | Integrazione reale (M8) |
| Concordare `openapi.yaml` | Team Mind | Congelare il contratto (entro M5) |
| Meccanismo notifiche in-app di Mind | Team Mind | Consegna proattiva (M6/M8) |
