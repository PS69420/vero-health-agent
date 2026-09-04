"""
Mock outbound-voice connector (stands in for Vapi AI + telephony).

Simulates a compliance check-in call entirely offline: no network calls, no
LLM calls, no telephony -- just deterministic template logic seeded from the
patient's own chart data, so a re-run produces the same transcript for the
same inputs (useful for testing). The RETURN SHAPE (call_id, transcript,
outcome_tag, escalate_to_rt, duration_sec) mirrors what a real Vapi webhook
payload + your own transcript summarizer would hand back, so wiring in a real
`VapiCallTool` later is a drop-in swap behind the same place_call() contract.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from agent.knowledge_base import BARRIER_KEYWORDS, BARRIER_PATIENT_LINES, BARRIER_TIPS
from agent.models import ComplianceSnapshot, Patient
from agent.tools.base import VoiceCallTool
from agent import config


def _seed(patient_id: str, salt: str) -> int:
    h = hashlib.sha256(f"{patient_id}:{salt}".encode()).hexdigest()
    return int(h[:8], 16)


def _first_name(full_name: str) -> str:
    if "," in full_name:
        parts = full_name.split(",")
        return parts[1].strip().split()[0]
    return full_name.split()[0]


def _infer_barrier(patient: Patient) -> str:
    flags_and_notes = " ".join(patient.clinical_flags) + " " + patient.office_note_summary.lower()
    for barrier, keywords in BARRIER_KEYWORDS.items():
        for kw in keywords:
            if kw in flags_and_notes.lower():
                return barrier
    return "forgetting"  # sensible default: most missed-usage nights are just not putting it on


class MockVoiceCallTool(VoiceCallTool):
    def __init__(self):
        self._counter = 0

    def place_call(self, patient: Patient, call_purpose: str, context: dict[str, Any]) -> dict:
        self._counter += 1
        seed = _seed(patient.patient_id, f"{call_purpose}:{self._counter}")
        now = datetime.now()
        call_id = f"MOCKCALL-{now.strftime('%Y%m%d%H%M%S')}-{self._counter:03d}"

        # ~1 in 6 calls simulate a missed connection, matching real-world call
        # center answer rates -- shows the agent's retry/escalation path.
        if seed % 6 == 0:
            return {
                "call_id": call_id,
                "patient_id": patient.patient_id,
                "timestamp": now.isoformat(),
                "call_purpose": call_purpose,
                "duration_sec": 0,
                "transcript": [],
                "outcome_tag": "no_answer_voicemail_left",
                "escalate_to_rt": False,
                "simulated": True,
            }

        snapshot: ComplianceSnapshot | None = context.get("snapshot")
        prior_calls: list[dict] = context.get("prior_calls", [])
        first = _first_name(patient.name)
        barrier = _infer_barrier(patient)
        tip = BARRIER_TIPS[barrier]

        transcript = []

        def say(speaker: str, text: str):
            transcript.append({"speaker": speaker, "text": text})

        say("agent", f"Hi, is this {first}? This is the care team calling from "
                      f"{config.ORG_NAME} about your CPAP therapy.")
        say("patient", f"Yes, this is {first}.")

        if prior_calls:
            say("agent", "Good to talk with you again -- just following up on our last call "
                          "about your usage.")

        if snapshot:
            say("agent",
                f"Our records show you're averaging about {snapshot.avg_usage_hours} hours a "
                f"night, using it on {snapshot.pct_nights_ge_4hr:.0f}% of nights for at least "
                f"4 hours. Insurance requires 70% of nights at 4+ hours in a 30-day stretch "
                f"within your first 90 days on the machine, so I wanted to check in before "
                f"that becomes a problem. How's it been going?")
        else:
            say("agent", "I wanted to check in on how the CPAP has been going.")

        say("patient", BARRIER_PATIENT_LINES[barrier])
        say("agent", tip)

        chronic_case = len(prior_calls) >= 1 and (
            not snapshot or snapshot.pct_nights_ge_4hr < config.COMPLIANCE.required_pct_nights
        )
        device_fault = bool(snapshot and snapshot.device_fault_reported)
        escalate = chronic_case or device_fault

        if escalate:
            say("patient", "I've tried a few things already and it's still not working well.")
            say("agent", "That's helpful to know -- I'm going to have one of our Respiratory "
                          "Therapists reach out directly to look at your settings and mask fit, "
                          "rather than just checking in again by phone.")
            outcome = "requests_rt_callback"
        else:
            say("patient", "Okay, I can try that.")
            say("agent", "Great -- I'll check back in with you in a couple weeks. Thanks for "
                          "your time, and call us anytime in between if anything comes up.")
            outcome = "acknowledged_will_improve"

        duration_sec = 90 + (seed % 180)

        return {
            "call_id": call_id,
            "patient_id": patient.patient_id,
            "timestamp": now.isoformat(),
            "call_purpose": call_purpose,
            "duration_sec": duration_sec,
            "transcript": transcript,
            "barrier_identified": barrier,
            "outcome_tag": outcome,
            "escalate_to_rt": bool(escalate),
            "simulated": True,
        }
