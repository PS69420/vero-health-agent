# Vero Health Agent — Presentation Outline (8–10 min)

*Plan this into your official outline template (Self-Study Capstone Checkpoint 7.1) — same caveat as the report, I don't have that template's exact fields. Timings assume ~130 words/minute spoken pace; adjust to your own delivery.*

Suggested visuals in **[brackets]** — pull these straight from the repo/dashboard rather than rebuilding them: the architecture diagram and roster are already in the published dashboard, the code snippets are in `agent/agents/*.py`.

---

### 1. Title & framing — ~30s
- Vero Health Agent: an autonomous DME/compliance-review agent for a sleep center.
- One line on who you are / the course context.
- **[Title slide]**

### 2. The problem, and why it matters — ~75s
- Two manual reviews every DME office runs constantly: (a) does this patient's sleep study support a new/updated equipment order, (b) is this patient on track to keep Medicare-covered PAP therapy (4hrs/night, 70% of nights, 30-day window, first 90 days).
- Miss either one and the consequence is concrete: a patient who needs equipment doesn't get it, or a patient loses coverage they still need.
- Currently done by hand, one patient/spreadsheet row at a time.
- **[Slide: the two review types, one sentence each, maybe a screenshot of the raw sample data]**

### 3. Goal and scope — ~45s
- Build an agent that does both reviews itself and takes the first correct action — draft the physician email, or place/queue the outreach call.
- Staged scope, deliberately: validate all decision logic offline against real sample clinical data first; add exactly one live integration, gated behind a human, only once the logic was proven.
- **[Slide: the staged-scope statement — this signals engineering judgment, not just "what I built"]**

### 4. Architecture walkthrough — ~2 min (the core of the talk)
- Two agents, one shared patient record: DME Needs Agent, Compliance Agent.
- Both reason ReAct-style — Thought → Action → Observation — call this out explicitly, it's your reasoning-and-memory story from earlier modules.
- Every external system sits behind an abstract interface (EMR, compliance data, email, voice, human-task-queue) — walk through why: it's what let you swap a mock voice tool for a real Vapi connector without touching agent logic.
- Two memory layers: episodic (what happened/when → cooldowns) and semantic (what was *said* → a real local vector store, feeds the next call and doctor emails).
- **[Slide: the architecture diagram from the dashboard, live if you can screen-share it — walk the arrows: sources → agents → tools → memory → feedback loop]**

### 5. Key design decisions across the program — ~90s
Pick 3–4 decisions that show evolution, not just a feature list:
- Escalation-over-automation for clinically ambiguous cases (central apnea, cardiac history, vulnerable patients) — a safety/guardrail decision.
- The AI-contact consent gate, enforced server-side, not just a UI toggle — human oversight decision, and *why* UI-only enforcement isn't real enforcement.
- Keeping the one real integration (Vapi) manual-only and off the automated path — a deliberate risk decision, not a limitation you're apologizing for.
- Two bugs you found via evaluation, not by accident (see next section) — shows the evaluation process actually did its job.
- **[Slide: 3–4 bullets, one line each — this is where you demonstrate judgment, so don't rush it]**

### 6. Evaluation approach and results — ~90s
- 23 unit tests across both agents' branching logic, cooldown math, consent routing, and semantic recall.
- A 12-date historical backtest that replays the real code across real dates with memory accumulating like production would.
- The backtest is what surfaced two real bugs: a wall-clock-vs-simulated-date timestamp bug, and a transcript quote-extraction bug — name both briefly, and that both are now regression-tested.
- Live, read-only verification against the real Vapi account (no call placed) as the final proof of integration correctness.
- **[Slide: test count + the 2 bugs found, framed as "evaluation caught these before they mattered"]**

### 7. GitHub repository — ~30–45s
- What's in it: full source, test suite, synthetic sample data, dashboard generator, setup instructions for the offline pipeline and the local Vapi console, this report.
- Say the URL out loud and show it on screen.
- **[Slide: repo URL + a directory listing screenshot]**

### 8. Strengths, limitations, next steps — ~75s
- Strengths: real evaluation (not just claims), safety gates that are actually enforced, clean interface boundary for going live.
- Limitations: EMR/AirView still mocked, no automated real calling yet, phone numbers not wired from a real source, lexical rather than neural semantic search.
- Next steps: real per-vendor connectors, real patient phone numbers, then — only once trusted — automated (not manual-only) real outreach.
- **[Slide: two columns, limitations vs. next steps, paired 1:1]**

### 9. Close — ~15s
- One sentence restating what the system does and the core design principle (safety/oversight before automation).

---

**Total: ~9 minutes.** If you're running long, trim section 4 (architecture) to the interface-boundary point and the memory layers only — cut the box-by-box diagram walk. If running short, expand section 6 with one concrete example (show an actual generated email or call transcript from the repo).
