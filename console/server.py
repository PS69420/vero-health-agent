#!/usr/bin/env python3
"""
Local Vapi test console.

Runs ONLY on your machine. Unlike the claude.ai dashboard artifact, this page
is not sandboxed -- it's an ordinary local web server, so it can make real
outbound requests to the Vapi API, which is what makes the ready-light and
the "Call now" buttons actually work.

The automated compliance-agent scheduling logic (the same one behind
`main.py run-all`) is untouched and still uses the safe MockVoiceCallTool --
nothing here changes what the automated path does. This console adds one new,
separate, human-triggered action: pressing "Call now" places one real Vapi
call to VAPI_TEST_OVERRIDE_NUMBER (see .env), using that patient's real
compliance data as context for the assistant.

Usage:
    python3 console/server.py [--port 8787] [--no-browser]
Then open http://localhost:8787 (opens automatically unless --no-browser).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env_file():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()

from agent.agents.compliance_agent import ComplianceAgent  # noqa: E402
from agent.agents.dme_needs_agent import DmeNeedsAgent  # noqa: E402
from agent.orchestrator import build_default_toolset  # noqa: E402
from agent.tools import vapi_call_tool  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def get_roster() -> list[dict]:
    """Re-evaluates every patient with the real agent logic, exactly like
    `main.py run-all` would -- but through the safe MOCK email/voice tools,
    so this never triggers a real call or email on its own. Only reflects
    current status; the manual call button is the only real-world action."""
    emr, compliance_data, email, voice, human_queue, memory = build_default_toolset()
    as_of = date.today()
    dme_agent = DmeNeedsAgent(email_tool=email, memory=memory, as_of=as_of)
    compliance_agent = ComplianceAgent(voice_tool=voice, human_queue_tool=human_queue, memory=memory, as_of=as_of)

    roster = []
    for p in emr.list_patients():
        snap = compliance_data.get_latest_snapshot(p.patient_id)
        dme = dme_agent.evaluate(p, snap)
        comp = compliance_agent.evaluate(p, snap)
        roster.append({
            "patient_id": p.patient_id,
            "name": p.name,
            "is_synthetic": p.is_synthetic,
            "ai_contact_consent": p.ai_contact_consent,
            "dme_action": dme.action,
            "dme_escalated": dme.requires_human_escalation,
            "compliance_action": comp.action,
            "compliance_escalated": comp.requires_human_escalation,
            "compliant": comp.compliant,
            "day_of_therapy": comp.day_of_therapy,
            "pct_nights_ge_4hr": comp.pct_nights_ge_4hr,
            "avg_usage_hours": snap.avg_usage_hours if snap else None,
            "ahi": snap.ahi if snap else None,
            "manual_calls": memory.manual_calls_for(p.patient_id),
        })
    return roster


def place_call_for(patient_id: str) -> tuple[int, dict]:
    emr, compliance_data, _email, _voice, _human_queue, memory = build_default_toolset()
    patient = emr.get_patient(patient_id)
    if patient is None:
        return 404, {"error": f"Unknown patient_id: {patient_id}"}

    snap = compliance_data.get_latest_snapshot(patient_id)
    try:
        result = vapi_call_tool.place_manual_call(patient, snap)
    except vapi_call_tool.VapiConfigError as e:
        return 502, {"error": str(e)}

    result["requested_at"] = datetime.now().isoformat()
    memory.record_manual_call(patient_id, result)
    return 200, result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[console] {self.address_string()} {fmt % args}\n")

    def _send_json(self, status: int, payload):
        body = json.dumps(payload, default=_json_default).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        if not path.exists():
            self._send_json(404, {"error": "not found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/status":
            try:
                self._send_json(200, vapi_call_tool.check_ready())
            except Exception as e:  # noqa: BLE001 - surface any failure to the UI, don't crash the server
                self._send_json(200, {"ready": False, "detail": f"Unexpected error: {e}"})
        elif path == "/api/patients":
            try:
                self._send_json(200, {"patients": get_roster(), "as_of": date.today().isoformat()})
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/call/"):
            patient_id = path[len("/api/call/"):]
            status, payload = place_call_for(patient_id)
            self._send_json(status, payload)
        else:
            self._send_json(404, {"error": "not found"})


def main():
    parser = argparse.ArgumentParser(description="Local Vapi test console")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Vapi test console running at {url}")
    print("Every call placed here rings the real phone in VAPI_TEST_OVERRIDE_NUMBER (.env) -- not a simulation.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
