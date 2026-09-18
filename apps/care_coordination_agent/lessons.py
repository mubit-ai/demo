"""Deterministic lesson derivation from observed encounter evidence.

Lesson text comes only from fixed templates parameterized by outcomes the
agent was actually allowed to observe. The runtime model never authors
lesson content. A candidate is considered valid only if it is identical to
one freshly derived from the finished encounter's visible state, so injected
or speculative lessons cannot reach Mubit through this path.
"""
import json

from memory import CATEGORIES, EVIDENCE_TYPES, Lesson
from scenarios import APPROVED_TEXT_ID, PATIENT_ID

TREATMENT_BANNED = ("mg", "dose", "dosage", "prescri", "discontinue", "stop taking",
                    "start taking", "medication change", "treatment change", "diagnos")
HIDDEN_MARKERS = ("answered_from", "patient_responds", "understanding_requires",
                  "booking_requires_arranged_ride", "patient_model", "HIDDEN_PATIENT_MODEL",
                  "teaching_goal", "Simulator ground truth")


def _actions(outcomes, name):
    return [(index, outcome) for index, outcome in enumerate(outcomes)
            if outcome["action"]["name"] == name]


def _contact_lesson(outcomes):
    sms_failed = next(((i, o) for i, o in _actions(outcomes, "outreach")
                       if o["action"].get("channel") == "sms" and o["status"] == "no_response"), None)
    phone_ok = next(((i, o) for i, o in _actions(outcomes, "outreach")
                     if o["action"].get("channel") == "phone_call" and o["status"] == "success"), None)
    if not sms_failed or not phone_ok or phone_ok[0] < sms_failed[0]:
        return None
    sms_time, ok_time = sms_failed[1]["action"]["local_time"], phone_ok[1]["action"]["local_time"]
    return Lesson(
        category="contact_strategy", patient_id=PATIENT_ID,
        applicability=(f"Synthetic patient {PATIENT_ID}: choosing an outreach channel when no "
                       "newer contact preference applies."),
        guidance=(f"For this synthetic patient, when no newer contact preference is available, "
                  f"a phone call at {ok_time} previously established contact after an SMS attempt "
                  f"received no response; prefer a call around that previously successful time. "
                  f"This is conditional patient-specific guidance, not a general channel rule."),
        evidence_type="observed_outcome",
        evidence_summary=(f"Observed in one encounter: outreach sms at {sms_time} -> no_response; "
                          f"outreach phone_call at {ok_time} -> success (contact established)."))


def _communication_lesson(outcomes):
    corrected_id, correction_index = None, None
    for index, outcome in _actions(outcomes, "check_understanding"):
        if outcome["status"] == "not_confirmed":
            added = (outcome.get("chart_change") or {}).get("added_patient_education_text_id")
            if added:
                corrected_id, correction_index = added, index
                break
    if corrected_id is None:
        return None
    for j, explained in _actions(outcomes, "explain_instructions"):
        if j <= correction_index or explained["action"].get("text_id") != corrected_id:
            continue
        for k, checked in _actions(outcomes, "check_understanding"):
            if k > j and checked["status"] == "confirmed":
                return Lesson(
                    category="communication_strategy", patient_id=PATIENT_ID,
                    applicability=(f"Synthetic patient {PATIENT_ID}: explaining approved "
                                   "instructions when a misunderstanding is observed."),
                    guidance=(f"For this synthetic patient, the approved '{corrected_id}' "
                              f"explanation followed by a comprehension check previously resolved "
                              f"a misunderstanding; the initial generic explanation did not "
                              f"establish understanding. Use only chart-approved wording."),
                    evidence_type="clinician_correction",
                    evidence_summary=(f"Observed in one encounter: generic explanation -> "
                                      f"check not_confirmed; scripted clinician correction added "
                                      f"approved text '{corrected_id}'; explanation of "
                                      f"'{corrected_id}' -> check confirmed."))
    return None


def _coordination_lesson(outcomes):
    failed = next(((i, o) for i, o in _actions(outcomes, "book_follow_up")
                   if o["status"] == "unsuccessful"), None)
    if not failed:
        return None
    transport = next(((i, o) for i, o in _actions(outcomes, "arrange_transportation")
                      if o["status"] == "success" and i > failed[0]), None)
    if not transport:
        return None
    booked = next(((i, o) for i, o in _actions(outcomes, "book_follow_up")
                   if o["status"] == "success" and i > transport[0]), None)
    if not booked:
        return None
    slot = booked[1]["action"]["slot"]
    return Lesson(
        category="coordination_sequence", patient_id=PATIENT_ID,
        applicability=(f"Synthetic patient {PATIENT_ID}: booking a follow-up visit when the "
                       "chart lists a transportation requirement."),
        guidance=(f"For this synthetic patient, resolve transportation before booking the "
                  f"follow-up; a booking attempted before the ride was arranged previously "
                  f"could not be confirmed. Applies only when transportation is required."),
        evidence_type="observed_outcome",
        evidence_summary=(f"Observed in one encounter: book_follow_up {slot} -> unsuccessful "
                          f"(ride not arranged); arrange_transportation -> success; "
                          f"book_follow_up {slot} -> success."))


def derive_lesson_candidates(state, chart):
    """Derive supported lesson candidates from a finished encounter's visible state."""
    outcomes = state["observed_outcomes"]
    candidates = []
    for derive in (_contact_lesson, _communication_lesson, _coordination_lesson):
        lesson = derive(outcomes)
        if lesson is None:
            continue
        if lesson.category not in CATEGORIES or lesson.evidence_type not in EVIDENCE_TYPES:
            continue
        if lesson.category == "communication_strategy":
            approved = {t["text_id"] for t in chart["instructions"]["patient_education_texts"]}
            if APPROVED_TEXT_ID not in approved:
                continue
        candidates.append({
            "lesson": lesson.model_dump(),
            "experiment_id": state["experiment_id"],
            "patient_id": state["patient_id"],
            "source_encounter_id": state["encounter_id"],
            "source_execution_id": state["execution_id"],
            "evidence_summary": lesson.evidence_summary,
        })
    return candidates


def validate_candidate(candidate, derived):
    """Return None when the candidate is grounded; otherwise a rejection reason."""
    try:
        lesson = Lesson.model_validate(candidate["lesson"])
    except ValueError as exc:
        return f"lesson schema invalid: {exc}"
    if lesson.category not in CATEGORIES:
        return f"category not allowed: {lesson.category!r}"
    if lesson.evidence_type not in EVIDENCE_TYPES:
        return f"evidence type not allowed: {lesson.evidence_type!r}"
    if candidate.get("patient_id") != PATIENT_ID or lesson.patient_id != PATIENT_ID:
        return "patient id mismatch"
    for key in ("experiment_id", "source_encounter_id", "source_execution_id"):
        if not candidate.get(key):
            return f"missing provenance: {key}"
    for text in (lesson.applicability, lesson.guidance):
        lowered = text.lower()
        for banned in TREATMENT_BANNED:
            if banned in lowered:
                return f"lesson text contains treatment-like language: {banned!r}"
    blob = json.dumps(candidate)
    for marker in HIDDEN_MARKERS:
        if marker in blob:
            return f"hidden simulator marker present in candidate: {marker!r}"
    grounded = any(candidate == original for original in derived)
    if not grounded:
        return "candidate is not identical to a freshly derived, evidence-backed lesson"
    return None
