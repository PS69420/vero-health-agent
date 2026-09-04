"""
Mock EMR connector.

Reads data/patients.json instead of calling a real EMR API. Replace with a
real `EmrTool` implementation (talking to your EMR's API/HL7-FHIR feed) when
ready -- keep the same list_patients()/get_patient() contract.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from agent.models import Patient, PapStatus, SleepStudy
from agent.tools.base import EmrTool


def _parse_date(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


class MockEmrTool(EmrTool):
    def __init__(self, data_path: Path):
        with open(data_path, "r") as f:
            raw = json.load(f)
        self._patients = [self._to_patient(p) for p in raw["patients"]]
        self._by_id = {p.patient_id: p for p in self._patients}

    @staticmethod
    def _to_patient(p: dict) -> Patient:
        studies = [
            SleepStudy(
                type=s["type"],
                date=_parse_date(s["date"]),
                ahi=s.get("ahi"),
                severity=s.get("severity"),
                pressure_cmh2o=s.get("pressure_cmh2o"),
                ahi_on_pressure=s.get("ahi_on_pressure"),
                rem_ahi=s.get("rem_ahi"),
                plm_index=s.get("plm_index"),
                plm_index_on_pressure=s.get("plm_index_on_pressure"),
                plm_severity=s.get("plm_severity"),
                device=s.get("device"),
                notes=s.get("notes"),
            )
            for s in p.get("sleep_studies", [])
        ]
        pap_raw = p.get("current_pap_status") or {}
        pap = PapStatus(
            on_pap=pap_raw.get("on_pap", False),
            device_type=pap_raw.get("device_type"),
            pressure=pap_raw.get("pressure"),
            order_date=_parse_date(pap_raw.get("order_date")),
            device_platform=pap_raw.get("device_platform"),
            sn_assigned_date=_parse_date(pap_raw.get("sn_assigned_date")),
            device_age_years=pap_raw.get("device_age_years"),
        )
        return Patient(
            patient_id=p["patient_id"],
            name=p["name"],
            dob=_parse_date(p["dob"]),
            sleep_studies=studies,
            pap_status=pap,
            office_note_summary=p.get("office_note_summary", ""),
            clinical_flags=list(p.get("clinical_flags", [])),
            is_synthetic=bool(p.get("_synthetic", False)),
        )

    def list_patients(self) -> list[Patient]:
        return list(self._patients)

    def get_patient(self, patient_id: str) -> Optional[Patient]:
        return self._by_id.get(patient_id)
