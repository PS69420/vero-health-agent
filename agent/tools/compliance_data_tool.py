"""
Mock compliance-data connector (stands in for AirView / DME-Link / ReactHealth).

Reads data/compliance.json instead of calling any real vendor API. Real DME
operations pull from more than one vendor portal (ResMed AirView, ReactHealth,
etc.) with different field names -- normalizing them into one ComplianceSnapshot
shape here is exactly the job a real connector will do; the difference is only
where the raw data comes from.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from agent.models import ComplianceSnapshot
from agent.tools.base import ComplianceDataTool


class MockComplianceDataTool(ComplianceDataTool):
    def __init__(self, data_path: Path):
        with open(data_path, "r") as f:
            raw = json.load(f)
        self._by_patient: dict[str, ComplianceSnapshot] = {}
        for s in raw["compliance_snapshots"]:
            snap = ComplianceSnapshot(
                patient_id=s["patient_id"],
                source_system=s["source_system"],
                device_model=s["device_model"],
                mode_on_device=s["mode_on_device"],
                therapy_start_date=date.fromisoformat(s["therapy_start_date"]),
                report_period_start=date.fromisoformat(s["report_period_start"]),
                report_period_end=date.fromisoformat(s["report_period_end"]),
                days_in_period=s["days_in_period"],
                days_used=s["days_used"],
                days_used_ge_4hr=s["days_used_ge_4hr"],
                avg_usage_hours=s["avg_usage_hours"],
                ahi=s.get("ahi"),
                leak_95th_percentile_Lmin=s.get("leak_95th_percentile_Lmin"),
                device_fault_reported=s.get("device_fault_reported", False),
                note=s.get("note"),
            )
            # Keep the most recent snapshot per patient if more than one exists.
            existing = self._by_patient.get(s["patient_id"])
            if not existing or snap.report_period_end > existing.report_period_end:
                self._by_patient[s["patient_id"]] = snap

    def get_latest_snapshot(self, patient_id: str) -> Optional[ComplianceSnapshot]:
        return self._by_patient.get(patient_id)
