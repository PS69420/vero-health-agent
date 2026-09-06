"""
Small, static "knowledge base" of policy/clinical reference text and the
per-agent escalation-flag reason tables.

Content lives in data/knowledge_base.json; this module just loads it. This is
static, hand-authored reference text -- fixed policy/clinical snippets that
don't vary per patient. For what a SPECIFIC patient has actually said in past
calls, see agent/semantic_memory.py instead, which is the real, per-patient
searchable memory layer this module's older docstring used to describe as a
placeholder.
"""

import json
from pathlib import Path

_KB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.json"

with open(_KB_PATH) as _f:
    _kb = json.load(_f)

MEDICARE_COMPLIANCE_RULE: str = _kb["medicare_compliance_rule"]
ESCALATION_POLICY: str = _kb["escalation_policy"]
BARRIER_TIPS: dict = _kb["barrier_tips"]
BARRIER_PATIENT_LINES: dict = _kb["barrier_patient_lines"]
BARRIER_KEYWORDS: dict = _kb["barrier_keywords"]
BARRIER_FOLLOWUP_LINES: dict = _kb["barrier_followup_lines"]

# Flags that mean "don't auto-act, a clinician needs to look at this" --
# one reason-table per agent, since the same clinical flag can warrant a
# different explanation depending on whether it's blocking an equipment
# order vs. blocking an outreach call.
DME_ESCALATION_FLAGS: dict = _kb["escalation_flags"]["dme_needs"]
COMPLIANCE_ESCALATION_FLAGS: dict = _kb["escalation_flags"]["compliance"]
