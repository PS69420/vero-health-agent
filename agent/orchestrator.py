"""
Runs the DME Needs Agent and Compliance Agent across the full patient roster
and writes a human-readable summary report plus machine-readable JSON
results to output/.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from agent.agents.compliance_agent import ComplianceAgent
from agent.agents.dme_needs_agent import DmeNeedsAgent
from agent.memory import JsonEpisodicMemory
from agent.tools.compliance_data_tool import MockComplianceDataTool
from agent.tools.email_tool import MockEmailTool
from agent.tools.emr_tool import MockEmrTool
from agent.tools.voice_tool import MockVoiceCallTool

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def build_default_toolset(output_dir: Path = OUTPUT_DIR):
    emr = MockEmrTool(DATA_DIR / "patients.json")
    compliance_data = MockComplianceDataTool(DATA_DIR / "compliance.json")
    email = MockEmailTool(output_dir / "emails")
    voice = MockVoiceCallTool()
    memory = JsonEpisodicMemory(output_dir / "memory" / "episodic_memory.json")
    return emr, compliance_data, email, voice, memory


def run_all(as_of: date | None = None, output_dir: Path = OUTPUT_DIR) -> dict:
    as_of = as_of or date.today()
    output_dir.mkdir(parents=True, exist_ok=True)

    emr, compliance_data, email, voice, memory = build_default_toolset(output_dir)
    dme_agent = DmeNeedsAgent(email_tool=email, memory=memory, as_of=as_of)
    compliance_agent = ComplianceAgent(voice_tool=voice, memory=memory, as_of=as_of)

    patients = emr.list_patients()

    dme_results = [
        dme_agent.evaluate(p, compliance_data.get_latest_snapshot(p.patient_id))
        for p in patients
    ]
    compliance_results = [
        compliance_agent.evaluate(p, compliance_data.get_latest_snapshot(p.patient_id))
        for p in patients
    ]

    (output_dir / "dme_recommendations.json").write_text(
        json.dumps([asdict(r) for r in dme_results], indent=2, default=_json_default)
    )
    (output_dir / "compliance_decisions.json").write_text(
        json.dumps([asdict(r) for r in compliance_results], indent=2, default=_json_default)
    )

    report = _build_summary_report(as_of, patients, dme_results, compliance_results)
    (output_dir / "summary_report.md").write_text(report)

    return {
        "as_of": as_of.isoformat(),
        "patients_evaluated": len(patients),
        "dme_results": dme_results,
        "compliance_results": compliance_results,
        "report_path": str(output_dir / "summary_report.md"),
    }


def _build_summary_report(as_of, patients, dme_results, compliance_results) -> str:
    by_id = {p.patient_id: p for p in patients}
    lines = [f"# Vero Health Agent - Run Summary ({as_of.isoformat()})\n"]

    lines.append("## DME Needs Agent\n")
    action_groups: dict[str, list] = {}
    for r in dme_results:
        action_groups.setdefault(r.action, []).append(r)

    for action, label in [
        ("order_initial_pap", "Initial PAP order requested"),
        ("equipment_upgrade", "Equipment/settings update requested"),
        ("supply_resupply", "Routine supply resupply (no physician order needed)"),
        ("no_action", "No action needed"),
    ]:
        group = action_groups.get(action, [])
        if not group:
            continue
        lines.append(f"### {label} ({len(group)})\n")
        for r in group:
            flag = " ⚠️ ESCALATED TO HUMAN" if r.requires_human_escalation else ""
            lines.append(f"- **{r.patient_name}** ({r.patient_id}){flag}")
            for reason in r.rationale:
                lines.append(f"    - {reason}")
            for reason in r.escalation_reasons:
                lines.append(f"    - ⚠️ {reason}")
        lines.append("")

    lines.append("## Compliance Agent\n")
    comp_groups: dict[str, list] = {}
    for r in compliance_results:
        comp_groups.setdefault(r.action, []).append(r)

    for action, label in [
        ("schedule_urgent_outreach", "Urgent outreach call placed (approaching 90-day cutoff)"),
        ("schedule_early_outreach", "Early outreach call placed (~2 weeks before day 30)"),
        ("schedule_maintenance_outreach", "Long-term adherence outreach call placed"),
        ("escalate_human", "Escalated to human (clinical flag or data issue)"),
        ("log_compliant", "Compliant - no action needed"),
        ("none", "No action this run"),
    ]:
        group = comp_groups.get(action, [])
        if not group:
            continue
        lines.append(f"### {label} ({len(group)})\n")
        for r in group:
            lines.append(
                f"- **{r.patient_name}** ({r.patient_id}) - day {r.day_of_therapy} of therapy, "
                f"{r.pct_nights_ge_4hr}% nights >= 4hr"
            )
            for reason in r.rationale:
                lines.append(f"    - {reason}")
            for reason in r.escalation_reasons:
                lines.append(f"    - ⚠️ {reason}")
        lines.append("")

    return "\n".join(lines)
