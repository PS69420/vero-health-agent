"""
Persistent (long-term/episodic) and in-process (short-term) memory.

Long-term memory is a plain JSON file on disk (output/memory/episodic_memory.json).
That's the whole point at this stage: no database server, nothing to configure,
easy to inspect by opening the file. When this goes to production, swap
JsonEpisodicMemory's load/save for a real DB (Postgres, etc.) behind the same
interface -- agents only call record_call(), record_decision(), history_for().
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


@dataclass
class ShortTermTrace:
    """Per-run reasoning trace (Thought/Action/Observation steps). Not persisted
    beyond the run's own output report -- this is scratch working memory."""
    steps: list[dict] = field(default_factory=list)

    def think(self, thought: str):
        self.steps.append({"step": "thought", "text": thought})

    def act(self, action: str, detail: Any = None):
        self.steps.append({"step": "action", "text": action, "detail": detail})

    def observe(self, observation: str):
        self.steps.append({"step": "observation", "text": observation})


class JsonEpisodicMemory:
    """
    Long-term memory of everything the agent has done for a patient:
    outreach calls (with transcripts), decisions made, emails sent.
    Persisted so a later run knows "we already called this patient 3 days
    ago" and doesn't nag them again.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, "r") as f:
                return json.load(f)
        return {"patients": {}}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2, default=_json_default)

    def _bucket(self, patient_id: str) -> dict:
        bucket = self._data["patients"].setdefault(
            patient_id, {"calls": [], "decisions": [], "emails": [], "human_tasks": [], "manual_calls": []}
        )
        # older memory files predate these buckets
        bucket.setdefault("human_tasks", [])
        bucket.setdefault("manual_calls", [])
        return bucket

    def record_call(self, patient_id: str, call_record: dict):
        self._bucket(patient_id)["calls"].append(call_record)
        self._save()

    def record_manual_call(self, patient_id: str, call_record: dict):
        """A real, human-triggered Vapi call (console 'Call now' button) --
        kept separate from record_call()'s automated/mock bucket so the two
        are never confused in reports or dashboards."""
        self._bucket(patient_id)["manual_calls"].append(call_record)
        self._save()

    def manual_calls_for(self, patient_id: str) -> list[dict]:
        return self._bucket(patient_id).get("manual_calls", [])

    def record_decision(self, patient_id: str, decision_record: dict):
        self._bucket(patient_id)["decisions"].append(decision_record)
        self._save()

    def record_email(self, patient_id: str, email_record: dict):
        self._bucket(patient_id)["emails"].append(email_record)
        self._save()

    def record_human_task(self, patient_id: str, task_record: dict):
        self._bucket(patient_id)["human_tasks"].append(task_record)
        self._save()

    def calls_for(self, patient_id: str) -> list[dict]:
        return self._bucket(patient_id).get("calls", [])

    def last_call_for(self, patient_id: str) -> dict | None:
        calls = self.calls_for(patient_id)
        return calls[-1] if calls else None

    def human_tasks_for(self, patient_id: str) -> list[dict]:
        return self._bucket(patient_id).get("human_tasks", [])

    def all_outreach_for(self, patient_id: str) -> list[dict]:
        """AI calls and human-queued tasks together, in chronological order --
        for logic that cares about "has this patient been contacted at all"
        regardless of channel (e.g. the once-only early-window outreach, or
        the outreach cooldown)."""
        combined = self.calls_for(patient_id) + self.human_tasks_for(patient_id)
        return sorted(combined, key=lambda r: r.get("timestamp") or r.get("created_at") or "")

    def emails_for(self, patient_id: str) -> list[dict]:
        return self._bucket(patient_id).get("emails", [])

    def days_since_last_email_of_type(self, patient_id: str, email_type: str, as_of: date) -> int | None:
        matching = [e for e in self.emails_for(patient_id) if e.get("email_type") == email_type]
        if not matching:
            return None
        last = matching[-1]
        last_date = date.fromisoformat(last["sent_at"][:10])
        return (as_of - last_date).days

    def days_since_last_call(self, patient_id: str, as_of: date) -> int | None:
        last = self.last_call_for(patient_id)
        if not last:
            return None
        last_date = date.fromisoformat(last["timestamp"][:10])
        return (as_of - last_date).days

    def days_since_last_outreach(self, patient_id: str, as_of: date) -> int | None:
        """Like days_since_last_call, but counts a human-queued task too --
        the cooldown should hold regardless of which channel last reached
        the patient."""
        outreach = self.all_outreach_for(patient_id)
        if not outreach:
            return None
        last = outreach[-1]
        last_date = date.fromisoformat((last.get("timestamp") or last.get("created_at"))[:10])
        return (as_of - last_date).days
