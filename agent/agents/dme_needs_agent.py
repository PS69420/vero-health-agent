"""
DME Needs Agent.

For each patient, reasons over their sleep study / titration history (ReAct
style: Thought -> Action -> Observation, logged into a trace for audit) and
decides whether:
  - a treatment-naive patient's diagnostic study supports ordering initial PAP
  - an existing PAP patient's therapy looks inadequately controlled and needs
    an equipment/settings change (new order needed from the physician)
  - an existing PAP patient just needs routine supplies (mask/cushion), which
    does NOT require a physician order and is logged as an internal action
    item rather than an email
  - the case has a clinical red flag and should be escalated to a human
    instead of auto-acted on (see knowledge_base.ESCALATION_POLICY)

When a physician order is warranted, drafts (and "sends", via the mock
EmailTool) an email requesting it. Nothing here calls a real EMR, AirView, or
email API -- see agent/tools/*.py.
"""

from __future__ import annotations

from datetime import date

from agent import config
from agent.knowledge_base import DME_ESCALATION_FLAGS as _ESCALATION_FLAGS
from agent.memory import JsonEpisodicMemory, ShortTermTrace
from agent.models import DmeRecommendation, Patient
from agent.tools.base import EmailTool


def _draft_initial_pap_email(patient: Patient, diagnostic) -> tuple[str, str]:
    subject = f"PAP Order Requested - {patient.name} (DOB {patient.dob.isoformat()})"
    body = (
        f"Dr. [Ordering Physician],\n\n"
        f"{patient.name}'s {diagnostic.type.replace('_', ' ')} on {diagnostic.date.isoformat()} "
        f"showed an AHI of {diagnostic.ahi} ({diagnostic.severity}), which meets criteria for "
        f"PAP therapy.\n\n"
        f"Requesting a signed order for initial PAP therapy (APAP recommended as first-line) "
        f"so we can proceed with setup and patient education.\n\n"
        f"Please let us know if you'd like to see the full study before signing.\n\n"
        f"-- {config.ORG_NAME} DME Team"
    )
    return subject, body


def _draft_equipment_change_email(patient: Patient, reasons: list[str], titration) -> tuple[str, str]:
    subject = f"Updated PAP Order Requested - {patient.name} (DOB {patient.dob.isoformat()})"
    reason_lines = "\n".join(f"  - {r}" for r in reasons)
    titration_line = ""
    if titration:
        titration_line = (
            f"\nMost recent titration ({titration.date.isoformat()}): "
            f"{titration.pressure_cmh2o} cmH2O, residual AHI {titration.ahi_on_pressure}."
            + (f" Study note: {titration.notes}" if titration.notes else "")
        )
    body = (
        f"Dr. [Ordering Physician],\n\n"
        f"{patient.name}'s current therapy appears to need an updated order based on:\n"
        f"{reason_lines}\n"
        f"{titration_line}\n\n"
        f"Requesting a signed updated order reflecting the recommended settings/device change "
        f"above.\n\n"
        f"-- {config.ORG_NAME} DME Team"
    )
    return subject, body


class DmeNeedsAgent:
    def __init__(self, email_tool: EmailTool, memory: JsonEpisodicMemory, as_of: date | None = None):
        self.email_tool = email_tool
        self.memory = memory
        self.as_of = as_of or date.today()

    def _send_order_email(self, patient: Patient, email_type: str, to: str, subject: str, body: str, trace) -> str | None:
        """Sends (simulated) unless we already asked for this same thing recently.
        Returns the outcome note to add to the rationale."""
        days_since = self.memory.days_since_last_email_of_type(patient.patient_id, email_type, self.as_of)
        if days_since is not None and days_since < config.DOCTOR_EMAIL_FOLLOWUP_COOLDOWN_DAYS:
            trace.observe(f"Already requested this {days_since} day(s) ago -- within the "
                           f"{config.DOCTOR_EMAIL_FOLLOWUP_COOLDOWN_DAYS}-day follow-up window, not re-sending.")
            return f"Already emailed the physician about this {days_since} day(s) ago; not re-sending yet."
        trace.act("draft_and_send_doctor_email", email_type)
        record = self.email_tool.send(to=to, subject=subject, body=body, patient_id=patient.patient_id, as_of=self.as_of)
        record["email_type"] = email_type
        self.memory.record_email(patient.patient_id, record)
        trace.observe(f"Email sent (simulated): {record['message_id']}")
        return None

    def evaluate(
        self,
        patient: Patient,
        compliance_snapshot=None,
        doctor_email: str = "referring.physician@example-clinic.test",
    ) -> DmeRecommendation:
        trace = ShortTermTrace()
        rationale: list[str] = []
        escalation_reasons: list[str] = []

        trace.think(f"Evaluating DME needs for {patient.name} ({patient.patient_id}).")

        # Check for any hard escalation flags up front.
        for flag in patient.clinical_flags:
            if flag in _ESCALATION_FLAGS:
                escalation_reasons.append(_ESCALATION_FLAGS[flag])
        if patient.is_synthetic:
            trace.observe("Synthetic test-fixture patient, not a real chart.")

        pap = patient.pap_status

        if pap is None or not pap.on_pap:
            trace.act("check_diagnostic_study", "patient not currently on PAP")
            diagnostic = patient.latest_diagnostic()
            if diagnostic is None:
                trace.observe("No diagnostic study on file -- insufficient data.")
                return DmeRecommendation(
                    patient_id=patient.patient_id, patient_name=patient.name,
                    action="no_action",
                    rationale=["No diagnostic sleep study on file; nothing actionable yet."],
                    requires_human_escalation=False,
                    reasoning_trace=trace.steps,
                )
            trace.observe(f"Diagnostic AHI = {diagnostic.ahi} ({diagnostic.severity}).")
            if diagnostic.ahi is not None and diagnostic.ahi >= config.CLINICAL.ahi_order_pap_threshold:
                rationale.append(
                    f"Diagnostic {diagnostic.type} ({diagnostic.date.isoformat()}) shows AHI "
                    f"{diagnostic.ahi} ({diagnostic.severity}), at/above the "
                    f"{config.CLINICAL.ahi_order_pap_threshold} threshold used to request initial "
                    f"PAP therapy."
                )
                if escalation_reasons:
                    trace.observe("Escalation flags present -- holding the auto-email, routing to a clinician.")
                    return DmeRecommendation(
                        patient_id=patient.patient_id, patient_name=patient.name,
                        action="order_initial_pap", rationale=rationale,
                        requires_human_escalation=True, escalation_reasons=escalation_reasons,
                        reasoning_trace=trace.steps,
                    )
                subject, body = _draft_initial_pap_email(patient, diagnostic)
                skip_note = self._send_order_email(patient, "order_initial_pap", doctor_email, subject, body, trace)
                if skip_note:
                    rationale.append(skip_note)
                return DmeRecommendation(
                    patient_id=patient.patient_id, patient_name=patient.name,
                    action="order_initial_pap", rationale=rationale,
                    requires_human_escalation=False, reasoning_trace=trace.steps,
                )
            trace.observe("AHI below PAP-ordering threshold; no action needed.")
            return DmeRecommendation(
                patient_id=patient.patient_id, patient_name=patient.name,
                action="no_action",
                rationale=[f"Diagnostic AHI {diagnostic.ahi} below action threshold; continue monitoring."],
                requires_human_escalation=False, reasoning_trace=trace.steps,
            )

        # Already on PAP: check whether current therapy is adequately controlled.
        # Prefer the compliance/AirView-reported AHI (reflects actual ongoing use)
        # over the titration-night AHI (a single night, possibly months stale) when
        # both are available -- a titration recommendation from months ago may
        # already have been implemented and working well since.
        trace.act("check_current_therapy_control", "patient already on PAP")
        titration = patient.latest_titration()
        pressure_label = (
            f"{titration.pressure_cmh2o}cmH2O" if titration and titration.pressure_cmh2o is not None
            else (pap.pressure or "current settings")
        )
        upgrade_reasons: list[str] = []

        current_ahi, ahi_source = None, None
        if compliance_snapshot is not None and compliance_snapshot.ahi is not None:
            current_ahi = compliance_snapshot.ahi
            ahi_source = f"current device-reported AHI ({compliance_snapshot.report_period_end.isoformat()} report)"
        elif titration and titration.ahi_on_pressure is not None:
            current_ahi = titration.ahi_on_pressure
            ahi_source = f"{titration.date.isoformat()} titration-night residual AHI"

        if current_ahi is not None:
            trace.observe(f"{ahi_source} = {current_ahi}.")
            if current_ahi >= config.CLINICAL.residual_ahi_uncontrolled_threshold:
                upgrade_reasons.append(
                    f"Residual AHI {current_ahi} on {pressure_label} ({ahi_source}) is at/above the "
                    f"{config.CLINICAL.residual_ahi_uncontrolled_threshold} 'adequately controlled' threshold."
                )

        if "equipment_end_of_life_replacement_needed" in patient.clinical_flags:
            upgrade_reasons.append("Device flagged as end-of-life / non-functional and due for replacement.")
        if "titration_recommendation_not_yet_implemented" in patient.clinical_flags:
            upgrade_reasons.append("Titration study recommended a settings/device change that the current order does not yet reflect.")

        supply_only_reasons: list[str] = []
        if "elevated_mask_leak" in patient.clinical_flags or (
            titration is None and "mask_comfort_issue" in patient.clinical_flags
        ):
            supply_only_reasons.append("Elevated mask leak / comfort complaint -- likely a mask refit or resupply, not a new order.")
        if "mask_comfort_issue" in patient.clinical_flags and not upgrade_reasons:
            supply_only_reasons.append("Mask comfort issue reported -- candidate for a mask/cushion resupply.")

        if pap.device_age_years and pap.device_age_years >= config.CLINICAL.device_replacement_age_years and \
                "equipment_end_of_life_replacement_needed" not in patient.clinical_flags:
            supply_only_reasons.append(
                f"Device is {pap.device_age_years} years old, at/beyond the "
                f"{config.CLINICAL.device_replacement_age_years}-year replacement benchmark -- worth a "
                f"proactive replacement-eligibility check."
            )

        if upgrade_reasons:
            rationale.extend(upgrade_reasons)
            if escalation_reasons:
                trace.observe("Escalation flags present -- holding the auto-email, routing to a clinician.")
                return DmeRecommendation(
                    patient_id=patient.patient_id, patient_name=patient.name,
                    action="equipment_upgrade", rationale=rationale,
                    requires_human_escalation=True, escalation_reasons=escalation_reasons,
                    reasoning_trace=trace.steps,
                )
            subject, body = _draft_equipment_change_email(patient, upgrade_reasons, titration)
            skip_note = self._send_order_email(patient, "equipment_upgrade", doctor_email, subject, body, trace)
            if skip_note:
                rationale.append(skip_note)
            return DmeRecommendation(
                patient_id=patient.patient_id, patient_name=patient.name,
                action="equipment_upgrade", rationale=rationale,
                requires_human_escalation=False, reasoning_trace=trace.steps,
            )

        if escalation_reasons:
            trace.observe("Escalation flags present with no other actionable finding -- logging for clinician review.")
            return DmeRecommendation(
                patient_id=patient.patient_id, patient_name=patient.name,
                action="no_action", rationale=["Well-controlled on current settings; flagged only for the items below."],
                requires_human_escalation=True, escalation_reasons=escalation_reasons,
                reasoning_trace=trace.steps,
            )

        if supply_only_reasons:
            trace.observe("Routine supply/resupply need identified -- no physician order required, logging as internal action item.")
            return DmeRecommendation(
                patient_id=patient.patient_id, patient_name=patient.name,
                action="supply_resupply", rationale=supply_only_reasons,
                requires_human_escalation=False, reasoning_trace=trace.steps,
            )

        trace.observe("Therapy well-controlled, no equipment or supply action needed.")
        return DmeRecommendation(
            patient_id=patient.patient_id, patient_name=patient.name,
            action="no_action", rationale=["Therapy well-controlled on current settings."],
            requires_human_escalation=False, reasoning_trace=trace.steps,
        )
