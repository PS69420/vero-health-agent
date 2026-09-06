"""
Compliance (adherence) Agent.

For each patient with an existing PAP device, checks their usage against the
Medicare 4-hours/70%-of-nights rule (see knowledge_base.MEDICARE_COMPLIANCE_RULE)
and decides what to do, ReAct-style (Thought -> Action -> Observation, logged
into a trace for audit):

  - compliant                          -> log it, no outreach needed
  - non-compliant, still early         -> nothing yet, too soon to call
  - non-compliant, ~2 weeks before the -> place an early check-in call
    30-day mark
  - non-compliant, approaching the     -> place an urgent check-in call
    90-day insurance cutoff
  - non-compliant, past the 90-day     -> this is now ongoing maintenance
    initial window (long-term patient)    coaching, not an urgent insurance
                                           deadline; handled with a lower-
                                           urgency outreach
  - a clinical red flag or vulnerable-  -> escalate to a human instead of
    patient flag is present                auto-calling
  - patient hasn't consented to AI      -> queue a callback for a human
    contact (patient.ai_contact_consent)   staff member instead of placing
                                            an automated Vapi call
  - we already contacted this patient   -> don't nag, log why we're skipping
    recently (memory check, either channel)

Outreach calls go through the mock VoiceCallTool (agent/tools/voice_tool.py)
when the patient has consented to AI contact, or the mock HumanTaskQueueTool
(agent/tools/human_queue_tool.py) when they haven't. Either way the outcome is
written to long-term memory so future runs know the outreach history for that
patient regardless of which channel reached them.
"""

from __future__ import annotations

from datetime import date

from agent import config
from agent.knowledge_base import COMPLIANCE_ESCALATION_FLAGS as _ESCALATION_FLAGS
from agent.memory import JsonEpisodicMemory, ShortTermTrace
from agent.models import ComplianceDecision, ComplianceSnapshot, Patient
from agent.tools.base import HumanTaskQueueTool, VoiceCallTool


class ComplianceAgent:
    def __init__(self, voice_tool: VoiceCallTool, human_queue_tool: HumanTaskQueueTool,
                 memory: JsonEpisodicMemory, as_of: date):
        self.voice_tool = voice_tool
        self.human_queue_tool = human_queue_tool
        self.memory = memory
        self.as_of = as_of

    def evaluate(self, patient: Patient, snapshot: ComplianceSnapshot | None) -> ComplianceDecision:
        trace = ShortTermTrace()
        rationale: list[str] = []
        rules = config.COMPLIANCE
        trace.think(f"Evaluating compliance for {patient.name} ({patient.patient_id}) as of {self.as_of.isoformat()}.")

        if snapshot is None:
            trace.observe("No compliance/usage data on file for this patient.")
            return ComplianceDecision(
                patient_id=patient.patient_id, patient_name=patient.name,
                day_of_therapy=-1, within_90_day_window=False, compliant=False,
                pct_nights_ge_4hr=0.0, action="none",
                rationale=["No compliance data available yet (device may not be reporting)."],
                reasoning_trace=trace.steps,
            )

        day_of_therapy = (self.as_of - snapshot.therapy_start_date).days
        within_90 = 0 <= day_of_therapy <= rules.insurance_review_window_days
        trace.observe(f"Day {day_of_therapy} of therapy (start {snapshot.therapy_start_date.isoformat()}).")

        data_stale_days = (self.as_of - snapshot.report_period_end).days
        if data_stale_days > 14:
            rationale.append(
                f"Latest compliance report is {data_stale_days} days old (through "
                f"{snapshot.report_period_end.isoformat()}) -- usage since then is unknown."
            )

        if snapshot.days_in_period < rules.compliance_window_days:
            trace.observe(f"Only {snapshot.days_in_period} days of data available -- too short a window to judge the 30-day rule.")
            rationale.append(
                f"Only {snapshot.days_in_period} day(s) of usage data on file; need "
                f"{rules.compliance_window_days} consecutive days to evaluate the 30-day compliance rule."
            )
            return ComplianceDecision(
                patient_id=patient.patient_id, patient_name=patient.name,
                day_of_therapy=day_of_therapy, within_90_day_window=within_90, compliant=False,
                pct_nights_ge_4hr=snapshot.pct_nights_ge_4hr, action="none",
                rationale=rationale, reasoning_trace=trace.steps,
            )

        pct = snapshot.pct_nights_ge_4hr
        compliant = pct >= rules.required_pct_nights
        trace.observe(f"{snapshot.days_used_ge_4hr}/{snapshot.days_in_period} nights >= 4hr = {pct}% (need {rules.required_pct_nights}%).")

        if compliant:
            rationale.append(f"Meets Medicare compliance: {pct}% of nights >= 4 hours (>= {rules.required_pct_nights}% required).")
            return ComplianceDecision(
                patient_id=patient.patient_id, patient_name=patient.name,
                day_of_therapy=day_of_therapy, within_90_day_window=within_90, compliant=True,
                pct_nights_ge_4hr=pct, action="log_compliant", rationale=rationale,
                reasoning_trace=trace.steps,
            )

        rationale.append(f"Below Medicare compliance: {pct}% of nights >= 4 hours (< {rules.required_pct_nights}% required).")

        # Clinical/vulnerable-patient escalation takes priority over any auto-call.
        escalation_reasons = [_ESCALATION_FLAGS[f] for f in patient.clinical_flags if f in _ESCALATION_FLAGS]
        if escalation_reasons:
            trace.observe("Escalation flag(s) present -- routing to a human instead of auto-calling.")
            return ComplianceDecision(
                patient_id=patient.patient_id, patient_name=patient.name,
                day_of_therapy=day_of_therapy, within_90_day_window=within_90, compliant=False,
                pct_nights_ge_4hr=pct, action="escalate_human", rationale=rationale,
                requires_human_escalation=True, escalation_reasons=escalation_reasons,
                reasoning_trace=trace.steps,
            )

        days_since_outreach = self.memory.days_since_last_outreach(patient.patient_id, self.as_of)
        if days_since_outreach is not None and days_since_outreach < rules.min_days_between_calls:
            trace.observe(f"Already contacted {days_since_outreach} day(s) ago -- within the {rules.min_days_between_calls}-day cooldown, skipping.")
            rationale.append(f"Outreach already placed {days_since_outreach} day(s) ago; holding off to avoid over-contacting.")
            return ComplianceDecision(
                patient_id=patient.patient_id, patient_name=patient.name,
                day_of_therapy=day_of_therapy, within_90_day_window=within_90, compliant=False,
                pct_nights_ge_4hr=pct, action="none", rationale=rationale, reasoning_trace=trace.steps,
            )

        prior_outreach = self.memory.all_outreach_for(patient.patient_id)

        def _place_outreach_call(purpose: str, action_label: str) -> ComplianceDecision:
            if not patient.ai_contact_consent:
                trace.act("queue_human_call", purpose)
                task_record = self.human_queue_tool.queue_call(
                    patient, call_purpose=purpose,
                    context={"snapshot": snapshot, "day_of_therapy": day_of_therapy, "as_of": self.as_of},
                )
                self.memory.record_human_task(patient.patient_id, task_record)
                trace.observe("Patient has not consented to AI contact -- queued for a human caller instead.")
                rationale.append(
                    f"Outreach needed ({purpose}), but patient has not consented to AI-based contact -- "
                    f"queued for a human staff member to call instead of placing a Vapi call."
                )
                return ComplianceDecision(
                    patient_id=patient.patient_id, patient_name=patient.name,
                    day_of_therapy=day_of_therapy, within_90_day_window=within_90, compliant=False,
                    pct_nights_ge_4hr=pct, action=action_label, rationale=rationale,
                    outreach_channel="human", reasoning_trace=trace.steps,
                )

            trace.act("place_compliance_call", purpose)
            call_record = self.voice_tool.place_call(
                patient, call_purpose=purpose,
                context={"snapshot": snapshot, "prior_calls": prior_outreach, "day_of_therapy": day_of_therapy, "as_of": self.as_of},
            )
            self.memory.record_call(patient.patient_id, call_record)
            outcome = call_record.get("outcome_tag")
            trace.observe(f"Call outcome: {outcome}.")
            rationale.append(f"Placed outreach call ({purpose}); outcome: {outcome}.")
            escalate = bool(call_record.get("escalate_to_rt"))
            esc_reasons = ["Patient call indicated ongoing difficulty -- referred to a Respiratory Therapist for direct follow-up."] if escalate else []
            return ComplianceDecision(
                patient_id=patient.patient_id, patient_name=patient.name,
                day_of_therapy=day_of_therapy, within_90_day_window=within_90, compliant=False,
                pct_nights_ge_4hr=pct, action=action_label, rationale=rationale,
                requires_human_escalation=escalate, escalation_reasons=esc_reasons,
                outreach_channel="ai", reasoning_trace=trace.steps,
            )

        if within_90:
            early_lo = rules.first_outreach_day - rules.first_outreach_window_days
            early_hi = rules.first_outreach_day + rules.first_outreach_window_days
            final_lo = rules.final_outreach_day - rules.final_outreach_window_days

            if early_lo <= day_of_therapy <= early_hi and not prior_outreach:
                return _place_outreach_call("early_compliance_checkin", "schedule_early_outreach")

            if day_of_therapy >= final_lo:
                return _place_outreach_call("urgent_pre_90_day_checkin", "schedule_urgent_outreach")

            if day_of_therapy < early_lo:
                rationale.append(f"Too early for outreach yet; first check-in call planned around day {rules.first_outreach_day}.")
            else:
                rationale.append(f"Between check-in windows; next planned outreach around day {rules.final_outreach_day} if still non-compliant.")

            return ComplianceDecision(
                patient_id=patient.patient_id, patient_name=patient.name,
                day_of_therapy=day_of_therapy, within_90_day_window=within_90, compliant=False,
                pct_nights_ge_4hr=pct, action="none", rationale=rationale, reasoning_trace=trace.steps,
            )

        if day_of_therapy > rules.insurance_review_window_days:
            trace.observe("Past the initial 90-day window -- treating as ongoing maintenance adherence coaching, not an urgent insurance deadline.")
            rationale.append("Beyond the initial 90-day compliance-proving window; this is a long-term adherence concern rather than an imminent coverage cutoff.")
            return _place_outreach_call("maintenance_adherence_checkin", "schedule_maintenance_outreach")

        # day_of_therapy < 0: therapy_start_date is in the future relative to as_of -- data issue.
        trace.observe("Therapy start date is after the evaluation date -- likely a data issue.")
        return ComplianceDecision(
            patient_id=patient.patient_id, patient_name=patient.name,
            day_of_therapy=day_of_therapy, within_90_day_window=False, compliant=False,
            pct_nights_ge_4hr=pct, action="escalate_human",
            rationale=["Therapy start date is after the evaluation date -- check chart data before acting."],
            requires_human_escalation=True,
            escalation_reasons=["Data issue: therapy_start_date is in the future relative to the evaluation date."],
            reasoning_trace=trace.steps,
        )
