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

**This build makes zero network calls.** Every "EMR", "AirView / DME Link",
"email", and "phone call" is a local mock reading/writing files in `data/` and
`output/`. That's intentional — you asked for a self-contained test system
using your sample patient notes, compliance reports, and sleep studies before
connecting to real systems (EMR, AirView, Vapi, etc.).

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
  knowledge_base.py     <- static policy/clinical reference text (stands in for
                          a RAG layer later, if you want the agent answering
                          open-ended questions instead of citing fixed rules)
  memory.py              <- long-term memory (JSON file today; swap for a real
                          DB later) + short-term per-run reasoning trace
  tools/
    base.py                <- ABSTRACT interfaces: EmrTool, ComplianceDataTool,
                             EmailTool, VoiceCallTool
    emr_tool.py              <- Mock*, reads data/patients.json
    compliance_data_tool.py   <- Mock*, reads data/compliance.json
    email_tool.py              <- Mock*, writes output/emails/*.txt
    voice_tool.py                <- Mock*, generates a deterministic simulated
                                   call transcript (no LLM/API call involved)
  agents/
    dme_needs_agent.py          <- DME Needs Agent
    compliance_agent.py          <- Compliance Agent
  orchestrator.py                 <- wires the above together, runs the roster
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

## Next steps once you're happy with the logic

- Swap `MockEmrTool` for your real EMR connector.
- Swap `MockComplianceDataTool` for the real AirView / DME Link connector(s) —
  you'll likely need one per vendor (ResMed, ReactHealth, etc.), each
  normalizing into `ComplianceSnapshot`.
- Swap `MockVoiceCallTool` for a real `VapiCallTool` and set up the Vapi
  webhook to write the finished call transcript back into `JsonEpisodicMemory`
  (or a real DB) in the same shape used here.
- Swap `MockEmailTool` for SMTP/Graph so doctor emails actually send, and
  point `doctor_email` at real addresses (currently a placeholder).
- Decide on a run cadence (e.g. a nightly cron / scheduled job calling
  `main.py run-all`) once this is wired to live data.
