# Vero Health Agent — Final Capstone Report

*Note: this is drafted to the section content required by the assignment brief. Paste it into your official report template (Self-Study Capstone Checkpoint 7.1) — I don't have that template's exact file, only the section list from the instructions.*

## The Problem and the Intended User

Sleep centers that also operate as durable medical equipment (DME) suppliers carry two review burdens that are easy to describe and hard to keep up with by hand. First, every sleep study and titration report needs a clinician to decide whether a patient needs a new PAP device, an equipment change, or just a mask resupply, and then to request it from the ordering physician. Second, every patient already on a device has to be watched against Medicare's compliance rule — at least 4 hours of use on at least 70% of nights, in a 30-day window, within the first 90 days of therapy — or the insurer stops paying for equipment the patient may still need. Both reviews are currently done manually, checking spreadsheets and calling patients one at a time. The intended users are DME office staff: schedulers, respiratory therapists, and the office managers who route physician requests. Patients are an indirect stakeholder, since the system's outreach calls reach them directly.

## Goal and Scope

The goal is an agent system that reviews a patient roster the way a DME coordinator would — reading sleep-study data to catch equipment needs, and reading usage data to catch compliance risk before the insurance deadline — and takes the first correct action itself: drafting the physician request, or placing (or queuing) a check-in call. The scope was staged deliberately: build and validate the full decision logic offline against real sample clinical data first, then add one live, real-world integration behind a manual, human-gated trigger, rather than connecting every system at once.

## Final Architecture and Major Components

The system is two cooperating agents sharing a common patient record. The **DME Needs Agent** reasons over sleep-study and titration history to decide between requesting an initial PAP order, requesting an equipment/settings upgrade, logging a routine resupply, or escalating to a clinician. The **Compliance Agent** reasons over usage data against the Medicare rule and decides whether to log compliance, wait, place outreach, or escalate. Both use a ReAct-style Thought → Action → Observation trace, so every decision is auditable rather than a black box.

Both agents sit behind abstract tool interfaces for every external system: an EMR connector, an AirView/DME-Link compliance-data connector, an email connector, a voice-call connector, and a human-task-queue connector. Every one is backed by a mock implementation today (local JSON fixtures, local files) except the voice connector, which now has a second, real implementation calling the Vapi API. Two memory layers back the agents: an episodic memory (what happened, and when — driving cooldowns and dedup) and a semantic memory (what a patient actually said, indexed with a local vector store, so a repeat call or doctor email can reference prior conversation content instead of starting cold). A local test console — a small server outside any browser sandbox — is the only path to real outbound calls, gated by a live readiness check and a manual "Call now" button per patient.

## How the Design Evolved Across the Program

The project moved from problem definition to a fully wired agent in five real steps. It started as a single-pass rule engine reading static fixtures. Reasoning and memory came next: the Thought/Action/Observation trace, and episodic memory for outreach cooldowns. Retrieval and coordination followed: a compliance snapshot feeding both agents instead of just one, and the semantic-memory layer that lets past call content shape the next call or email. Guardrails and human oversight were added deliberately, not as an afterthought — an AI-contact consent flag that routes non-consenting patients to a human-call queue instead of an automated call, enforced on the server, not just as a disabled button in a UI. The final step connected one real external system, Vapi, but deliberately kept it out of the automated path: real calls are placed only through a separate local console, by an explicit human button press, never by the scheduler.

## Implementation Overview

The system is Python, standard library only for all decision logic, memory, and the local vector store — no paid API and no install required to run the offline path. Business rules (AHI thresholds, the Medicare rule, outreach timing) live in a JSON config file, not scattered through code. Twenty-two synthetic patients, transcribed from anonymized sample sleep studies, compliance reports, and office notes, form the test fixture set; one is a labeled synthetic case built to exercise the treatment-naive ordering path. A CLI (`main.py`) runs the full pipeline or either agent alone. A published dashboard visualizes the architecture and lets a reviewer scrub through a 12-date backtest of real agent output. A local console is the only real-world-effect surface: a live Vapi status light and per-patient manual call buttons.

## Evaluation Methods and Results

Evaluation combines unit tests, a historical backtest, and live verification. Twenty-three unit tests cover both agents' branching logic, the compliance-cooldown math, the consent-gate routing, and the semantic-memory retrieval end-to-end — using the real vector store and real mock tools wherever the behavior under test depended on their actual output, not fakes. A 12-date backtest replays the real orchestrator across historical dates with memory accumulating exactly as it would day to day, which surfaced two real defects before they mattered in production: a wall-clock timestamp bug that would have broken cooldown math under any multi-date replay, and a quote-extraction bug that surfaced a greeting line instead of a patient's actual complaint. Both were fixed and are now covered by regression tests. Read-only checks against the live Vapi account (fetching the configured assistant and phone number) confirm the real integration is reachable without ever placing a call.

## Safety, Reliability, and Human Oversight

Escalation takes priority over automation whenever a case is clinically ambiguous: central/mixed apnea, cardiac history, conflicting chart data, and non-verbal or vulnerable patients all route to a human reviewer rather than an automated email or call. The AI-contact consent gate is enforced twice — in the UI and, more importantly, on the server — so a stale page can't place a call to a patient who declined AI contact. Both agents dedup against memory before acting, across channels, so a patient reached by a human isn't also auto-called days later. The one real-world integration is manual-only, behind a live readiness check, and its credentials never leave the local machine — a published, shareable dashboard cannot reach any external API at all, by design of its hosting sandbox, so a real API key was never put at risk there.

## Current Limitations and Future Improvements

The EMR and AirView/DME-Link connectors are still mocks; going live means writing one real implementation per vendor behind the existing interfaces. Automated Vapi calling doesn't exist yet — every real call is a manual button press, which is correct for this stage but not a production cadence. Patient phone numbers aren't wired from a real source yet, so every test call routes to a fixed override number. The semantic memory uses lexical (TF-IDF) rather than neural embeddings, sufficient at the current per-patient transcript volume but worth revisiting for cross-patient pattern search at scale. No real patient data was used anywhere in this project, consistent with program data-privacy requirements.

## GitHub Repository

**[insert public repo URL]** — includes the full source, the test suite, sample synthetic patient data, the dashboard generator, setup instructions for both the offline pipeline and the local Vapi console, and this report.
