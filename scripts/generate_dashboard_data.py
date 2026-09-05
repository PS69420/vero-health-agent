#!/usr/bin/env python3
"""
Generates the data payload behind the Vero Health Agent dashboard artifact.

This does NOT reimplement the agents' logic in another language -- it drives
the real code (agent.orchestrator.run_all) across a sequence of historical
`--as-of` dates in a scratch working directory, letting memory accumulate
across dates exactly as it would in production (one call per date, in order).
That gives the dashboard a real, backtested "movie" of both agents' decisions
over time -- including the call/email cooldowns actually kicking in -- with
zero risk of a JS reimplementation drifting from what agent/agents/*.py
actually does.

It also runs the real unit test suite (tests/) and captures pass/fail per
test, so the dashboard's "Test suite" panel reflects an actual test run, not
a claim.

Usage:
    python3 scripts/generate_dashboard_data.py [--out output/dashboard_data.json]
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.orchestrator import run_all  # noqa: E402

# Biweekly cadence spanning the three patients still inside their 90-day
# compliance-proving window as of the sample data's "today" (2026-09-05) --
# chosen so at least one snapshot lands inside each patient's narrow ~13-day
# early-outreach window as well as their wide final-outreach window, showing
# the full lifecycle: too early -> early call -> monitoring -> urgent call(s)
# -> rolls into long-term maintenance once past day 90.
SNAPSHOT_DATES = [
    "2026-05-04", "2026-05-18", "2026-06-01", "2026-06-15", "2026-06-29",
    "2026-07-13", "2026-07-27", "2026-08-10", "2026-08-24", "2026-09-07",
    "2026-09-21", "2026-10-05",
]


def run_tests() -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"))
    collected: list[dict] = []

    class Collector(unittest.TestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            collected.append({"name": test.id().split(".")[-1], "doc": test.shortDescription() or "", "status": "pass"})

        def addFailure(self, test, err):
            super().addFailure(test, err)
            collected.append({"name": test.id().split(".")[-1], "doc": test.shortDescription() or "", "status": "fail",
                               "detail": self._exc_info_to_string(err, test)})

        def addError(self, test, err):
            super().addError(test, err)
            collected.append({"name": test.id().split(".")[-1], "doc": test.shortDescription() or "", "status": "error",
                               "detail": self._exc_info_to_string(err, test)})

    result = Collector()
    suite.run(result)
    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "total": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors),
        "failed": len(result.failures),
        "errored": len(result.errors),
        "tests": collected,
    }


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def _read_email_body(path: str) -> str | None:
    try:
        text = Path(path).read_text()
        parts = text.split("\n\n", 1)
        return parts[1].strip() if len(parts) > 1 else text.strip()
    except OSError:
        return None


def build_snapshot(as_of_str: str, backtest_dir: Path, patients_by_id: dict) -> dict:
    as_of = date.fromisoformat(as_of_str)
    result = run_all(as_of=as_of, output_dir=backtest_dir)
    mem = json.loads((backtest_dir / "memory" / "episodic_memory.json").read_text())["patients"]

    dme_by_id = {r.patient_id: r for r in result["dme_results"]}
    comp_by_id = {r.patient_id: r for r in result["compliance_results"]}

    patients_out = []
    for pid, p in patients_by_id.items():
        bucket = mem.get(pid, {"calls": [], "emails": []})
        emails = []
        for e in bucket.get("emails", []):
            e = dict(e)
            if e.get("file"):
                e["body"] = _read_email_body(e["file"])
            emails.append(e)
        patients_out.append({
            "patient_id": pid,
            "name": p["name"],
            "is_synthetic": bool(p.get("_synthetic", False)),
            "dme": asdict(dme_by_id[pid]),
            "compliance": asdict(comp_by_id[pid]),
            "emails": emails,
            "calls": bucket.get("calls", []),
        })

    def counts(results, key):
        out: dict = {}
        for r in results:
            out[getattr(r, key)] = out.get(getattr(r, key), 0) + 1
        return out

    stats = {
        "as_of": as_of_str,
        "patients_evaluated": len(patients_out),
        "dme_action_counts": counts(result["dme_results"], "action"),
        "compliance_action_counts": counts(result["compliance_results"], "action"),
        "dme_escalations": sum(1 for r in result["dme_results"] if r.requires_human_escalation),
        "compliance_escalations": sum(1 for r in result["compliance_results"] if r.requires_human_escalation),
        "emails_sent_cumulative": sum(len(v.get("emails", [])) for v in mem.values()),
        "calls_placed_cumulative": sum(len(v.get("calls", [])) for v in mem.values()),
        "emails_sent_this_date": sum(1 for pl in patients_out for e in pl["emails"] if e["sent_at"].startswith(as_of_str)),
        "calls_placed_this_date": sum(1 for pl in patients_out for c in pl["calls"] if c["timestamp"].startswith(as_of_str)),
    }
    return {"as_of": as_of_str, "stats": stats, "patients": patients_out}


def main():
    patients_raw = json.loads((ROOT / "data" / "patients.json").read_text())["patients"]
    patients_by_id = {p["patient_id"]: p for p in patients_raw}
    cfg = json.loads((ROOT / "data" / "config.json").read_text())

    backtest_dir = Path(tempfile.mkdtemp(prefix="vero_dashboard_backtest_"))
    snapshots = []
    try:
        for d in SNAPSHOT_DATES:
            snapshots.append(build_snapshot(d, backtest_dir, patients_by_id))
    finally:
        shutil.rmtree(backtest_dir, ignore_errors=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": cfg,
        "test_results": run_tests(),
        "snapshot_dates": SNAPSHOT_DATES,
        "snapshots": snapshots,
    }

    out_path = ROOT / "output" / "dashboard_data.json"
    if "--out" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--out") + 1])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, default=_json_default))

    tr = payload["test_results"]
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    print(f"tests: {tr['passed']}/{tr['total']} passed" + (f", {tr['failed']} failed, {tr['errored']} errored" if tr['failed'] or tr['errored'] else ""))
    print(f"snapshots: {len(snapshots)} ({SNAPSHOT_DATES[0]} .. {SNAPSHOT_DATES[-1]})")


if __name__ == "__main__":
    main()
