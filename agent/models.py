"""
Shared data shapes used across tools and agents.

Kept as plain dataclasses (no pydantic/ORM dependency) so the test system
runs with zero pip installs. When real connectors (EMR, AirView, Vapi) are
wired in, they should adapt their payloads INTO these shapes -- nothing
downstream should need to change.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class SleepStudy:
    type: str
    date: date
    ahi: Optional[float] = None
    severity: Optional[str] = None
    pressure_cmh2o: Optional[float] = None
    ahi_on_pressure: Optional[float] = None
    rem_ahi: Optional[float] = None
    plm_index: Optional[float] = None
    plm_index_on_pressure: Optional[float] = None
    plm_severity: Optional[str] = None
    device: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class PapStatus:
    on_pap: bool
    device_type: Optional[str] = None
    pressure: Optional[str] = None
    order_date: Optional[date] = None
    device_platform: Optional[str] = None
    sn_assigned_date: Optional[date] = None
    device_age_years: Optional[float] = None


@dataclass
class Patient:
    patient_id: str
    name: str
    dob: date
    sleep_studies: list[SleepStudy] = field(default_factory=list)
    pap_status: Optional[PapStatus] = None
    office_note_summary: str = ""
    clinical_flags: list[str] = field(default_factory=list)
    is_synthetic: bool = False
    # Whether the patient has consented to being contacted by the AI voice
    # agent (Vapi). Defaults True so existing fixtures/tests don't need to
    # opt in explicitly; a real EMR feed should always send this explicitly.
    # When False, the compliance agent routes outreach to a human caller
    # instead of placing an automated call.
    ai_contact_consent: bool = True

    def latest_study(self) -> Optional[SleepStudy]:
        return max(self.sleep_studies, key=lambda s: s.date) if self.sleep_studies else None

    def latest_titration(self) -> Optional[SleepStudy]:
        titrations = [s for s in self.sleep_studies if "titration" in s.type.lower()]
        return max(titrations, key=lambda s: s.date) if titrations else None

    def latest_diagnostic(self) -> Optional[SleepStudy]:
        diags = [s for s in self.sleep_studies if "diagnostic" in s.type.lower()]
        return max(diags, key=lambda s: s.date) if diags else None


@dataclass
class ComplianceSnapshot:
    patient_id: str
    source_system: str
    device_model: str
    mode_on_device: str
    therapy_start_date: date
    report_period_start: date
    report_period_end: date
    days_in_period: int
    days_used: int
    days_used_ge_4hr: int
    avg_usage_hours: float
    ahi: Optional[float] = None
    leak_95th_percentile_Lmin: Optional[float] = None
    device_fault_reported: bool = False
    note: Optional[str] = None

    @property
    def pct_nights_ge_4hr(self) -> float:
        if self.days_in_period <= 0:
            return 0.0
        return round(100.0 * self.days_used_ge_4hr / self.days_in_period, 1)


@dataclass
class DmeRecommendation:
    patient_id: str
    patient_name: str
    action: str  # "order_initial_pap" | "equipment_upgrade" | "supply_resupply" | "no_action"
    rationale: list[str]
    requires_human_escalation: bool
    escalation_reasons: list[str] = field(default_factory=list)
    reasoning_trace: list[dict] = field(default_factory=list)


@dataclass
class ComplianceDecision:
    patient_id: str
    patient_name: str
    day_of_therapy: int
    within_90_day_window: bool
    compliant: bool
    pct_nights_ge_4hr: float
    action: str  # "none" | "log_compliant" | "schedule_early_outreach" | "schedule_urgent_outreach" | "schedule_maintenance_outreach" | "escalate_human"
    rationale: list[str]
    requires_human_escalation: bool = False
    escalation_reasons: list[str] = field(default_factory=list)
    reasoning_trace: list[dict] = field(default_factory=list)
    # Set only when `action` places outreach: "ai" (Vapi-style call) or
    # "human" (patient hasn't consented to AI contact -- queued for a staff
    # member to call instead). None for actions that don't place outreach.
    outreach_channel: Optional[str] = None
