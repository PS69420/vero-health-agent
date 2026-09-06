"""
Real Vapi outbound-call connector.

Unlike everything else in this codebase, this module makes a REAL network
call to a REAL, paid, real-world-effect API -- it rings an actual phone.
It is deliberately NOT wired into the automated ComplianceAgent scheduling
path (that still uses the safe MockVoiceCallTool). This module is only ever
invoked by an explicit human action: the "Call now" button in the local
console (console/server.py).

Credentials come from environment variables (see .env, gitignored):
    VAPI_API_KEY              - private API key
    VAPI_ASSISTANT_ID         - the assistant that places the call
    VAPI_PHONE_NUMBER_ID      - the Vapi-provisioned number it calls FROM
    VAPI_TEST_OVERRIDE_NUMBER - while testing, EVERY call goes to this
                                 number instead of a real patient's, so test
                                 calls always land on a phone you control.
                                 Remove this override once real patient
                                 phone numbers are wired in from the EMR.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

VAPI_API_BASE = "https://api.vapi.ai"
# Vapi's API sits behind Cloudflare, which blocks Python's default
# urllib User-Agent as a bot signature (HTTP 403 / Cloudflare error 1010) --
# any normal-looking one avoids it.
_USER_AGENT = "VeroHealthAgent-LocalConsole/1.0"


class VapiConfigError(RuntimeError):
    """Credentials missing/invalid, or the Vapi API returned an error."""


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise VapiConfigError(f"{name} is not set (check your .env file).")
    return val


def _api_get(path: str) -> dict:
    req = urllib.request.Request(
        f"{VAPI_API_BASE}{path}",
        headers={"Authorization": f"Bearer {_env('VAPI_API_KEY')}", "User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise VapiConfigError(f"GET {path} -> HTTP {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise VapiConfigError(f"GET {path} -> network error: {e.reason}")


def check_ready() -> dict:
    """Read-only health check: confirms the API key, assistant, and phone
    number are all valid and reachable. NEVER places a call."""
    try:
        assistant_id = _env("VAPI_ASSISTANT_ID")
        phone_id = _env("VAPI_PHONE_NUMBER_ID")
        _env("VAPI_API_KEY")
        _env("VAPI_TEST_OVERRIDE_NUMBER")
    except VapiConfigError as e:
        return {"ready": False, "detail": str(e)}

    try:
        assistant = _api_get(f"/assistant/{assistant_id}")
        phone = _api_get(f"/phone-number/{phone_id}")
    except VapiConfigError as e:
        return {"ready": False, "detail": str(e)}

    return {
        "ready": True,
        "detail": f"Connected as \"{assistant.get('name', assistant_id)}\", calling from {phone.get('number', phone_id)}.",
        "assistant_name": assistant.get("name"),
        "from_number": phone.get("number"),
    }


def _first_name(full_name: str) -> str:
    if "," in full_name:
        return full_name.split(",")[1].strip().split()[0]
    return full_name.split()[0]


def _variable_values(patient, snapshot, recall_note: Optional[str] = None) -> dict:
    """Matches the {{...}} template variables in the configured Vapi
    assistant's system prompt exactly -- see PATIENT DATA section there.
    `previousCallNotes` is extra context from semantic memory (see
    agent/semantic_memory.py) -- it's only useful once the assistant's
    prompt actually references {{previousCallNotes}}; Vapi silently ignores
    variables a prompt doesn't use, so passing it costs nothing either way."""
    device = "unknown"
    if patient.pap_status and patient.pap_status.on_pap:
        device = patient.pap_status.device_type or "PAP device"
        if patient.pap_status.pressure:
            device = f"{device} ({patient.pap_status.pressure})"
    return {
        "patientName": _first_name(patient.name),
        "device": device,
        "avgUsage": f"{snapshot.avg_usage_hours} hours" if snapshot else "unknown",
        "compliancePct": str(round(snapshot.pct_nights_ge_4hr)) if snapshot else "unknown",
        "daysOver4h": str(snapshot.days_used_ge_4hr) if snapshot else "unknown",
        "totalDays": str(snapshot.days_in_period) if snapshot else "unknown",
        "ahi": str(snapshot.ahi) if snapshot and snapshot.ahi is not None else "unknown",
        "previousCallNotes": recall_note or "No prior call notes on file.",
    }


def fetch_call(call_id: str) -> dict:
    """Read-only: fetches a call's current status/transcript/summary from
    Vapi. Placing a call returns immediately (status "queued"); the
    transcript only exists once the call has actually ended, so callers
    should poll this after some delay rather than expecting it right away."""
    body = _api_get(f"/call/{call_id}")
    return {
        "call_id": body.get("id"),
        "status": body.get("status"),
        "ended_reason": body.get("endedReason"),
        "transcript": body.get("transcript"),
        "summary": body.get("summary"),
    }


def place_manual_call(patient, snapshot=None, recall_note: Optional[str] = None) -> dict:
    """Places a REAL outbound call via Vapi, to VAPI_TEST_OVERRIDE_NUMBER
    (not the patient's real number -- see module docstring). Only ever call
    this in direct response to an explicit human button press."""
    to_number = _env("VAPI_TEST_OVERRIDE_NUMBER")
    variable_values = _variable_values(patient, snapshot, recall_note)
    payload = {
        "assistantId": _env("VAPI_ASSISTANT_ID"),
        "phoneNumberId": _env("VAPI_PHONE_NUMBER_ID"),
        "customer": {"number": to_number, "name": patient.name},
        "assistantOverrides": {"variableValues": variable_values},
    }
    req = urllib.request.Request(
        f"{VAPI_API_BASE}/call",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {_env('VAPI_API_KEY')}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise VapiConfigError(f"POST /call -> HTTP {e.code}: {e.read().decode()[:500]}")
    except urllib.error.URLError as e:
        raise VapiConfigError(f"POST /call -> network error: {e.reason}")

    return {
        "call_id": body.get("id"),
        "status": body.get("status"),
        "to_number": to_number,
        "patient_id": patient.patient_id,
        "variable_values": variable_values,
        "transcript": None,
        "transcript_synced": False,  # console/server.py fills this in once the call ends
        "simulated": False,
    }
