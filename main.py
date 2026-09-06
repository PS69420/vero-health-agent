#!/usr/bin/env python3
"""
CLI entrypoint for the Vero Health Agent test system.

No network calls happen anywhere in this program -- every "EMR", "AirView /
DME Link", "email", and "call" interaction reads/writes local files under
data/ and output/. See README.md for how to swap in real integrations later.

Usage:
    python3 main.py run-all [--as-of YYYY-MM-DD]
    python3 main.py list-patients
    python3 main.py dme-check [--as-of YYYY-MM-DD]
    python3 main.py compliance-check [--as-of YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date

from agent.orchestrator import OUTPUT_DIR, _json_default, build_default_toolset, run_all
from agent.agents.dme_needs_agent import DmeNeedsAgent
from agent.agents.compliance_agent import ComplianceAgent


def _parse_as_of(s: str | None) -> date:
    return date.fromisoformat(s) if s else date.today()


def cmd_list_patients(_args):
    emr, *_ = build_default_toolset()
    for p in emr.list_patients():
        pap = "on PAP" if (p.pap_status and p.pap_status.on_pap) else "not on PAP"
        tag = " [SYNTHETIC]" if p.is_synthetic else ""
        print(f"{p.patient_id}  {p.name:35s} {pap}{tag}")


def cmd_dme_check(args):
    as_of = _parse_as_of(args.as_of)
    emr, compliance_data, email, _voice, _human_queue, memory, semantic_memory = build_default_toolset()
    agent = DmeNeedsAgent(email_tool=email, memory=memory, as_of=as_of, semantic_memory=semantic_memory)
    results = [
        agent.evaluate(p, compliance_data.get_latest_snapshot(p.patient_id))
        for p in emr.list_patients()
    ]
    print(json.dumps([asdict(r) for r in results], indent=2, default=_json_default))


def cmd_compliance_check(args):
    as_of = _parse_as_of(args.as_of)
    emr, compliance_data, _email, voice, human_queue, memory, semantic_memory = build_default_toolset()
    agent = ComplianceAgent(voice_tool=voice, human_queue_tool=human_queue, memory=memory, as_of=as_of,
                             semantic_memory=semantic_memory)
    results = [
        agent.evaluate(p, compliance_data.get_latest_snapshot(p.patient_id))
        for p in emr.list_patients()
    ]
    print(json.dumps([asdict(r) for r in results], indent=2, default=_json_default))


def cmd_run_all(args):
    as_of = _parse_as_of(args.as_of)
    result = run_all(as_of=as_of)
    print(f"Evaluated {result['patients_evaluated']} patients as of {result['as_of']}.")
    print(f"Full report: {result['report_path']}")
    print(f"Emails (simulated): {OUTPUT_DIR / 'emails'}")
    print(f"Call transcripts + memory: {OUTPUT_DIR / 'memory' / 'episodic_memory.json'}")


def main():
    parser = argparse.ArgumentParser(description="Vero Health Agent - test/offline mode")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-patients", help="List all patients in the mock EMR")
    p_list.set_defaults(func=cmd_list_patients)

    p_dme = sub.add_parser("dme-check", help="Run only the DME Needs Agent")
    p_dme.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    p_dme.set_defaults(func=cmd_dme_check)

    p_comp = sub.add_parser("compliance-check", help="Run only the Compliance Agent")
    p_comp.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    p_comp.set_defaults(func=cmd_compliance_check)

    p_run = sub.add_parser("run-all", help="Run both agents and write the summary report")
    p_run.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today)")
    p_run.set_defaults(func=cmd_run_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
