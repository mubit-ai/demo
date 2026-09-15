"""Deterministic encounter environment: validated actions, hidden patient behavior.

The simulator owns hidden ground truth and exposes only the synthetic chart,
the action catalog, and observed outcomes of the current encounter. Invalid
actions or arguments raise ActionError and change no state, budget, or chart.
Executed actions count against a fixed budget of ACTION_BUDGET; exhausting it
reports the encounter as incomplete, never successful. There is no persistent
memory in this milestone and no code path that writes lessons.
"""
import re
import uuid
from copy import deepcopy

from scenarios import (
    DEMO_VERSION, HIDDEN_PATIENT_MODEL, PATIENT_ID, SYNTHETIC_NOTICE, get_scenario)
from trace import Trace

ACTION_BUDGET = 8
FLAGS = ("contact_established", "understanding_confirmed",
         "transportation_arranged", "follow_up_booked")
CONTACT_REQUIRED_ACTIONS = ("explain_instructions", "check_understanding",
                            "arrange_transportation", "book_follow_up")

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EXPERIMENT_RE = re.compile(r"^cc-[A-Za-z0-9_-]{1,60}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

ACTION_SPECS = [
    {"name": "outreach",
     "description": "Attempt to reach the patient on one chart-listed channel inside the outreach window.",
     "params": {"channel": "one of chart.coordination.outreach_channels",
                "local_time": "HH:MM inside chart.coordination.outreach_window"}},
    {"name": "explain_instructions",
     "description": "Read one chart-approved patient-education text to the patient. Wording is fixed; no text can be supplied or altered.",
     "params": {"text_id": "text_id from chart.instructions.patient_education_texts"}},
    {"name": "check_understanding",
     "description": "Ask the patient to restate the instructions and record whether understanding is established.",
     "params": {}},
    {"name": "arrange_transportation",
     "description": "Book the clinic-contracted ride for the follow-up visit (only encounters whose chart lists a transportation requirement).",
     "params": {}},
    {"name": "book_follow_up",
     "description": "Book one of the chart's available follow-up slots.",
     "params": {"slot": "ISO datetime from chart.follow_up.available_slots"}},
    {"name": "close_encounter",
     "description": "Close the encounter once every required objective is met.",
     "params": {}},
    {"name": "escalate",
     "description": "Hand off to the on-call coordinator when the objectives cannot be met.",
     "params": {}},
]
_PARAM_KEYS = {spec["name"]: set(spec["params"]) for spec in ACTION_SPECS}


class ActionError(ValueError):
    """An invalid action or argument. State, chart, and budget are unchanged."""


def new_experiment_id():
    return "cc-" + uuid.uuid4().hex[:12]


def _minutes(hhmm):
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _safe_action(action):
    return deepcopy(action) if isinstance(action, dict) else repr(action)


class Encounter:
    def __init__(self, scenario, experiment_id, execution_id=None, encounter_id=None,
                 extra_metadata=None, event_observer=None):
        self._scenario = deepcopy(scenario)
        self._chart = deepcopy(scenario["chart"])
        self._hidden = {
            "patient_model": deepcopy(HIDDEN_PATIENT_MODEL),
            "dynamic": {
                "correction_delivered": False,
                "approved_explanation_given": False,
            },
        }
        for section, values in (scenario.get("hidden_overrides") or {}).items():
            self._hidden["patient_model"].setdefault(section, {}).update(deepcopy(values))
        self._flags = {flag: False for flag in FLAGS}
        unknown_initial = set(scenario.get("initial_flags") or {}) - set(FLAGS)
        if unknown_initial:
            raise ValueError(f"Scenario declares unknown initial flags: {sorted(unknown_initial)}")
        for flag, value in (scenario.get("initial_flags") or {}).items():
            self._flags[flag] = bool(value)
        self._explained_texts = []
        self._action_history = []
        self._outcomes = []
        self._status = "open"
        self._completion_reason = None
        self.experiment_id = experiment_id
        self.execution_id = execution_id or uuid.uuid4().hex
        self.encounter_id = encounter_id or uuid.uuid4().hex
        unknown = [o for o in self._scenario["objectives"] if o not in FLAGS]
        if unknown:
            raise ValueError(f"Scenario declares unknown objectives: {unknown}")
        self._trace = Trace({
            "demo_version": DEMO_VERSION,
            "experiment_id": self.experiment_id,
            "execution_id": self.execution_id,
            "encounter_id": self.encounter_id,
            "patient_id": PATIENT_ID,
            "scenario_id": self._scenario["id"],
            "scenario_title": self._scenario["title"],
            "action_budget": ACTION_BUDGET,
            "synthetic": SYNTHETIC_NOTICE,
            **deepcopy(extra_metadata or {}),
        }, observer=event_observer)
        self._trace.emit("chart_snapshot", chart=self._chart)
        if scenario.get("initial_flags"):
            self._trace.emit("initial_state", initial_flags=dict(scenario["initial_flags"]),
                             note="Visible starting conditions from the synthetic chart")

    def chart(self):
        return deepcopy(self._chart)

    def available_actions(self):
        return deepcopy(ACTION_SPECS)

    def visible_state(self):
        return {
            "experiment_id": self.experiment_id,
            "execution_id": self.execution_id,
            "encounter_id": self.encounter_id,
            "patient_id": PATIENT_ID,
            "scenario_id": self._scenario["id"],
            "status": self._status,
            "completion_reason": self._completion_reason,
            "action_budget": ACTION_BUDGET,
            "actions_used": len(self._action_history),
            "actions_remaining": max(0, ACTION_BUDGET - len(self._action_history)),
            "objectives": [{"objective": o, "met": self._flags[o]}
                           for o in self._scenario["objectives"]],
            "action_history": deepcopy(self._action_history),
            "observed_outcomes": deepcopy(self._outcomes),
        }

    def trace(self):
        return self._trace.to_dict()

    def record_event(self, kind, **data):
        """Append an audit event from the agent loop. Never carries hidden state."""
        self._trace.emit(kind, **data)

    def debug_hidden_state(self):
        """TEST/DEBUG ONLY ground truth.

        Deliberately outside the agent-facing surface (chart, visible_state,
        available_actions, act) and never used by prompt construction; it exists
        so offline tests can assert what the agent must not know. Never
        serialize into prompts, traces, or any model-visible record.
        """
        return deepcopy(self._hidden)

    def act(self, action):
        self._trace.emit("action_requested", action=_safe_action(action))
        if self._status != "open":
            self._reject(action, f"Encounter is already terminal ({self._status})")
        if len(self._action_history) >= ACTION_BUDGET:
            self._budget_exhausted()
            self._reject(action, "Action budget exhausted; the encounter is reported as incomplete")
        validated = self._validate(action)
        self._trace.emit("action_validated", action=deepcopy(validated))
        handler = getattr(self, "_execute_" + validated["name"])
        status, observation, chart_change, flag_changes = handler(validated)
        self._action_history.append(deepcopy(validated))
        outcome = {"action": deepcopy(validated), "status": status, "observation": observation}
        if chart_change:
            outcome["chart_change"] = chart_change
        self._outcomes.append(deepcopy(outcome))
        self._trace.emit("outcome", **deepcopy(outcome))
        if flag_changes:
            before = {flag: self._flags[flag] for flag in flag_changes}
            for flag, value in flag_changes.items():
                self._flags[flag] = value
            self._trace.emit("state_transition",
                             changed={flag: {"before": before[flag], "after": value}
                                      for flag, value in flag_changes.items()})
        if validated["name"] == "close_encounter":
            self._terminalize("completed",
                              "All required objectives met: " + ", ".join(self._scenario["objectives"]),
                              "encounter_completed")
        elif validated["name"] == "escalate":
            unmet = self._unmet()
            reason = "Handed off to the on-call care coordinator"
            if unmet:
                reason += "; unmet objectives: " + ", ".join(unmet)
            self._terminalize("escalated", reason, "encounter_escalated")
        return deepcopy(outcome)

    def finalize(self):
        """Report an open encounter as incomplete rather than leaving it ambiguous."""
        if self._status != "open":
            return self.visible_state()
        unmet = self._unmet()
        if len(self._action_history) >= ACTION_BUDGET:
            reason = (f"Action budget of {ACTION_BUDGET} exhausted with unmet objectives: " + ", ".join(unmet)
                      if unmet else
                      f"Action budget of {ACTION_BUDGET} exhausted before close_encounter")
        else:
            reason = ("Encounter ended without close_encounter; unmet objectives: " + ", ".join(unmet)
                      if unmet else
                      "Encounter ended without close_encounter")
        self._terminalize("incomplete", reason, "encounter_incomplete")
        return self.visible_state()

    def _unmet(self):
        return [o for o in self._scenario["objectives"] if not self._flags[o]]

    def _reject(self, action, reason):
        self._trace.emit("action_rejected", action=_safe_action(action), reason=reason)
        raise ActionError(reason)

    def _budget_exhausted(self):
        if self._status != "open":
            return
        unmet = self._unmet()
        reason = (f"Action budget of {ACTION_BUDGET} exhausted with unmet objectives: " + ", ".join(unmet)
                  if unmet else
                  f"Action budget of {ACTION_BUDGET} exhausted before close_encounter")
        self._terminalize("incomplete", reason, "encounter_incomplete")

    def _terminalize(self, status, reason, event_type):
        self._status = status
        self._completion_reason = reason
        self._trace.emit(event_type, status=status, reason=reason,
                         objectives=[{"objective": o, "met": self._flags[o]}
                                     for o in self._scenario["objectives"]])

    def _validate(self, action):
        if not isinstance(action, dict):
            self._reject(action, "Action must be an object with a 'name' field")
        name = action.get("name")
        if not isinstance(name, str) or name not in _PARAM_KEYS:
            self._reject(action, "Unsupported action: " + repr(name)
                         + ". Supported: " + ", ".join(_PARAM_KEYS))
        extra = sorted(set(action) - {"name"} - _PARAM_KEYS[name])
        if extra:
            self._reject(action, f"Unknown argument(s) for {name}: {extra}")
        missing = sorted(_PARAM_KEYS[name] - set(action))
        if missing:
            self._reject(action, f"Missing required argument(s) for {name}: {missing}")
        for key in _PARAM_KEYS[name]:
            if not isinstance(action[key], str):
                self._reject(action, f"Argument {key!r} for {name} must be a string")
        if name == "outreach":
            self._validate_outreach(action)
        elif name == "explain_instructions":
            self._validate_explain(action)
        elif name == "arrange_transportation":
            if not self._chart.get("follow_up", {}).get("transportation", {}).get("required"):
                self._reject(action, "This encounter's chart has no follow-up transportation requirement")
            if self._flags["transportation_arranged"]:
                self._reject(action, "Transportation is already arranged for this encounter")
        elif name == "book_follow_up":
            slots = self._chart.get("follow_up", {}).get("available_slots")
            if not slots:
                self._reject(action, "This encounter's chart has no bookable follow-up slots")
            if action["slot"] not in slots:
                self._reject(action, f"Invalid follow-up slot: {action['slot']!r}. "
                                     f"Available slots are listed in chart.follow_up")
            if self._flags["follow_up_booked"]:
                self._reject(action, "A follow-up appointment is already booked for this encounter")
        if name in CONTACT_REQUIRED_ACTIONS and not self._flags["contact_established"]:
            self._reject(action, "Patient contact has not been established in this encounter")
        if name == "close_encounter" and self._unmet():
            self._reject(action, "Encounter objectives not met: " + ", ".join(self._unmet()))
        return {key: action[key] for key in ["name"] + sorted(_PARAM_KEYS[name])}

    def _validate_outreach(self, action):
        channels = self._chart["coordination"]["outreach_channels"]
        if action["channel"] not in channels:
            self._reject(action, f"Invalid outreach channel: {action['channel']!r}. "
                                 f"Channels listed in the chart: {channels}")
        if not TIME_RE.match(action["local_time"]):
            self._reject(action, f"Invalid local_time: {action['local_time']!r}. "
                                 "Expected HH:MM (24-hour, zero-padded)")
        window = self._chart["coordination"]["outreach_window"]
        if not _minutes(window["opens"]) <= _minutes(action["local_time"]) <= _minutes(window["closes"]):
            self._reject(action, f"local_time {action['local_time']} is outside the outreach window "
                                 f"{window['opens']}-{window['closes']}")

    def _validate_explain(self, action):
        approved = [t["text_id"] for t in self._chart["instructions"]["patient_education_texts"]]
        if action["text_id"] not in approved:
            self._reject(action, f"Unknown or not-yet-approved patient-education text_id: "
                                 f"{action['text_id']!r}. Education wording cannot be supplied or altered")

    def _execute_outreach(self, action):
        hidden = self._hidden["patient_model"]["outreach_behavior"][action["channel"]]
        if action["channel"] == "sms":
            if hidden["patient_responds"]:
                return ("success", "The patient replied to the SMS; contact established.", None,
                        {"contact_established": True})
            return "no_response", "SMS delivered to the number on file; no reply was received.", None, {}
        call = _minutes(action["local_time"])
        if _minutes(hidden["answered_from"]) <= call <= _minutes(hidden["answered_until"]):
            return ("success", "The patient answered the call; contact established.", None,
                    {"contact_established": True})
        return ("no_response",
                "The call rang through to voicemail; contact was not completed.", None, {})

    def _execute_explain_instructions(self, action):
        self._explained_texts.append(action["text_id"])
        required = self._hidden["patient_model"]["comprehension_behavior"]["understanding_requires_text_id"]
        if action["text_id"] == required:
            self._hidden["dynamic"]["approved_explanation_given"] = True
        return "success", f"Read the approved education text '{action['text_id']}' to the patient.", None, {}

    def _execute_check_understanding(self, action):
        dynamic = self._hidden["dynamic"]
        if not self._explained_texts:
            return ("not_confirmed",
                    "No education text has been explained during this encounter; there was nothing to confirm.",
                    None, {})
        if dynamic["approved_explanation_given"]:
            return ("confirmed",
                    "The patient restated the morning and evening plan in their own words; "
                    "understanding is established.", None, {"understanding_confirmed": True})
        chart_change = None
        correction = self._scenario["scripted_correction"]
        if correction and not dynamic["correction_delivered"]:
            dynamic["correction_delivered"] = True
            entry = deepcopy(correction["approved_text"])
            self._chart["instructions"]["patient_education_texts"].append(entry)
            self._chart["instructions"]["current_approved_text_id"] = entry["text_id"]
            chart_change = {"added_patient_education_text_id": entry["text_id"],
                            "current_approved_text_id": entry["text_id"]}
            self._trace.emit("clinician_correction", source=correction["source"],
                             verdict=correction["verdict"], text=correction["text"],
                             approved_text=deepcopy(entry),
                             note="Scripted synthetic feedback; fixed approved wording, not agent-generated")
        return ("not_confirmed",
                "The patient could not restate the instructions; understanding was not established.",
                chart_change, {})

    def _execute_arrange_transportation(self, action):        return ("success",
                "Round-trip ride booked with the clinic-contracted ride service for the follow-up "
                "visit; the patient accepted.", None, {"transportation_arranged": True})

    def _execute_book_follow_up(self, action):
        requires_ride = self._hidden["patient_model"]["coordination_behavior"]["booking_requires_arranged_ride"]
        if requires_ride and not self._flags["transportation_arranged"]:
            return ("unsuccessful",
                    "The appointment slot could not be confirmed: the patient's ride for the "
                    "visit has not been arranged.", None, {})
        return ("success", f"Follow-up appointment booked for {action['slot']}; the ride is already arranged.",
                None, {"follow_up_booked": True})

    def _execute_close_encounter(self, action):
        return "success", "Encounter closed; all required coordination objectives are met.", None, {}

    def _execute_escalate(self, action):
        return "success", "Encounter handed off to the on-call care coordinator.", None, {}


def start_encounter(scenario_id, experiment_id=None, execution_id=None, encounter_id=None,
                    extra_metadata=None, event_observer=None):
    """Begin a fresh, deterministic encounter. Scenario state is deep-copied per run."""
    experiment_id = experiment_id or new_experiment_id()
    if not isinstance(experiment_id, str) or not EXPERIMENT_RE.match(experiment_id):
        raise ValueError("experiment_id must match " + EXPERIMENT_RE.pattern)
    for label, value in (("execution_id", execution_id), ("encounter_id", encounter_id)):
        if value is not None and (not isinstance(value, str) or not ID_RE.match(value)):
            raise ValueError(f"{label} must match {ID_RE.pattern}")
    return Encounter(get_scenario(scenario_id), experiment_id, execution_id, encounter_id,
                     extra_metadata, event_observer)
