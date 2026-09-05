"""
Lightweight sanity tests -- no pytest dependency required, run with:
    python3 -m tests.test_dme_needs_agent
Also picked up by `python3 -m unittest discover` if you have it configured.
"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.memory import JsonEpisodicMemory
from agent.models import Patient, PapStatus, SleepStudy
from agent.agents.dme_needs_agent import DmeNeedsAgent


class FakeEmailTool:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, patient_id, as_of=None):
        record = {"message_id": f"FAKE-{len(self.sent)}", "to": to, "subject": subject, "sent_at": "2026-01-01T00:00:00"}
        self.sent.append((subject, body, patient_id))
        return record


def _tmp_memory(tmp_path: Path) -> JsonEpisodicMemory:
    return JsonEpisodicMemory(tmp_path / "mem.json")


class TestDmeNeedsAgent(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = Path(tempfile.mkdtemp())
        self.email = FakeEmailTool()
        self.memory = _tmp_memory(self.tmpdir)
        self.agent = DmeNeedsAgent(email_tool=self.email, memory=self.memory)

    def test_treatment_naive_high_ahi_triggers_order_email(self):
        patient = Patient(
            patient_id="t1", name="Test, Naive", dob=date(1980, 1, 1),
            sleep_studies=[SleepStudy(type="HST_diagnostic", date=date(2026, 1, 1), ahi=40.0, severity="severe")],
            pap_status=PapStatus(on_pap=False),
        )
        rec = self.agent.evaluate(patient)
        self.assertEqual(rec.action, "order_initial_pap")
        self.assertFalse(rec.requires_human_escalation)
        self.assertEqual(len(self.email.sent), 1)

    def test_treatment_naive_low_ahi_no_action(self):
        patient = Patient(
            patient_id="t2", name="Test, Mild", dob=date(1980, 1, 1),
            sleep_studies=[SleepStudy(type="HST_diagnostic", date=date(2026, 1, 1), ahi=3.0, severity="normal")],
            pap_status=PapStatus(on_pap=False),
        )
        rec = self.agent.evaluate(patient)
        self.assertEqual(rec.action, "no_action")
        self.assertEqual(len(self.email.sent), 0)

    def test_central_apnea_flag_escalates_instead_of_emailing(self):
        patient = Patient(
            patient_id="t3", name="Test, Complex", dob=date(1980, 1, 1),
            sleep_studies=[
                SleepStudy(type="CPAP_titration", date=date(2026, 1, 1), pressure_cmh2o=15, ahi_on_pressure=20.0)
            ],
            pap_status=PapStatus(on_pap=True, device_type="CPAP", pressure="15"),
            clinical_flags=["central_apnea_component_at_diagnosis"],
        )
        rec = self.agent.evaluate(patient)
        self.assertTrue(rec.requires_human_escalation)
        self.assertEqual(len(self.email.sent), 0)

    def test_well_controlled_no_action(self):
        patient = Patient(
            patient_id="t4", name="Test, Controlled", dob=date(1980, 1, 1),
            sleep_studies=[SleepStudy(type="CPAP_titration", date=date(2026, 1, 1), pressure_cmh2o=10, ahi_on_pressure=1.0)],
            pap_status=PapStatus(on_pap=True, device_type="CPAP", pressure="10"),
        )
        rec = self.agent.evaluate(patient)
        self.assertEqual(rec.action, "no_action")
        self.assertFalse(rec.requires_human_escalation)

    def test_dedup_uses_as_of_not_wall_clock_for_email_cooldown(self):
        """Regression test companion to the compliance-agent backtest test:
        the real MockEmailTool must timestamp against `as_of`, not wall-clock
        time, or a backtest replaying historical dates in one process would
        misjudge the 14-day doctor-followup cooldown."""
        from agent.tools.email_tool import MockEmailTool
        import tempfile
        real_email = MockEmailTool(Path(tempfile.mkdtemp()))
        agent = DmeNeedsAgent(email_tool=real_email, memory=self.memory, as_of=date(2026, 1, 1))
        patient = Patient(
            patient_id="t5", name="Test, Naive2", dob=date(1980, 1, 1),
            sleep_studies=[SleepStudy(type="HST_diagnostic", date=date(2025, 12, 1), ahi=40.0, severity="severe")],
            pap_status=PapStatus(on_pap=False),
        )
        agent.evaluate(patient)
        record = self.memory.emails_for("t5")[0]
        self.assertTrue(record["sent_at"].startswith("2026-01-01"))

        # 20 simulated days later -- past the 14-day cooldown, evaluated in
        # the same real-world instant as the first call above.
        agent2 = DmeNeedsAgent(email_tool=real_email, memory=self.memory, as_of=date(2026, 1, 21))
        days_since = self.memory.days_since_last_email_of_type("t5", "order_initial_pap", agent2.as_of)
        self.assertEqual(days_since, 20)


if __name__ == "__main__":
    unittest.main()
