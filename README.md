# Vero Health Agent (Test / Offline Mode)

An AI-agent prototype for a sleep center + DME company that:

1. **DME Needs Agent** — reviews each patient's sleep study / titration history
   and current PAP status, and drafts a request email to the ordering physician
   when a patient needs an initial PAP order or an equipment/settings upgrade
   (distinguishing that from routine mask/supply resupply, which doesn't need a
   doctor's order).
2. **Compliance Agent** — reviews AirView/DME-Link-style usage data against the
   Medicare rule (≥4 hrs/night on ≥70% of nights in a 30-day window, within the
   first 90 days of therapy), and for at-risk patients places a simulated
   Vapi-style outreach call — once ~2 weeks before the 30-day mark, and again
   before the 90-day insurance cutoff if still non-compliant — storing every
   transcript so it never nags the same patient twice in one week.

**The automated path (`main.py run-all`, and the compliance agent's scheduled
outreach) still makes zero network calls.** Every "EMR", "AirView / DME Link",
and "email" is a local mock reading/writing files in `data/` and `output/`,
and automated compliance calls still go through the safe `MockVoiceCallTool`
(a simulated transcript, not a real phone call). That's intentional — nothing
gets auto-dialed without a human in the loop yet.

**The one real integration so far is manual, human-triggered Vapi calls** via
the local test console (`console/server.py`) — see [Local Vapi test
console](#local-vapi-test-console) below. That's the only thing in this repo
that makes a real network call or rings a real phone.

## Quick start

No dependencies to install — standard library only.

```bash
python3 main.py list-patients                     # see the 22 test patients
python3 main.py run-all --as-of 2026-09-04         # run both agents, write report
python3 main.py dme-check --as-of 2026-09-04       # DME agent only, JSON to stdout
python3 main.py compliance-check --as-of 2026-09-04 # compliance agent only
python3 -m unittest discover -s tests -v           # run the test suite
```

`--as-of` lets you simulate "today" so you can watch the compliance agent's
behavior change as a patient moves through their 90-day window — try a few
dates in a row (e.g. day 10, day 15, day 75) and watch when it starts calling.
Omit it to use the real current date.

After `run-all`, check `output/`:
- `summary_report.md` — human-readable run summary (start here)
- `dme_recommendations.json`, `compliance_decisions.json` — full structured
  results, including the Thought/Action/Observation reasoning trace per patient
- `emails/*.txt` — every simulated doctor email that was "sent"
- `memory/episodic_memory.json` — persistent record of every call transcript,
  email, and decision, per patient (this is what prevents duplicate outreach
  on repeated runs)

## Test data

`data/patients.json` and `data/compliance.json` hold 21 patients transcribed
from the sample PSG/HST/CPAP-titration reports, AirView/ReactHealth compliance
reports, and office notes you provided, normalized into one consistent shape —
plus one clearly-labeled synthetic patient (`p022`) added only to exercise the
"treatment-naive patient needs an initial PAP order" code path, since none of
the real sample patients were PAP-naive.

The dataset intentionally includes messy real-world cases the agent has to
handle correctly, not just easy ones:
- **Central/mixed sleep apnea, treatment-emergent central apnea, cardiac
  arrhythmia history** → the DME agent must *not* auto-draft a routine order;
  it escalates to a human instead.
- **A non-verbal patient (Down syndrome) whose caregiver reports the symptoms**
  → both agents route this to a human rather than auto-calling the patient.
- **Chart data conflicts** (e.g. a titration study says one pressure, the
  device compliance report shows another) → flagged for a clinician/tech to
  resolve, not silently acted on.
- **A patient with a stale compliance report and years of therapy history**
  → the compliance agent correctly treats this as long-term maintenance
  adherence, not an imminent 90-day insurance cutoff.

## Architecture — designed to swap in real integrations later

```
agent/
  config.py           <- all clinical/business thresholds (AHI cutoffs, the
                          Medicare 70%/30-day rule, outreach timing) in one
                          place, so ops can tune policy without touching logic
  models.py            <- shared data shapes (Patient, ComplianceSnapshot, ...)
  knowledge_base.py     <- static, hand-authored policy/clinical reference text
                          (fixed snippets that don't vary per patient)
  memory.py              <- episodic long-term memory (JSON file today; swap
                          for a real DB later): what happened, and when
  semantic_memory.py       <- SEARCHABLE long-term memory: what a patient has
                          actually said on past calls. Real local vector
                          search (TF-IDF + cosine similarity, stdlib only)
                          today; swap for an embedding-API-backed store later
                          behind the same VectorStore interface
  tools/
    base.py                <- ABSTRACT interfaces: EmrTool, ComplianceDataTool,
                             EmailTool, VoiceCallTool, HumanTaskQueueTool
    emr_tool.py              <- Mock*, reads data/patients.json
    compliance_data_tool.py   <- Mock*, reads data/compliance.json
    email_tool.py              <- Mock*, writes output/emails/*.txt
    voice_tool.py                <- Mock*, generates a deterministic simulated
                                   call transcript (no LLM/API call involved)
    human_queue_tool.py           <- Mock*, logs a pending callback task for
                                   patients who haven't consented to AI contact
    vapi_call_tool.py               <- REAL Vapi connector -- the one thing in
                                   this repo that makes a live network call.
                                   Only used by console/server.py; see
                                   "Local Vapi test console" below
  agents/
    dme_needs_agent.py          <- DME Needs Agent
    compliance_agent.py          <- Compliance Agent
  orchestrator.py                 <- wires the above together, runs the roster
console/
  server.py                        <- local-only web server for real,
                                   human-triggered Vapi calls (see below)
  static/index.html                  <- its frontend
```

**The only thing that changes when you're ready to go live is `agent/tools/`.**
Every agent talks *only* to the abstract interfaces in `tools/base.py`. To
connect a real system:

1. Write a new class implementing the same interface, e.g.
   `AirViewApiTool(ComplianceDataTool)` that calls the real ResMed AirView API
   and returns a `ComplianceSnapshot` (or `EmrApiTool(EmrTool)` for your real
   EMR, `VapiCallTool(VoiceCallTool)` that calls the real Vapi API and normalizes
   the webhook payload into the same call-record shape, `SmtpEmailTool(EmailTool)`
   or a Graph-API email tool).
2. Swap which class gets instantiated in `agent/orchestrator.py` /
   `build_default_toolset()`.
3. Nothing in `agent/agents/*.py` needs to change — that's the point of the
   interface boundary.

A natural place to introduce real credentials later is environment variables
(e.g. `AIRVIEW_API_KEY`, `VAPI_API_KEY`) read inside the new tool
implementations — keep them out of `config.py` so this repo never holds a
secret.

## Semantic memory — giving the agent recall of what patients actually said

`agent/memory.py` (episodic) answers "did we call this patient, and when."
`agent/semantic_memory.py` answers "what did they say" — a real, working
per-patient vector search over call transcripts, so the next call or doctor
email can reference the actual prior conversation instead of starting cold
each time. It's a genuine vector store (TF-IDF term vectors + cosine
similarity), computed with nothing but the standard library — no embedding
API, no cost, no extra dependency, which is the right size for a handful of
short transcripts per patient. Swap `LocalTfidfVectorStore` for a real
embedding-API-backed store (OpenAI/Voyage + Chroma/pgvector/Pinecone) behind
the same `VectorStore` interface once transcript volume or search quality
calls for it — same mock-now/real-later pattern as every tool in this repo.

Two places consume it, both real and tested (see `tests/test_semantic_memory.py`,
plus the recall-specific tests in each agent's test file):

- **Before placing another compliance call** (`ComplianceAgent`), it searches
  for what the patient said last time and passes it into the call as
  `context["recall"]` — so instead of "good to talk with you again," the
  transcript opens with e.g. *"Last time we spoke you mentioned the mask was
  leaking — has refitting it helped at all?"* (see `BARRIER_FOLLOWUP_LINES` in
  `data/knowledge_base.json`).
- **Before drafting an equipment-upgrade doctor email** (`DmeNeedsAgent`), it
  searches the same memory and cites relevant past complaints in the email
  body, with the patient's actual words where available — supporting the
  equipment-change request with more than just chart/device data.
- **Real Vapi calls get it too**: the console passes a `previousCallNotes`
  variable built from the same search into every manual call. It only does
  something once your Vapi assistant's prompt actually references
  `{{previousCallNotes}}` — Vapi silently ignores variables a prompt doesn't
  use, so this is a no-op until you add that placeholder.
- **Real Vapi transcripts get indexed too, once they exist**: placing a call
  returns before it's actually happened, so `console/server.py`'s
  `sync_manual_call_transcripts()` polls Vapi for any call that has since
  ended and indexes its transcript/summary the same way — checked every time
  the console's patient list refreshes.

## Notable design decisions worth knowing about

- **Escalation over automation for anything clinically ambiguous.** Central
  apnea, treatment-emergent central apnea, cardiac history, chart data
  conflicts, and non-verbal/vulnerable patients all route to a human instead
  of an automated email or call — see `_ESCALATION_FLAGS` in each agent file.
- **Current AHI control is judged from the most recent compliance-report AHI
  when available**, not the (possibly months-stale) titration-night AHI —
  otherwise a patient whose settings already got fixed and are working well
  would keep getting flagged forever.
- **Both agents dedup against memory** before acting: the DME agent won't
  re-email the doctor about the same order type within 14 days, and the
  compliance agent won't re-call a patient within 7 days. Tune both in
  `config.py`.
- **The compliance agent distinguishes "still inside the 90-day proving
  window" from "long-term patient with an ongoing adherence problem."** The
  former gets framed around the insurance deadline; the latter is treated as
  routine maintenance coaching, which is the clinically accurate framing.

## Local Vapi test console

`console/server.py` is a small local web server (standard library only, no
Flask) that connects to the **real** Vapi API — the one thing in this repo
that makes a real network call and rings a real phone. It exists separately
from the claude.ai dashboard artifact on purpose: a published artifact runs
in a browser sandbox that only allows loading fonts/scripts from a short CDN
allowlist, so it **cannot** call Vapi's API (or any other external API) no
matter how it's written, and it would be unsafe to ship a real Vapi API key
inside a page's client-side JavaScript anyway (anyone viewing the page could
read it out of the source). This console runs only on your machine instead,
so it has ordinary outbound network access and never exposes the key to a
browser tab you might share.

**Setup (one time):**
```bash
cp .env.example .env
# then fill in VAPI_API_KEY, VAPI_ASSISTANT_ID, VAPI_PHONE_NUMBER_ID,
# and VAPI_TEST_OVERRIDE_NUMBER in .env
```

**Run it:**
```bash
python3 console/server.py          # opens http://localhost:8787 in your browser
```

What it does:
- A **status light** at the top does a real, read-only check against the Vapi
  API (confirms your API key, assistant, and phone number are all valid and
  reachable) and re-checks automatically every 20 seconds — that's your proof
  the connection is actually live, not just configured. Green = ready to
  call, red = something's wrong (with the real error shown).
- The **patient roster** below it is computed by the same real agent logic as
  `main.py run-all`, through the same safe mock tools — loading this page
  never sends a real email or places a real call on its own.
- Each patient has a **"Call now" button**, enabled only while the light is
  green *and* the patient has consented to AI contact (`ai_contact_consent`
  in their record) — a patient without consent shows "No AI consent" and
  stays disabled regardless of the light, enforced server-side too (not just
  a disabled button) so a stale page or direct request can't bypass it.
  Pressing it places one real Vapi call, right then, to whatever number is
  set as `VAPI_TEST_OVERRIDE_NUMBER` in `.env` — **every** patient's button
  rings that same number while in test mode, regardless of whose name is on
  it, since the sample patient data doesn't carry real phone numbers. The
  call passes that patient's real compliance data (usage %, AHI, device,
  etc.) *and* a summary of what they said on past calls (see "Semantic
  memory" above) to the assistant as context, matching the `{{variables}}`
  in its configured system prompt.
- Every manually-placed call is logged to `output/memory/episodic_memory.json`
  under a separate `manual_calls` bucket, kept apart from the automated
  agent's (still-mocked) `calls` bucket so the two are never confused. Since
  a real call hasn't happened yet the instant it's placed, the console polls
  Vapi for the finished transcript on every roster refresh and indexes it
  into semantic memory once it's available.

This is a real, billed action against your Vapi account each time you press
the button — there's no simulate-only mode for this particular button by
design, since the whole point is proving the live connection works.

## Next steps once you're happy with the logic

- Swap `MockEmrTool` for your real EMR connector.
- Swap `MockComplianceDataTool` for the real AirView / DME Link connector(s) —
  you'll likely need one per vendor (ResMed, ReactHealth, etc.), each
  normalizing into `ComplianceSnapshot`.
- `agent/tools/vapi_call_tool.py` is a real Vapi connector, but today it's only
  wired into the manual console (see below), not the automated
  `ComplianceAgent` scheduling path. Once you're comfortable with real calls
  firing on a schedule instead of a button press, swap `MockVoiceCallTool` for
  it there too, remove the `VAPI_TEST_OVERRIDE_NUMBER` override, and wire real
  patient phone numbers into the EMR data.
- Swap `MockEmailTool` for SMTP/Graph so doctor emails actually send, and
  point `doctor_email` at real addresses (currently a placeholder).
- Decide on a run cadence (e.g. a nightly cron / scheduled job calling
  `main.py run-all`) once this is wired to live data.
