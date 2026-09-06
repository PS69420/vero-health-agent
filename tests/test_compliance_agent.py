import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.memory import JsonEpisodicMemory
from agent.models import ComplianceSnapshot, Patient, PapStatus
from agent.agents.compliance_agent import ComplianceAgent


class FakeVoiceTool:
    def __init__(self, outcome="acknowledged_will_improve", escalate=False):
        self.calls = []
        self.outcome = outcome
        self.escalate = escalate

    def place_call(self, patient, call_purpose, context):
        record = {
            "call_id": f"FAKECALL-{len(self.calls)}",
            "patient_id": patient.patient_id,
            "timestamp": "2026-06-01T00:00:00",
            "call_purpose": call_purpose,
            "duration_sec": 120,
            "transcript": [{"speaker": "agent", "text": "hi"}],
            "outcome_tag": self.outcome,
            "escalate_to_rt": self.escalate,
            "simulated": True,
        }
        self.calls.append(record)
        return record


class FakeHumanQueueTool:
    def __init__(self):
        self.tasks = []

    def queue_call(self, patient, call_purpose, context, reason="ai_contact_consent_declined"):
        record = {
            "task_id": f"FAKETASK-{len(self.tasks)}",
            "patient_id": patient.patient_id,
            "created_at": (context.get("as_of").isoformat() if context.get("as_of") else "2026-06-01") + "T00:00:00",
            "call_purpose": call_purpose,
            "reason": reason,
            "status": "pending_human_callback",
            "simulated": True,
        }
        self.tasks.append(record)
        return record


def _patient(pid="p1", flags=None, ai_contact_consent=True):
    return Patient(patient_id=pid, name="Test, Patient", dob=date(1980, 1, 1),
                    pap_status=PapStatus(on_pap=True, device_type="CPAP"),
                    clinical_flags=flags or [], ai_contact_consent=ai_contact_consent)


def _snapshot(pid, therapy_start, period_end, days_used_ge_4hr, days_in_period=30):
    return ComplianceSnapshot(
        patient_id=pid, source_system="Mock", device_model="X", mode_on_device="CPAP",
        therapy_start_date=therapy_start,
        report_period_start=date(period_end.year, period_end.month, 1),
        report_period_end=period_end,
        days_in_period=days_in_period, days_used=days_in_period,
        days_used_ge_4hr=days_used_ge_4hr, avg_usage_hours=3.0,
    )


class TestComplianceAgent(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.memory = JsonEpisodicMemory(self.tmpdir / "mem.json")
        self.human_queue = FakeHumanQueueTool()

    def test_compliant_patient_no_call(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 2, 14))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 15), date(2026, 2, 13), days_used_ge_4hr=25)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "log_compliant")
        self.assertEqual(len(voice.calls), 0)

    def test_non_compliant_around_day_12_triggers_early_call(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "schedule_early_outreach")
        self.assertEqual(len(voice.calls), 1)

    def test_second_call_within_cooldown_is_skipped(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        agent.evaluate(p, snap)  # first call placed
        agent2 = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 14))
        decision2 = agent2.evaluate(p, snap)
        self.assertEqual(decision2.action, "none")
        self.assertEqual(len(voice.calls), 1)

    def test_vulnerable_patient_escalates_instead_of_calling(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient(flags=["non_verbal_patient_caregiver_required"])
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "escalate_human")
        self.assertEqual(len(voice.calls), 0)

    def test_insufficient_data_window_no_call(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 5))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 4), days_used_ge_4hr=3, days_in_period=3)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "none")
        self.assertEqual(len(voice.calls), 0)

    def test_backtest_across_dates_uses_as_of_not_wall_clock_for_cooldown(self):
        """Regression test: the real MockVoiceCallTool must timestamp calls
        against the simulated `as_of` date, not real wall-clock time -- a
        backtest that replays several historical dates in one process (e.g.
        the dashboard's time-scrubber) executes them all within the same
        real instant, so a wall-clock timestamp would make every call look
        like it happened "today" and the 7-day cooldown would compare against
        the wrong dates entirely."""
        from agent.tools.voice_tool import MockVoiceCallTool
        voice = MockVoiceCallTool()
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)

        # Day 70 of therapy: within the final (pre-90-day) outreach window.
        agent1 = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 3, 12))
        decision1 = agent1.evaluate(p, snap)
        self.assertEqual(decision1.action, "schedule_urgent_outreach")

        # 15 simulated days later (day 85) -- well past the 7-day cooldown,
        # so this call must still go out even though both evaluations happen
        # in the same real-world millisecond.
        agent2 = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 3, 27))
        decision2 = agent2.evaluate(p, snap)
        self.assertEqual(decision2.action, "schedule_urgent_outreach")

        calls = self.memory.calls_for("p1")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0]["timestamp"].startswith("2026-03-12"))
        self.assertTrue(calls[1]["timestamp"].startswith("2026-03-27"))

    def test_no_ai_consent_routes_to_human_queue_instead_of_calling(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient(ai_contact_consent=False)
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "schedule_early_outreach")
        self.assertEqual(decision.outreach_channel, "human")
        self.assertFalse(decision.requires_human_escalation)  # routing, not a clinical escalation
        self.assertEqual(len(voice.calls), 0)
        self.assertEqual(len(self.human_queue.tasks), 1)

    def test_no_ai_consent_still_respects_cross_channel_cooldown(self):
        """A human-queued task counts against the same outreach cooldown as
        an AI call -- we shouldn't queue a second callback 2 days after the
        first just because it went through the human channel instead."""
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient(ai_contact_consent=False)
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        agent.evaluate(p, snap)  # first task queued
        agent2 = ComplianceAgent(voice_tool=voice, human_queue_tool=self.human_queue, memory=self.memory, as_of=date(2026, 1, 14))
        decision2 = agent2.evaluate(p, snap)
        self.assertEqual(decision2.action, "none")
        self.assertEqual(len(self.human_queue.tasks), 1)


if __name__ == "__main__":
    unittest.main()
