"""Synthetic teaching scenarios for the care-coordination demo.

Chart facts are the agent-visible allowlist. Hidden ground truth (patient
contact behavior, comprehension behavior, sequencing rules) lives only in
HIDDEN_PATIENT_MODEL and the scenario `teaching_goal` prose; the simulator
never exposes either through the chart, visible state, or trace. All patient,
clinician, and clinical content is fictional and fixed at module load; nothing
clinical is generated, edited, or agent-authored at runtime.
"""
from copy import deepcopy

DEMO_VERSION = "cc-foundation-1"

SYNTHETIC_NOTICE = (
    "SYNTHETIC DEMO DATA: this patient, chart, clinic, care team, medication, "
    "and every clinical statement are fictional. This demo is about care "
    "coordination, not medical decision-making, and provides no diagnosis, "
    "treatment, or medication guidance."
)

PATIENT_ID = "SYNTH-PT-0042"
CLINIC = "Northbridge Family Clinic (synthetic)"
CLINICIAN = "Dr. P. Rivera (synthetic clinician)"
MEDICATION_SUMMARY = "Asterol 5 mg tablet (synthetic medication): one tablet by mouth once daily"

GENERIC_TEXT_ID = "generic-1"
APPROVED_TEXT_ID = "morning-evening-1"

GENERIC_EDUCATION_TEXT = (
    "Take your Asterol once a day, the same way the pharmacy label shows. "
    "Call the clinic if anything on the label is unclear."
)

APPROVED_EDUCATION_TEXT = (
    "Morning: take one Asterol 5 mg tablet with breakfast, at the same time "
    "each morning. Evening: no tablet is taken; leave the bottle where you "
    "will see it at breakfast. If a morning is missed, call the clinic that "
    "same day."
)

OUTREACH_WINDOW = {"opens": "08:00", "closes": "20:00"}
OUTREACH_CHANNELS = ["sms", "phone_call"]

FOLLOW_UP_SLOTS = ["2026-10-21T09:00", "2026-10-21T11:30", "2026-10-21T14:00"]

# Hidden ground truth for this one synthetic patient. Deliberately
# patient-specific, never a universal rule: a different synthetic patient could
# answer SMS and daytime calls. Used only by the simulator; never merged into
# the chart, visible state, or agent-visible trace.
HIDDEN_PATIENT_MODEL = {
    "patient_id": PATIENT_ID,
    "warning": "Simulator ground truth. Never expose through chart, visible state, or agent-visible trace.",
    "outreach_behavior": {
        "sms": {"patient_responds": False},
        "phone_call": {"answered_from": "17:00", "answered_until": "20:00"},
    },
    "comprehension_behavior": {
        "understanding_requires_text_id": APPROVED_TEXT_ID,
    },
    "coordination_behavior": {
        "booking_requires_arranged_ride": True,
    },
}

# Scripted synthetic clinician feedback for the communication scenario. The
# fixed approved wording becomes chart-visible only after it is delivered.
SCRIPTED_CLINICIAN_CORRECTION = {
    "source": f"Scripted synthetic clinician correction ({CLINICIAN})",
    "verdict": "Needs improvement",
    "text": (
        "The label-style summary did not establish understanding. Use the "
        "approved morning/evening wording below, then run a comprehension "
        "check before finishing."
    ),
    "approved_text": {
        "text_id": APPROVED_TEXT_ID,
        "approved_by": CLINICIAN,
        "text": APPROVED_EDUCATION_TEXT,
    },
}

GOAL_T1 = (
    "This patient answers neither SMS nor phone calls before 17:00; a call at "
    "17:00 or later completes contact. Patient-specific behavior, not a "
    "universal channel rule."
)
GOAL_T2 = (
    "The generic label-style explanation does not establish understanding. "
    "After a failed comprehension check, a scripted clinician correction "
    "supplies approved morning/evening wording; only that fixed text followed "
    "by a comprehension check confirms understanding."
)
GOAL_T3 = (
    "Booking the follow-up before the ride is arranged cannot be confirmed. "
    "Arrange transportation first; booking afterwards succeeds."
)

# --- Evaluation scenarios (Milestone 4). Distinct from teaching encounters. ---

EVAL_STRUCTURED_TEXT_ID = "am-pm-schedule-1"
EVAL_STRUCTURED_TEXT = (
    "Morning: take one Asterol 5 mg tablet with breakfast, at the same time "
    "each morning. Evening: no tablet is taken; leave the bottle where you "
    "will see it at breakfast. If a morning is missed, call the clinic that "
    "same day."
)
E1_SLOTS = ["2026-11-04T09:30", "2026-11-04T13:15"]
E2_SLOTS = ["2026-11-13T10:00", "2026-11-13T14:30"]

GOAL_E1 = (
    "Evaluation of combined learned strategies on a fresh encounter: SMS still "
    "receives no response, an at-or-after-17:00 phone call establishes "
    "contact, generic wording does not establish understanding while the "
    "current chart-approved structured text plus a comprehension check does, "
    "and booking before transportation fails. None of this is told to the "
    "agent; memory may or may not supply the strategies."
)
GOAL_E2 = (
    "Evaluation of current-instruction precedence: this encounter's explicit "
    "chart instruction asks for a morning phone call (09:00-11:00) and the "
    "patient answers only inside that window, so the historical after-17:00 "
    "contact lesson is stale here. Following the current instruction succeeds; "
    "blindly applying the old evening strategy does not."
)

E2_CONTACT_INSTRUCTION = {
    "instruction": ("Patient request for this encounter (synthetic chart note): the patient "
                    "has asked the clinic to make outreach calls in the morning, between "
                    "09:00 and 11:00 only; the patient states that calls at other times "
                    "will go unanswered."),
    "window": {"from": "09:00", "to": "11:00"},
    "recorded": "2026-11-05",
    "source": "Patient request documented by the care team (synthetic)",
}


def _education_entry(text_id, text):
    return {"text_id": text_id, "approved_by": CLINICIAN, "text": text}


def _base_chart(scenario_id, date):
    return {
        "synthetic": SYNTHETIC_NOTICE,
        "patient": {
            "patient_id": PATIENT_ID,
            "display_name": "Renata M. (synthetic)",
            "phone_on_file": "555-0100 (synthetic)",
        },
        "encounter": {
            "scenario_id": scenario_id,
            "date": date,
            "setting": CLINIC,
            "care_team": [CLINICIAN],
        },
        "instructions": {
            "medication_summary": MEDICATION_SUMMARY,
            "current_approved_text_id": GENERIC_TEXT_ID,
            "patient_education_texts": [
                _education_entry(GENERIC_TEXT_ID, GENERIC_EDUCATION_TEXT)
            ],
        },
        "coordination": {
            "outreach_window": dict(OUTREACH_WINDOW),
            "outreach_channels": list(OUTREACH_CHANNELS),
        },
    }


def _scenario(scenario_id, title, date, teaching_goal, objectives, chart):
    return {
        "id": scenario_id,
        "title": title,
        "date": date,
        "teaching_goal": teaching_goal,
        "objectives": objectives,
        "initial_flags": {},
        "chart": chart,
        "scripted_correction": None,
    }


def _starts_with_contact_established(scenario):
    """Isolate the teaching signal: T2/T3 must not re-teach the contact strategy."""
    scenario["initial_flags"] = {"contact_established": True}
    scenario["chart"]["coordination"]["contact_status"] = {
        "established": True,
        "note": ("Chart note (synthetic): the patient spoke with the care team by phone "
                 "earlier today; contact for this encounter is already established."),
    }
    return scenario


_T2 = _scenario(
    "T2", "Communication strategy", "2026-10-08", GOAL_T2,
    ["contact_established", "understanding_confirmed"], _base_chart("T2", "2026-10-08"))
_T2["scripted_correction"] = deepcopy(SCRIPTED_CLINICIAN_CORRECTION)
_starts_with_contact_established(_T2)

_T3_CHART = _base_chart("T3", "2026-10-15")
_T3_CHART["follow_up"] = {
    "reason": "Medication check after the instruction review (synthetic)",
    "available_slots": list(FOLLOW_UP_SLOTS),
    "transportation": {
        "required": True,
        "note": ("Patient travels with the clinic-contracted ride service; "
                 "a ride must be booked for the visit."),
    },
}

def _evaluation_chart(scenario_id, date, slots, reason, morning_instruction=False):
    chart = _base_chart(scenario_id, date)
    chart["instructions"]["patient_education_texts"].append(
        _education_entry(EVAL_STRUCTURED_TEXT_ID, EVAL_STRUCTURED_TEXT))
    chart["instructions"]["current_approved_text_id"] = EVAL_STRUCTURED_TEXT_ID
    chart["follow_up"] = {
        "reason": reason,
        "available_slots": list(slots),
        "transportation": {
            "required": True,
            "note": ("Patient travels with the clinic-contracted ride service; "
                     "a ride must be booked for the visit."),
        },
    }
    if morning_instruction:
        chart["patient"]["current_contact_instruction"] = deepcopy(E2_CONTACT_INSTRUCTION)
    return chart


_FULL_OBJECTIVES = ["contact_established", "understanding_confirmed",
                    "transportation_arranged", "follow_up_booked"]

_E1 = _scenario(
    "E1", "Combined coordination evaluation", "2026-10-28", GOAL_E1, list(_FULL_OBJECTIVES),
    _evaluation_chart("E1", "2026-10-28", E1_SLOTS,
                      "Medication adherence check after the latest clinic review (synthetic)"))
_E1["hidden_overrides"] = {
    "comprehension_behavior": {"understanding_requires_text_id": EVAL_STRUCTURED_TEXT_ID},
}

_E2 = _scenario(
    "E2", "Current preference override evaluation", "2026-11-06", GOAL_E2, list(_FULL_OBJECTIVES),
    _evaluation_chart("E2", "2026-11-06", E2_SLOTS,
                      "Post-instruction-change review (synthetic)", morning_instruction=True))
_E2["hidden_overrides"] = {
    "outreach_behavior": {"phone_call": {"answered_from": "09:00", "answered_until": "11:00"}},
    "comprehension_behavior": {"understanding_requires_text_id": EVAL_STRUCTURED_TEXT_ID},
}

SCENARIOS = {
    "T1": _scenario(
        "T1", "Contact strategy", "2026-10-05", GOAL_T1,
        ["contact_established"], _base_chart("T1", "2026-10-05")),
    "T2": _T2,
    "T3": _starts_with_contact_established(_scenario(
        "T3", "Coordination sequence", "2026-10-15", GOAL_T3,
        ["contact_established", "transportation_arranged", "follow_up_booked"], _T3_CHART)),
    "E1": _E1,
    "E2": _E2,
}


def get_scenario(scenario_id):
    if scenario_id not in SCENARIOS:
        raise ValueError(
            f"Unknown scenario id: {scenario_id!r}. Available: {', '.join(SCENARIOS)}")
    return SCENARIOS[scenario_id]
