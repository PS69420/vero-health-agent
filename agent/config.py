"""
Business rules and thresholds for the Vero Health Agent.

The actual values live in data/config.json -- plain data, not code, so
they're cheap to read and edit (no need to touch Python, or re-read a whole
module's worth of comments, to retune a clinical threshold or the compliance
call-timing policy). This module just loads them into small typed objects
the rest of the codebase imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"


@dataclass(frozen=True)
class ClinicalThresholds:
    ahi_normal_max: float
    ahi_mild_max: float
    ahi_moderate_max: float
    ahi_order_pap_threshold: float
    residual_ahi_uncontrolled_threshold: float
    plm_severe_threshold: float
    leak_significant_lmin: float
    device_replacement_age_years: float


@dataclass(frozen=True)
class ComplianceRules:
    """
    Medicare (CMS) PAP compliance definition, LCD-based:
    Usage of >= required_daily_hours per night on >= required_pct_nights of
    nights during ANY consecutive compliance_window_days period within the
    first insurance_review_window_days of therapy. Failing this, coverage
    can be discontinued.
    """
    required_daily_hours: float
    required_pct_nights: float
    compliance_window_days: int
    insurance_review_window_days: int
    first_outreach_day: int
    first_outreach_window_days: int
    final_outreach_day: int
    final_outreach_window_days: int
    min_days_between_calls: int


with open(_CONFIG_PATH) as _f:
    _raw = json.load(_f)

CLINICAL = ClinicalThresholds(**_raw["clinical_thresholds"])
COMPLIANCE = ComplianceRules(**_raw["compliance_rules"])

# Sleep center / DME company identity used in generated emails & call scripts.
ORG_NAME = _raw["org"]["name"]
ORG_SCHEDULING_PHONE = _raw["org"]["scheduling_phone"]
ORG_FROM_EMAIL = _raw["org"]["from_email"]
DOCTOR_EMAIL_FOLLOWUP_COOLDOWN_DAYS = _raw["org"]["doctor_email_followup_cooldown_days"]
