"""
Mock human-callback queue.

Stands in for wherever your team actually tracks "a staff member needs to
call this patient" -- a shared spreadsheet, a work-queue tool, a task in the
EMR, whatever it ends up being. No network calls; just logs the task via
memory.record_human_task() so it shows up alongside AI-placed calls in the
patient's history.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from agent.models import Patient
from agent.tools.base import HumanTaskQueueTool

_REASON_LABEL = {
    "ai_contact_consent_declined": "Patient has not consented to AI-based contact -- routed to a human caller instead of placing an automated Vapi call.",
}


class MockHumanTaskQueueTool(HumanTaskQueueTool):
    def __init__(self):
        self._counter = 0

    def queue_call(self, patient: Patient, call_purpose: str, context: dict[str, Any],
                    reason: str = "ai_contact_consent_declined") -> dict:
        self._counter += 1
        as_of: Optional[date] = context.get("as_of")
        created_at = datetime.combine(as_of, datetime.now().time()) if as_of else datetime.now()
        task_id = f"MOCKTASK-{created_at.strftime('%Y%m%d%H%M%S')}-{self._counter:03d}"
        return {
            "task_id": task_id,
            "patient_id": patient.patient_id,
            "created_at": created_at.isoformat(),
            "call_purpose": call_purpose,
            "reason": _REASON_LABEL.get(reason, reason),
            "status": "pending_human_callback",
            "simulated": True,
        }
