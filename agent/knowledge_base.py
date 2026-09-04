"""
Small, static "knowledge base" of policy/clinical reference text and the
per-agent escalation-flag reason tables.

Content lives in data/knowledge_base.json; this module just loads it. This
stands in for the RAG/semantic-memory layer in the fuller design: right now
it's a plain JSON lookup, which is all this scale needs. If you later want
the agent to answer open-ended questions ("why does Medicare require this?")
instead of just citing fixed snippets, swap this module for an
embedding-based retriever over the same source documents -- the call sites
(agents/*.py, tools/voice_tool.py) don't need to change, they just want data
back for a given key.
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

# Flags that mean "don't auto-act, a clinician needs to look at this" --
# one reason-table per agent, since the same clinical flag can warrant a
# different explanation depending on whether it's blocking an equipment
# order vs. blocking an outreach call.
DME_ESCALATION_FLAGS: dict = _kb["escalation_flags"]["dme_needs"]
COMPLIANCE_ESCALATION_FLAGS: dict = _kb["escalation_flags"]["compliance"]
