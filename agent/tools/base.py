"""
Abstract tool interfaces.

Every external system the agent needs (EMR, compliance portal, email,
outbound voice) is accessed only through these interfaces. Today, every
implementation in this package is a "Mock*" class that reads local JSON
fixtures / writes local files -- NO network calls happen anywhere in this
codebase.

To go live later: write a new class per interface (e.g. `AirViewApiTool`,
`VapiCallTool`) that implements the same methods and calls the real API, then
swap which class gets instantiated in orchestrator.py. Nothing in agent/agents/
should need to change, because it only ever talks to these interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from agent.models import ComplianceSnapshot, Patient


class EmrTool(ABC):
    @abstractmethod
    def list_patients(self) -> list[Patient]:
        """Return the roster of patients to evaluate."""

    @abstractmethod
    def get_patient(self, patient_id: str) -> Optional[Patient]:
        ...


class ComplianceDataTool(ABC):
    """Represents AirView / DME-Link / vendor compliance portals (ResMed,
    ReactHealth, etc.) normalized to one shape."""

    @abstractmethod
    def get_latest_snapshot(self, patient_id: str) -> Optional[ComplianceSnapshot]:
        ...


class EmailTool(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, body: str, patient_id: str) -> dict:
        """Send (or, in test mode, simulate) an email. Returns a record dict
        with at least {message_id, to, subject, sent_at}."""


class VoiceCallTool(ABC):
    @abstractmethod
    def place_call(self, patient: Patient, call_purpose: str, context: dict) -> dict:
        """Place (or, in test mode, simulate) an outbound call. Returns a
        call record dict with at least
        {call_id, patient_id, timestamp, duration_sec, transcript, outcome_tag,
        escalate_to_rt}."""
