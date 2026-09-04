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

    def send(self, to, subject, body, patient_id):
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


if __name__ == "__main__":
    unittest.main()
