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


def _patient(pid="p1", flags=None):
    return Patient(patient_id=pid, name="Test, Patient", dob=date(1980, 1, 1),
                    pap_status=PapStatus(on_pap=True, device_type="CPAP"),
                    clinical_flags=flags or [])


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

    def test_compliant_patient_no_call(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, memory=self.memory, as_of=date(2026, 2, 14))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 15), date(2026, 2, 13), days_used_ge_4hr=25)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "log_compliant")
        self.assertEqual(len(voice.calls), 0)

    def test_non_compliant_around_day_12_triggers_early_call(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "schedule_early_outreach")
        self.assertEqual(len(voice.calls), 1)

    def test_second_call_within_cooldown_is_skipped(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        agent.evaluate(p, snap)  # first call placed
        agent2 = ComplianceAgent(voice_tool=voice, memory=self.memory, as_of=date(2026, 1, 14))
        decision2 = agent2.evaluate(p, snap)
        self.assertEqual(decision2.action, "none")
        self.assertEqual(len(voice.calls), 1)

    def test_vulnerable_patient_escalates_instead_of_calling(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, memory=self.memory, as_of=date(2026, 1, 12))
        p = _patient(flags=["non_verbal_patient_caregiver_required"])
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 11), days_used_ge_4hr=5)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "escalate_human")
        self.assertEqual(len(voice.calls), 0)

    def test_insufficient_data_window_no_call(self):
        voice = FakeVoiceTool()
        agent = ComplianceAgent(voice_tool=voice, memory=self.memory, as_of=date(2026, 1, 5))
        p = _patient()
        snap = _snapshot("p1", date(2026, 1, 1), date(2026, 1, 4), days_used_ge_4hr=3, days_in_period=3)
        decision = agent.evaluate(p, snap)
        self.assertEqual(decision.action, "none")
        self.assertEqual(len(voice.calls), 0)


if __name__ == "__main__":
    unittest.main()
