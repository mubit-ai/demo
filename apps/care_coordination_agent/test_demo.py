"""Offline simulator and agent-loop checks. No real model APIs, no Mubit, no network.

debug_hidden_state() is used here exclusively: ground truth stays in tests and
never reaches the chart, visible state, agent prompts, or agent-visible trace.
The fake providers below are test-only doubles; the application never sees them.
"""
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from scenarios import (APPROVED_TEXT_ID, GENERIC_TEXT_ID, PATIENT_ID, SCENARIOS,
                       SCRIPTED_CLINICIAN_CORRECTION)
from simulator import ACTION_BUDGET, ActionError, new_experiment_id, start_encounter
from memory import (CATEGORIES, EVIDENCE_TYPES, MEMORY_VERSION, PROMPT_FIELDS, Memory,
                    MemoryError, Lesson, normalize_for_prompt, snapshot_hash)
from compare import (ARMS, ComparisonInvalid, build_arms, compare_scenario, print_report,
                     run_comparison, rubric, score_run, starting_state_hash,
                     starting_state_view, validate_evaluation_scenario, verdict, verdict_reason)
from lessons import HIDDEN_MARKERS, derive_lesson_candidates, validate_candidate
from agent import (MAX_STEP_ATTEMPTS, SYSTEM_POLICY, Decision, ProviderError,
                   StructuredOutputError, decision_fault, run_encounter)

SMS = {"name": "outreach", "channel": "sms", "local_time": "10:00"}
EARLY_CALL = {"name": "outreach", "channel": "phone_call", "local_time": "12:00"}
LATE_CALL = {"name": "outreach", "channel": "phone_call", "local_time": "18:00"}
CALL_1659 = {"name": "outreach", "channel": "phone_call", "local_time": "16:59"}
CALL_1700 = {"name": "outreach", "channel": "phone_call", "local_time": "17:00"}
CALL_2000 = {"name": "outreach", "channel": "phone_call", "local_time": "20:00"}
EXPLAIN_GENERIC = {"name": "explain_instructions", "text_id": GENERIC_TEXT_ID}
EXPLAIN_APPROVED = {"name": "explain_instructions", "text_id": APPROVED_TEXT_ID}
CHECK = {"name": "check_understanding"}
TRANSPORT = {"name": "arrange_transportation"}
BOOK = {"name": "book_follow_up", "slot": "2026-10-21T11:30"}
CLOSE = {"name": "close_encounter"}
ESCALATE = {"name": "escalate"}
MORNING_CALL = {"name": "outreach", "channel": "phone_call", "local_time": "09:30"}
EXPLAIN_EVAL = {"name": "explain_instructions", "text_id": "am-pm-schedule-1"}
E1_BOOK = {"name": "book_follow_up", "slot": "2026-11-04T09:30"}
E2_BOOK = {"name": "book_follow_up", "slot": "2026-11-13T10:00"}

FORBIDDEN_VISIBLE_SUBSTRINGS = (
    "17:00", "answered_from", "patient_responds", "understanding_requires",
    "booking_requires_arranged_ride", "teaching_goal", "patient_model",
    "HIDDEN_PATIENT_MODEL", "Simulator ground truth",
)


def run(encounter, *actions):
    return [encounter.act(action) for action in actions]


def contact(encounter):
    encounter.act(LATE_CALL)


class ContactStrategyTests(unittest.TestCase):
    def test_sms_gets_no_response_and_late_call_succeeds(self):
        encounter = start_encounter("T1", experiment_id="cc-test")
        self.assertEqual(encounter.act(SMS)["status"], "no_response")
        self.assertEqual(encounter.act(EARLY_CALL)["status"], "no_response")
        self.assertFalse(encounter.visible_state()["objectives"][0]["met"])
        self.assertEqual(encounter.act(LATE_CALL)["status"], "success")
        encounter.act(CLOSE)
        state = encounter.visible_state()
        self.assertEqual(state["status"], "completed")
        self.assertEqual([o["met"] for o in state["objectives"]], [True])

    def test_call_window_boundary_is_deterministic(self):
        for action, expected in ((CALL_1659, "no_response"), (CALL_1700, "success"),
                                 (CALL_2000, "success")):
            with self.subTest(action=action):
                encounter = start_encounter("T1", experiment_id="cc-test")
                self.assertEqual(encounter.act(action)["status"], expected)

    def test_coordination_actions_require_established_contact(self):
        encounter = start_encounter("T1", experiment_id="cc-test")  # T1 starts without contact
        for action in (EXPLAIN_GENERIC, CHECK):
            with self.subTest(action=action["name"]):
                self.assertRaisesRegex(ActionError, "contact", encounter.act, action)
        self.assertEqual(encounter.visible_state()["actions_used"], 0)


class CommunicationStrategyTests(unittest.TestCase):
    def reached(self, scenario_id="T2"):
        encounter = start_encounter(scenario_id, experiment_id="cc-test")
        if scenario_id == "T1":
            contact(encounter)  # Only T1 still needs to establish contact.
        return encounter

    def test_generic_explanation_is_misunderstood_and_correction_arrives(self):
        encounter = self.reached()
        encounter.act(EXPLAIN_GENERIC)
        outcome = encounter.act(CHECK)
        self.assertEqual(outcome["status"], "not_confirmed")
        self.assertIn("chart_change", outcome)
        correction = [e for e in encounter.trace()["events"] if e["type"] == "clinician_correction"]
        self.assertEqual(len(correction), 1)
        self.assertEqual(correction[0]["approved_text"], SCRIPTED_CLINICIAN_CORRECTION["approved_text"])
        self.assertIn("synthetic", correction[0]["source"].lower())
        chart_ids = [t["text_id"] for t in encounter.chart()["instructions"]["patient_education_texts"]]
        self.assertEqual(chart_ids, [GENERIC_TEXT_ID, APPROVED_TEXT_ID])

    def test_understanding_confirmed_only_after_approved_text_and_check(self):
        encounter = self.reached()
        run(encounter, EXPLAIN_GENERIC, CHECK)
        outcome = encounter.act(CHECK)  # Re-checking without approved wording still fails.
        self.assertEqual(outcome["status"], "not_confirmed")
        self.assertRaises(ActionError, encounter.act, CLOSE)
        run(encounter, EXPLAIN_APPROVED)
        self.assertEqual(encounter.act(CHECK)["status"], "confirmed")
        encounter.act(CLOSE)
        self.assertEqual(encounter.visible_state()["status"], "completed")

    def test_check_without_any_explanation_has_nothing_to_confirm(self):
        encounter = self.reached()
        outcome = encounter.act(CHECK)
        self.assertEqual(outcome["status"], "not_confirmed")
        self.assertNotIn("chart_change", outcome)

    def test_correction_is_delivered_once_and_only_in_scripted_scenario(self):
        encounter = self.reached()
        run(encounter, EXPLAIN_GENERIC, CHECK, CHECK)
        texts = encounter.chart()["instructions"]["patient_education_texts"]
        self.assertEqual([t["text_id"] for t in texts], [GENERIC_TEXT_ID, APPROVED_TEXT_ID])
        t1 = self.reached("T1")
        run(t1, EXPLAIN_GENERIC, CHECK)
        self.assertEqual(len(t1.chart()["instructions"]["patient_education_texts"]), 1)

    def test_intended_T2_path_leaves_budget_slack(self):
        encounter = start_encounter("T2", experiment_id="cc-test")
        for action in (EXPLAIN_GENERIC, CHECK, EXPLAIN_APPROVED, CHECK, CLOSE):
            encounter.act(action)
        state = encounter.visible_state()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["actions_used"], 5)
        self.assertGreaterEqual(state["actions_remaining"], 3)


class CoordinationSequenceTests(unittest.TestCase):
    def reached(self):
        return start_encounter("T3", experiment_id="cc-test")  # starts with contact established

    def test_transportation_before_booking_completes(self):
        encounter = self.reached()
        self.assertEqual(encounter.act(TRANSPORT)["status"], "success")
        self.assertEqual(encounter.act(BOOK)["status"], "success")
        encounter.act(CLOSE)
        state = encounter.visible_state()
        self.assertEqual(state["status"], "completed")
        self.assertEqual([o["met"] for o in state["objectives"]], [True, True, True])

    def test_booking_before_transportation_is_not_successful_coordination(self):
        encounter = self.reached()
        outcome = encounter.act(BOOK)
        self.assertEqual(outcome["status"], "unsuccessful")
        self.assertIn("ride", outcome["observation"])
        self.assertFalse(encounter.visible_state()["objectives"][2]["met"])
        self.assertRaises(ActionError, encounter.act, CLOSE)

    def test_failed_booking_can_be_recovered_within_budget(self):
        encounter = self.reached()
        run(encounter, BOOK, TRANSPORT, BOOK, CLOSE)
        state = encounter.visible_state()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["observed_outcomes"][0]["status"], "unsuccessful")
        self.assertEqual(state["observed_outcomes"][2]["status"], "success")

    def test_unresolved_booking_leaves_encounter_incomplete(self):
        encounter = self.reached()
        encounter.act(BOOK)
        state = encounter.finalize()
        self.assertEqual(state["status"], "incomplete")
        self.assertIn("follow_up_booked", state["completion_reason"])


class ValidationTests(unittest.TestCase):
    def test_unknown_and_probe_actions_are_rejected(self):
        encounter = start_encounter("T1", experiment_id="cc-test")
        probes = ({"name": "inspect_hidden_state"}, {"name": "get_patient_model"},
                  {"name": "read_simulator"}, {"name": "set_flag"},
                  {"name": "outreach_debug"}, {"name": ""}, {"name": 7},
                  {"name": "close_encounter", "force": True}, "close_encounter",
                  ["close_encounter"], None, {})
        for probe in probes:
            with self.subTest(probe=probe):
                self.assertRaises(ActionError, encounter.act, probe)
        self.assertEqual(encounter.visible_state()["actions_used"], 0)
        rejected = [e for e in encounter.trace()["events"] if e["type"] == "action_rejected"]
        self.assertEqual(len(rejected), len(probes))

    def test_outreach_arguments_are_validated_without_normalization(self):
        encounter = start_encounter("T1", experiment_id="cc-test")
        bad = (
            {"name": "outreach", "channel": "phone", "local_time": "12:00"},
            {"name": "outreach", "channel": "SMS", "local_time": "12:00"},
            {"name": "outreach", "channel": "email", "local_time": "12:00"},
            {"name": "outreach", "channel": "phone_call"},
            {"name": "outreach", "channel": "phone_call", "local_time": "9:00"},
            {"name": "outreach", "channel": "phone_call", "local_time": "17:00:00"},
            {"name": "outreach", "channel": "phone_call", "local_time": "24:00"},
            {"name": "outreach", "channel": "phone_call", "local_time": "07:59"},
            {"name": "outreach", "channel": "phone_call", "local_time": "20:01"},
            {"name": "outreach", "channel": "phone_call", "local_time": 1700},
        )
        for action in bad:
            with self.subTest(action=action):
                self.assertRaises(ActionError, encounter.act, action)
        self.assertEqual(encounter.visible_state()["actions_used"], 0)
        self.assertEqual(encounter.act(CALL_1700)["status"], "success")

    def test_explain_rejects_free_text_and_unapproved_ids(self):
        encounter = start_encounter("T2", experiment_id="cc-test")
        contact(encounter)
        self.assertRaises(ActionError, encounter.act,
                          {"name": "explain_instructions", "text": "Stop taking Asterol."})
        self.assertRaises(ActionError, encounter.act,
                          {"name": "explain_instructions", "text_id": "invented-1"})
        self.assertRaises(ActionError, encounter.act, EXPLAIN_APPROVED)  # Not yet in chart.
        run(encounter, EXPLAIN_GENERIC, CHECK)
        encounter.act(EXPLAIN_APPROVED)  # Available only after the scripted correction.

    def test_booking_arguments_are_validated(self):
        encounter = start_encounter("T3", experiment_id="cc-test")
        contact(encounter)
        self.assertRaises(ActionError, encounter.act, {"name": "book_follow_up"})
        self.assertRaises(ActionError, encounter.act,
                          {"name": "book_follow_up", "slot": "2026-10-22T09:00"})
        self.assertRaises(ActionError, encounter.act,
                          {"name": "book_follow_up", "slot": BOOK["slot"], "extra": 1})
        t1 = start_encounter("T1", experiment_id="cc-test")
        contact(t1)
        self.assertRaises(ActionError, t1.act, BOOK)  # No bookable slots in this chart.
        t2 = start_encounter("T2", experiment_id="cc-test")
        contact(t2)
        self.assertRaises(ActionError, t2.act, TRANSPORT)  # No transportation requirement.

    def test_rejections_never_change_state_or_budget(self):
        encounter = start_encounter("T3", experiment_id="cc-test")
        contact(encounter)
        self.assertEqual(encounter.act(BOOK)["status"], "unsuccessful")  # Valid args, bad order: executes.
        self.assertEqual(encounter.visible_state()["actions_used"], 2)  # Failed booking consumed budget.
        for action in ({"name": "book_follow_up", "slot": "nonsense"},
                       {"name": "teleport"}, {"name": "arrange_transportation", "note": "x"}):
            self.assertRaises(ActionError, encounter.act, action)
        self.assertEqual(encounter.visible_state()["actions_used"], 2)
        self.assertEqual(len(encounter.visible_state()["observed_outcomes"]), 2)
        self.assertEqual(encounter.chart()["follow_up"]["available_slots"],
                         SCENARIOS["T3"]["chart"]["follow_up"]["available_slots"])


class BudgetAndCompletionTests(unittest.TestCase):
    def test_budget_exhaustion_reports_incomplete_with_unmet_objectives(self):
        encounter = start_encounter("T2", experiment_id="cc-test")
        for action in (SMS, SMS, SMS, SMS, SMS, SMS, SMS, SMS):
            encounter.act(action)
        self.assertRaises(ActionError, encounter.act, SMS)
        state = encounter.visible_state()
        self.assertEqual(state["status"], "incomplete")
        self.assertIn("Action budget of 8 exhausted", state["completion_reason"])
        self.assertIn("understanding_confirmed", state["completion_reason"])
        self.assertRaises(ActionError, encounter.act, CLOSE)  # Already terminal.
        self.assertEqual(encounter.finalize()["status"], "incomplete")

    def test_finalize_reports_open_encounters_as_incomplete(self):
        encounter = start_encounter("T1", experiment_id="cc-test")
        encounter.act(SMS)
        state = encounter.finalize()
        self.assertEqual(state["status"], "incomplete")
        self.assertIn("contact_established", state["completion_reason"])

    def test_close_rejected_while_objectives_unmet_and_escalate_ends_encounter(self):
        encounter = start_encounter("T2", experiment_id="cc-test")
        self.assertRaises(ActionError, encounter.act, CLOSE)
        contact(encounter)
        run(encounter, EXPLAIN_GENERIC, CHECK)
        self.assertRaises(ActionError, encounter.act, CLOSE)
        encounter.act(ESCALATE)
        state = encounter.visible_state()
        self.assertEqual(state["status"], "escalated")
        self.assertIn("understanding_confirmed", state["completion_reason"])
        self.assertRaises(ActionError, encounter.act, SMS)

    def test_available_actions_catalog_is_small_and_leak_free(self):
        encounter = start_encounter("T1", experiment_id="cc-test")
        names = [spec["name"] for spec in encounter.available_actions()]
        self.assertEqual(names, ["outreach", "explain_instructions", "check_understanding",
                                 "arrange_transportation", "book_follow_up",
                                 "close_encounter", "escalate"])
        self.assertNotIn("17:00", json.dumps(encounter.available_actions()))


class ScenarioIsolationTests(unittest.TestCase):
    def test_T2_T3_start_with_contact_established_and_T1_does_not(self):
        for scenario_id in ("T2", "T3"):
            with self.subTest(scenario_id=scenario_id):
                encounter = start_encounter(scenario_id, experiment_id="cc-test")
                objectives = encounter.visible_state()["objectives"]
                self.assertEqual(objectives[0],
                                 {"objective": "contact_established", "met": True})
                self.assertIn("already established", json.dumps(encounter.chart()))
                self.assertIn("initial_state", [e["type"] for e in encounter.trace()["events"]])
        t1 = start_encounter("T1", experiment_id="cc-test")
        self.assertFalse(t1.visible_state()["objectives"][0]["met"])
        self.assertNotIn("contact_status", json.dumps(t1.chart()))

    def test_T2_allows_immediate_explanation(self):
        encounter = start_encounter("T2", experiment_id="cc-test")
        self.assertEqual(encounter.act(EXPLAIN_GENERIC)["status"], "success")

    def test_intended_paths_leave_budget_slack(self):
        paths = {
            "T1": (SMS, EARLY_CALL, LATE_CALL, CLOSE),
            "T2": (EXPLAIN_GENERIC, CHECK, EXPLAIN_APPROVED, CHECK, CLOSE),
            "T3": (BOOK, TRANSPORT, BOOK, CLOSE),
        }
        for scenario_id, script in paths.items():
            with self.subTest(scenario_id=scenario_id):
                encounter = start_encounter(scenario_id, experiment_id="cc-test")
                for action in script:
                    encounter.act(action)
                state = encounter.visible_state()
                self.assertEqual(state["status"], "completed")
                self.assertGreaterEqual(state["actions_remaining"], 3)


class StateSeparationTests(unittest.TestCase):
    def surfaces(self, encounter):
        return (json.dumps(encounter.chart()) + json.dumps(encounter.visible_state())
                + json.dumps(encounter.available_actions()) + json.dumps(encounter.trace()))

    def test_hidden_state_is_absent_from_every_visible_surface(self):
        for scenario_id, script in (("T2", (EXPLAIN_GENERIC, CHECK, EXPLAIN_APPROVED, CHECK, CLOSE)),
                                    ("T3", (BOOK, TRANSPORT, BOOK, CLOSE)),
                                    ("T1", (SMS, EARLY_CALL, LATE_CALL, CLOSE))):
            with self.subTest(scenario_id=scenario_id):
                encounter = start_encounter(scenario_id, experiment_id="cc-test")
                for action in script:
                    encounter.act(action)
                for banned in FORBIDDEN_VISIBLE_SUBSTRINGS:
                    self.assertNotIn(banned, self.surfaces(encounter))

    def test_approved_wording_is_invisible_until_the_correction_arrives(self):
        encounter = start_encounter("T2", experiment_id="cc-test")
        encounter.act(EXPLAIN_GENERIC)
        for banned in (APPROVED_TEXT_ID, "Morning: take one Asterol"):
            self.assertNotIn(banned, self.surfaces(encounter))
        encounter.act(CHECK)
        self.assertIn(APPROVED_TEXT_ID, self.surfaces(encounter))

    def test_debug_hidden_state_contains_the_ground_truth_it_claims(self):
        encounter = start_encounter("T2", experiment_id="cc-test")
        hidden = json.dumps(encounter.debug_hidden_state())
        for canary in ("17:00", "answered_from", "patient_responds"):
            self.assertIn(canary, hidden)

    def test_failed_outcomes_explain_observed_blockers_not_hidden_rules(self):
        encounter = start_encounter("T3", experiment_id="cc-test")
        contact(encounter)
        observation = encounter.act(BOOK)["observation"]
        self.assertIn("ride", observation)
        self.assertNotIn("17:00", observation)


class DeterminismAndIsolationTests(unittest.TestCase):
    def script(self, scenario_id):
        scripts = {
            "T1": (SMS, EARLY_CALL, LATE_CALL, CLOSE),
            "T2": (EXPLAIN_GENERIC, CHECK, EXPLAIN_APPROVED, CHECK, CLOSE),
            "T3": (BOOK, TRANSPORT, BOOK, CLOSE),
            "E1": (LATE_CALL, EXPLAIN_EVAL, CHECK, TRANSPORT, E1_BOOK, CLOSE),
            "E2": (MORNING_CALL, EXPLAIN_EVAL, CHECK, TRANSPORT, E2_BOOK, CLOSE),
        }
        return scripts[scenario_id]

    def play(self, scenario_id, experiment_id="cc-a", execution_id="exec-fixed", encounter_id="enc-fixed"):
        encounter = start_encounter(scenario_id, experiment_id=experiment_id,
                                    execution_id=execution_id, encounter_id=encounter_id)
        for action in self.script(scenario_id):
            encounter.act(action)
        return encounter

    def test_fresh_executions_start_from_identical_scenario_state(self):
        for scenario_id in SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                first, second = (start_encounter(scenario_id, experiment_id="cc-a",
                                                 execution_id="exec-1", encounter_id="enc-1")
                                 for _ in range(2))
                self.assertEqual(first.chart(), second.chart())
                self.assertEqual(first.debug_hidden_state(), second.debug_hidden_state())
                self.assertEqual(first.visible_state(), second.visible_state())

    def test_identical_scripts_produce_identical_traces(self):
        for scenario_id in SCENARIOS:
            with self.subTest(scenario_id=scenario_id):
                first, second = self.play(scenario_id), self.play(scenario_id)
                self.assertEqual(json.dumps(first.trace(), sort_keys=True),
                                 json.dumps(second.trace(), sort_keys=True))
                self.assertEqual(first.visible_state(), second.visible_state())
                self.assertEqual(first.finalize(), second.finalize())

    def test_scenario_definitions_are_never_mutated(self):
        pristine = deepcopy(SCENARIOS)
        for scenario_id in SCENARIOS:
            self.play(scenario_id, execution_id="exec-m", encounter_id="enc-m")
        self.assertEqual(SCENARIOS, pristine)

    def test_experiments_and_identifiers_stay_isolated(self):
        first = start_encounter("T2", experiment_id="cc-alpha",
                                execution_id="exec-alpha", encounter_id="enc-alpha")
        second = start_encounter("T2", experiment_id="cc-beta",
                                 execution_id="exec-beta", encounter_id="enc-beta")
        self.assertEqual(first.chart(), second.chart())
        self.assertEqual(first.debug_hidden_state(), second.debug_hidden_state())
        for action in self.script("T2"):
            first.act(action)
        fresh = start_encounter("T2", experiment_id="cc-beta",
                                execution_id="exec-beta2", encounter_id="enc-beta2")
        self.assertEqual(fresh.chart(), second.chart())
        self.assertEqual(fresh.debug_hidden_state(), second.debug_hidden_state())
        state = fresh.visible_state()
        self.assertEqual(state["actions_used"], 0)
        self.assertEqual(state["status"], "open")
        self.assertEqual({k: state[k] for k in ("experiment_id", "execution_id",
                                                "encounter_id", "patient_id")},
                         {"experiment_id": "cc-beta", "execution_id": "exec-beta2",
                          "encounter_id": "enc-beta2", "patient_id": PATIENT_ID})

    def test_identifier_formats_are_validated(self):
        for bad in ("sc-other", "cc-", 123, "bad", "cc-" + "x" * 61):
            with self.subTest(experiment_id=bad):
                self.assertRaises(ValueError, start_encounter, "T1", experiment_id=bad)
        for kwargs in ({"execution_id": "bad id!"}, {"encounter_id": "../escape"}):
            with self.subTest(kwargs=kwargs):
                self.assertRaises(ValueError, start_encounter, "T1", "cc-ok", **kwargs)
        self.assertRaises(ValueError, start_encounter, "T9", experiment_id="cc-ok")
        self.assertTrue(new_experiment_id().startswith("cc-"))


class TraceTests(unittest.TestCase):
    def test_trace_records_the_full_contract(self):
        encounter = start_encounter("T2", experiment_id="cc-test",
                                    execution_id="exec-trace", encounter_id="enc-trace")
        run(encounter, EXPLAIN_GENERIC, CHECK, EXPLAIN_APPROVED, CHECK, CLOSE)
        trace = encounter.trace()
        self.assertEqual(trace["execution"]["execution_id"], "exec-trace")
        self.assertEqual(trace["execution"]["encounter_id"], "enc-trace")
        self.assertEqual(trace["execution"]["patient_id"], PATIENT_ID)
        self.assertEqual(trace["execution"]["action_budget"], ACTION_BUDGET)
        self.assertIn("synthetic", trace["execution"])
        kinds = [event["type"] for event in trace["events"]]
        self.assertEqual(kinds[0], "chart_snapshot")
        self.assertEqual(kinds[1], "initial_state")
        self.assertEqual(kinds.count("action_requested"), 5)
        self.assertEqual(kinds.count("action_validated"), 5)
        self.assertEqual(kinds.count("outcome"), 5)
        self.assertEqual(kinds.count("clinician_correction"), 1)
        self.assertEqual(kinds[-1], "encounter_completed")
        transition = [e for e in trace["events"] if e["type"] == "state_transition"]
        self.assertEqual(len(transition), 1)  # Contact was established at start, not by an action.
        self.assertEqual(transition[0]["changed"]["understanding_confirmed"],
                         {"before": False, "after": True})
        seqs = [event["seq"] for event in trace["events"]]
        self.assertEqual(seqs, sorted(seqs) and list(range(1, len(seqs) + 1)))
        final = trace["events"][-1]
        self.assertEqual(final["status"], "completed")
        self.assertIn("reason", final)

    def test_rejections_are_audited_in_the_trace_only(self):
        encounter = start_encounter("T1", experiment_id="cc-test")
        self.assertRaises(ActionError, encounter.act, {"name": "nope"})
        kinds = [event["type"] for event in encounter.trace()["events"]]
        self.assertEqual(kinds, ["chart_snapshot", "action_requested", "action_rejected"])
        self.assertEqual(encounter.visible_state()["observed_outcomes"], [])

    def test_trace_never_contains_hidden_state(self):
        encounter = self.trace_encounter = start_encounter("T3", experiment_id="cc-test")
        run(encounter, BOOK, TRANSPORT, BOOK, CLOSE)
        for banned in FORBIDDEN_VISIBLE_SUBSTRINGS:
            self.assertNotIn(banned, json.dumps(encounter.trace()))


USAGE_STUB = dict(model="test-only", calls=1, elapsed_seconds=0.0,
                  prompt_tokens=None, output_tokens=None, total_tokens=None)

T2_SCRIPT = [dict(action="explain_instructions", arguments={"text_id": GENERIC_TEXT_ID},
                  rationale="TEST FIXTURE: start with the chart's approved text",
                  recalled_lesson_ids=[]),
             dict(action="check_understanding", arguments={},
                  rationale="TEST FIXTURE: confirm understanding", recalled_lesson_ids=[]),
             dict(action="explain_instructions", arguments={"text_id": APPROVED_TEXT_ID},
                  rationale="TEST FIXTURE: use the corrected approved wording",
                  recalled_lesson_ids=[]),
             dict(action="check_understanding", arguments={},
                  rationale="TEST FIXTURE: confirm again", recalled_lesson_ids=[]),
             dict(action="close_encounter", arguments={},
                  rationale="TEST FIXTURE: objectives met", recalled_lesson_ids=[])]


class FakeProvider:
    """Test-only scripted provider seam; the application never sees this class."""

    def __init__(self, script):
        self.model = "test-only"
        self.script = list(script)
        self.calls = []

    def generate(self, system, payload, schema):
        self.calls.append((system, deepcopy(payload)))
        if not self.script:
            raise ProviderError("TEST FIXTURE: script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        decision = item(payload) if callable(item) else dict(item)
        return Decision.model_validate(decision), dict(USAGE_STUB), json.dumps(decision)


def d(action, **arguments):
    return dict(action=action, arguments=arguments, rationale="TEST FIXTURE rationale",
                recalled_lesson_ids=[])


class AgentLoopTests(unittest.TestCase):
    def run_t(self, scenario_id, script, execution="exec-agent", encounter="enc-agent"):
        self.provider = FakeProvider(script)
        return run_encounter(self.provider, scenario_id, experiment_id="cc-test",
                             execution_id=execution, encounter_id=encounter)

    def test_structured_decision_executes_the_simulator_action(self):
        run = self.run_t("T1", [d("outreach", channel="phone_call", local_time="18:00"),
                                d("close_encounter")])
        self.assertEqual(run["result"]["status"], "completed")
        outcomes = run["final_visible_state"]["observed_outcomes"]
        self.assertEqual([o["action"] for o in outcomes],
                         [{"name": "outreach", "channel": "phone_call", "local_time": "18:00"},
                          {"name": "close_encounter"}])
        self.assertEqual(outcomes[0]["status"], "success")

    def test_model_never_receives_hidden_state(self):
        run = self.run_t("T2", deepcopy(T2_SCRIPT))
        self.assertEqual(run["result"]["status"], "completed")
        payloads = json.dumps([call[1] for call in self.provider.calls])
        for banned in FORBIDDEN_VISIBLE_SUBSTRINGS:
            self.assertNotIn(banned, payloads)
        self.assertNotIn(APPROVED_TEXT_ID, json.dumps(self.provider.calls[0][1]))
        self.assertNotIn(APPROVED_TEXT_ID, json.dumps(self.provider.calls[1][1]))
        self.assertIn(APPROVED_TEXT_ID, json.dumps(self.provider.calls[2][1]))  # post-correction

    def test_system_prompt_has_no_solution_hints(self):
        for hint in ("17:00", "after 5", "5 PM", "five", "transportation first",
                     "arrange transportation before", "morning/evening", "teaching"):
            self.assertNotIn(hint, SYSTEM_POLICY)
        for required in ("synthetic", "coordination", "approved", "chart", "outcome",
                         str(ACTION_BUDGET)):
            self.assertIn(required, SYSTEM_POLICY)

    def test_history_and_outcomes_are_supplied_on_subsequent_steps(self):
        run = self.run_t("T1", [d("outreach", channel="sms", local_time="10:00"),
                                d("outreach", channel="phone_call", local_time="18:00"),
                                d("close_encounter")])
        self.assertEqual(run["result"]["status"], "completed")
        second = self.provider.calls[1][1]
        self.assertEqual(second["step"], 2)
        self.assertEqual(second["chart"]["patient"]["patient_id"], PATIENT_ID)
        self.assertEqual(second["available_actions"][0]["name"], "outreach")
        self.assertEqual(second["visible_state"]["actions_remaining"], 7)
        self.assertEqual(second["visible_state"]["action_history"][0]["channel"], "sms")
        self.assertEqual(second["visible_state"]["observed_outcomes"][0]["status"], "no_response")
        self.assertEqual(set(second), {"chart", "visible_state", "available_actions",
                                       "step", "attempt", "attempt_failures"})

    def test_action_budget_is_enforced_without_extra_model_calls(self):
        run = self.run_t("T1", [d("outreach", channel="sms", local_time="10:00")] * 8)
        self.assertEqual(run["result"]["status"], "incomplete")
        self.assertIsNone(run["result"]["error"])
        self.assertEqual(run["result"]["actions_used"], ACTION_BUDGET)
        self.assertEqual(len(self.provider.calls), ACTION_BUDGET)  # no ninth model call
        self.assertIn("Action budget of 8 exhausted",
                      run["result"]["completion_reason"])

    def test_successful_close_terminates_the_loop(self):
        run = self.run_t("T1", [d("outreach", channel="phone_call", local_time="18:00"),
                                d("close_encounter"), d("escalate")])  # leftover proves stopping
        self.assertEqual(run["result"]["status"], "completed")
        self.assertEqual(len(self.provider.calls), 2)

    def test_escalation_terminates_the_loop(self):
        run = self.run_t("T2", [d("escalate")])
        self.assertEqual(run["result"]["status"], "escalated")
        self.assertEqual(run["result"]["actions_used"], 1)
        self.assertEqual(len(self.provider.calls), 1)

    def test_malformed_output_is_retried_with_feedback_then_recovers(self):
        run = self.run_t("T1", [StructuredOutputError("TEST FIXTURE: not valid JSON"),
                                d("outreach", channel="phone_call", local_time="18:00"),
                                d("close_encounter")])
        self.assertEqual(run["result"]["status"], "completed")
        invalid = [e for e in run["trace"]["events"] if e["type"] == "invalid_decision"]
        self.assertEqual(len(invalid), 1)
        self.assertEqual((invalid[0]["step"], invalid[0]["attempt"]), (1, 1))
        retry = self.provider.calls[1][1]
        self.assertEqual(len(retry["attempt_failures"]), 1)
        self.assertIn("TEST FIXTURE", retry["attempt_failures"][0])

    def test_structured_failure_exhaustion_fails_the_run(self):
        run = self.run_t("T1", [StructuredOutputError("TEST FIXTURE: bad")] * MAX_STEP_ATTEMPTS)
        self.assertEqual(run["result"]["status"], "failed")
        self.assertIn("Structured-output failure", run["result"]["error"])
        self.assertIn("last reason", run["result"]["error"])
        self.assertEqual(run["result"]["actions_used"], 0)
        self.assertEqual(len(self.provider.calls), MAX_STEP_ATTEMPTS)
        self.assertEqual(run["final_visible_state"]["status"], "incomplete")

    def test_invalid_decision_cannot_bypass_simulator_validation(self):
        run = self.run_t("T3", [
            d("book_follow_up", slot="2026-99-99T00:00"),
            dict(action="book_follow_up",
                 arguments={"slot": "2026-10-21T11:30", "why": "1"},
                 rationale="TEST FIXTURE", recalled_lesson_ids=[]),
            d("arrange_transportation"),
            d("book_follow_up", slot="2026-10-21T11:30"),
            d("close_encounter")])
        self.assertEqual(run["result"]["status"], "completed")
        invalid = [e for e in run["trace"]["events"] if e["type"] == "invalid_decision"]
        self.assertEqual(len(invalid), 2)
        self.assertIn("Simulator rejected", invalid[0]["reason"])
        self.assertIn("unexpected", invalid[1]["reason"])
        requested = [e["action"]["name"] for e in run["trace"]["events"]
                     if e["type"] == "action_requested"]
        self.assertEqual(requested, ["book_follow_up", "arrange_transportation",
                                     "book_follow_up", "close_encounter"])

    def test_provider_failure_is_surfaced_not_converted_to_success(self):
        run = self.run_t("T1", [ProviderError("Gemini HTTP 500: TEST FIXTURE outage")])
        self.assertEqual(run["result"]["status"], "failed")
        self.assertIn("Gemini HTTP 500", run["result"]["error"])
        self.assertEqual(run["result"]["actions_used"], 0)
        self.assertEqual(run["final_visible_state"]["status"], "incomplete")
        errors = [e for e in run["trace"]["events"] if e["type"] == "provider_error"]
        self.assertEqual(len(errors), 1)
        finished = [e for e in run["trace"]["events"] if e["type"] == "agent_run_finished"][0]
        self.assertEqual(finished["status"], "failed")

    def test_recalled_lesson_ids_must_remain_empty(self):
        run = self.run_t("T1", [
            dict(action="outreach", arguments={"channel": "sms", "local_time": "10:00"},
                 rationale="TEST FIXTURE", recalled_lesson_ids=["made-up-lesson"]),
            d("outreach", channel="phone_call", local_time="18:00"),
            d("close_encounter")])
        self.assertEqual(run["result"]["status"], "completed")
        invalid = [e for e in run["trace"]["events"] if e["type"] == "invalid_decision"]
        self.assertEqual(len(invalid), 1)
        self.assertIn("recalled_lesson_ids", invalid[0]["reason"])
        steps = [e for e in run["trace"]["events"] if e["type"] == "agent_step"]
        self.assertTrue(steps and all(e["recalled_lesson_ids"] == [] for e in steps))


class AgentTraceTests(unittest.TestCase):
    def completed_t2(self):
        provider = FakeProvider(deepcopy(T2_SCRIPT))
        run = run_encounter(provider, "T2", experiment_id="cc-test",
                            execution_id="exec-trace", encounter_id="enc-trace")
        return run, provider

    def test_trace_records_prompts_decisions_actions_and_outcomes(self):
        run, provider = self.completed_t2()
        trace = run["trace"]
        header = trace["execution"]
        for key in ("experiment_id", "execution_id", "encounter_id", "patient_id",
                    "scenario_id", "action_budget", "agent", "synthetic"):
            self.assertIn(key, header)
        self.assertEqual(header["agent"]["memory_mode"], "none")
        self.assertEqual(header["agent"]["run_mode"], "read_only")
        self.assertEqual(header["agent"]["system_policy"], SYSTEM_POLICY)
        calls = [e for e in trace["events"] if e["type"] == "model_call"]
        self.assertEqual(len(calls), 5)
        for index, event in enumerate(calls, start=1):
            self.assertEqual(event["step"], index)
            self.assertEqual(event["provider"], "test-only")
            self.assertEqual(event["prompt"]["chart"]["patient"]["patient_id"], PATIENT_ID)
            self.assertEqual(event["prompt"]["visible_state"]["actions_remaining"],
                             ACTION_BUDGET - (index - 1))
            self.assertEqual(event["remaining_budget"], ACTION_BUDGET - (index - 1))
        decisions = [e for e in trace["events"] if e["type"] == "model_decision"]
        self.assertEqual(decisions[0]["decision"]["action"], "explain_instructions")
        self.assertIn("raw", decisions[0])
        self.assertEqual(decisions[0]["usage"]["model"], "test-only")
        agent_steps = [e for e in trace["events"] if e["type"] == "agent_step"]
        self.assertEqual([e["action"]["name"] for e in agent_steps],
                         ["explain_instructions", "check_understanding",
                          "explain_instructions", "check_understanding", "close_encounter"])
        self.assertTrue(all(e["rationale"] for e in agent_steps))
        outcomes = [e for e in trace["events"] if e["type"] == "outcome"]
        self.assertEqual(outcomes[1]["status"], "not_confirmed")
        self.assertEqual(trace["events"][-1]["type"], "agent_run_finished")
        self.assertEqual(trace["events"][-1]["status"], "completed")
        self.assertEqual(run["result"]["model_calls"], 5)

    def test_agent_traces_do_not_expose_hidden_state(self):
        run, _ = self.completed_t2()
        for banned in FORBIDDEN_VISIBLE_SUBSTRINGS:
            self.assertNotIn(banned, json.dumps(run["trace"]))
        self.assertNotIn(APPROVED_TEXT_ID, json.dumps(run["trace"]["events"][0]))

    def test_scripted_provider_runs_are_reproducible(self):
        script = [d("book_follow_up", slot="2026-10-21T11:30"), d("arrange_transportation"),
                  d("book_follow_up", slot="2026-10-21T11:30"), d("close_encounter")]
        first = run_encounter(FakeProvider(script), "T3", experiment_id="cc-det",
                              execution_id="exec-det", encounter_id="enc-det")
        second = run_encounter(FakeProvider(script), "T3", experiment_id="cc-det",
                               execution_id="exec-det", encounter_id="enc-det")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


class AgentBoundaryTests(unittest.TestCase):
    def test_debug_hidden_state_is_unreachable_from_agent_code(self):
        for name in ("agent.py", "run_agent.py", "run_demo.py", "compare.py"):
            source = Path(__file__).with_name(name).read_text()
            self.assertNotIn("debug_hidden_state", source)

    def test_decision_fault_checks_arguments_and_memory_field(self):
        good = Decision.model_validate(d("outreach", channel="sms", local_time="10:00"))
        self.assertIsNone(decision_fault(good))
        extra = Decision.model_validate(
            dict(action="outreach", arguments={"channel": "sms"}, rationale="TEST FIXTURE",
                 recalled_lesson_ids=[]))
        self.assertIn("missing", decision_fault(extra))
        invented = Decision.model_validate(
            dict(action="outreach", arguments={"channel": "sms", "local_time": "10:00"},
                 rationale="TEST FIXTURE", recalled_lesson_ids=["x"]))
        self.assertIn("recalled_lesson_ids", decision_fault(invented))


def seed_lesson(reference, category="contact_strategy", evidence_type="observed_outcome"):
    return {"id": reference, "category": category, "patient_id": PATIENT_ID,
            "applicability": f"Synthetic patient {PATIENT_ID}: seed applicability.",
            "guidance": "TEST FIXTURE seed guidance.",
            "evidence_type": evidence_type,
            "evidence_summary": "TEST FIXTURE seed evidence.",
            "experiment_id": "cc-test", "source_encounter_id": "enc-seed",
            "source_execution_id": "exec-seed"}


class FakeMemory:
    """Test-only Mubit double over a shared durable list; production uses the real client."""

    def __init__(self, experiment="cc-test", stored=None, fail_recall=False, fail_write=False,
                 calls_sink=None):
        self.experiment = experiment
        self.run_id = f"{experiment}-fake"
        self.server = stored if stored is not None else []
        self.fail_recall = fail_recall
        self.fail_write = fail_write
        self.calls = calls_sink if calls_sink is not None else []

    def recall(self, patient_id=PATIENT_ID):
        self.calls.append(("recall", patient_id))
        if self.fail_recall:
            raise MemoryError("TEST FIXTURE: Mubit recall failed")
        return deepcopy([lesson for lesson in self.server
                         if lesson["experiment_id"] == self.experiment])

    def remember(self, candidate):
        self.calls.append(("remember", deepcopy(candidate)))
        if self.fail_write:
            raise MemoryError("TEST FIXTURE: Mubit write failed")
        reference = f"mubit-ref-{len(self.server) + 1}"
        self.server.append({"id": reference, **deepcopy(candidate["lesson"]),
                            "experiment_id": candidate["experiment_id"],
                            "source_encounter_id": candidate["source_encounter_id"],
                            "source_execution_id": candidate["source_execution_id"],
                            "evidence_summary": candidate["evidence_summary"]})
        return reference

    def record(self, reference_id, succeeded, rationale):
        self.calls.append(("record", reference_id, succeeded))

    def close(self):
        pass


class TeachingIsolationTests(unittest.TestCase):
    def test_teaching_objectives_are_exactly_isolated(self):
        self.assertEqual(SCENARIOS["T1"]["objectives"], ["contact_established"])
        self.assertEqual(SCENARIOS["T2"]["objectives"],
                         ["contact_established", "understanding_confirmed"])
        self.assertEqual(SCENARIOS["T3"]["objectives"],
                         ["contact_established", "transportation_arranged", "follow_up_booked"])

    def test_T1_closes_after_contact_without_solving_communication(self):
        provider = FakeProvider([d("outreach", channel="phone_call", local_time="18:00"),
                                 d("close_encounter")])
        run = run_encounter(provider, "T1", experiment_id="cc-test")
        self.assertEqual(run["result"]["status"], "completed")
        names = [o["action"]["name"] for o in run["final_visible_state"]["observed_outcomes"]]
        self.assertEqual(names, ["outreach", "close_encounter"])


class LessonDerivationTests(unittest.TestCase):
    def teach(self, scenario_id, script, memory_kwargs=None):
        self.memory = FakeMemory(**(memory_kwargs or {}))
        self.provider = FakeProvider(script)
        self.run = run_encounter(self.provider, scenario_id, experiment_id="cc-test",
                                 execution_id="exec-teach", encounter_id="enc-teach",
                                 memory=self.memory, memory_mode="none", run_mode="teaching")
        return self.run

    def candidates(self, run=None):
        return [e["candidate"] for e in (run or self.run)["trace"]["events"]
                if e["type"] == "lesson_candidate"]

    def writes(self):
        return [args for name, args in self.memory.calls if name == "remember"]

    def test_T1_lesson_requires_failed_attempt_then_successful_call(self):
        self.teach("T1", [d("outreach", channel="sms", local_time="10:00"),
                          d("outreach", channel="phone_call", local_time="18:00"),
                          d("close_encounter")])
        self.assertEqual(self.run["result"]["status"], "completed")
        self.assertEqual(len(self.candidates()), 1)
        self.assertEqual(self.candidates()[0]["lesson"]["category"], "contact_strategy")
        self.assertEqual(self.run["result"]["lessons_stored"], 1)
        self.assertEqual(self.run["result"]["reference_ids"], ["mubit-ref-1"])
        kinds = [e["type"] for e in self.run["trace"]["events"]]
        self.assertLess(kinds.index("lesson_candidate"), kinds.index("memory_write_started"))
        self.assertLess(kinds.index("memory_write_started"), kinds.index("memory_write_finished"))

    def test_T1_without_failure_evidence_stores_nothing(self):
        self.teach("T1", [d("outreach", channel="phone_call", local_time="18:00"),
                          d("close_encounter")])
        self.assertEqual(self.run["result"]["status"], "completed")
        self.assertEqual(self.candidates(), [])
        self.assertEqual(self.writes(), [])

    def test_T1_lesson_is_patient_specific_and_conditional(self):
        self.teach("T1", [d("outreach", channel="sms", local_time="10:00"),
                          d("outreach", channel="phone_call", local_time="18:00"),
                          d("close_encounter")])
        lesson = self.candidates()[0]["lesson"]
        self.assertEqual(lesson["patient_id"], PATIENT_ID)
        self.assertIn(PATIENT_ID, lesson["applicability"])
        self.assertIn("no newer contact preference", lesson["guidance"])
        self.assertIn("conditional", lesson["guidance"])
        self.assertNotIn("never", lesson["guidance"].lower())

    def test_T2_lesson_requires_correction_wording_and_confirmation(self):
        self.teach("T2", deepcopy(T2_SCRIPT))
        self.assertEqual(self.run["result"]["status"], "completed")
        candidates = self.candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["lesson"]["category"], "communication_strategy")
        self.assertEqual(candidates[0]["lesson"]["evidence_type"], "clinician_correction")
        self.assertIn(APPROVED_TEXT_ID, candidates[0]["lesson"]["guidance"])

    def test_T2_without_confirmed_understanding_stores_nothing(self):
        self.teach("T2", [d("explain_instructions", text_id=GENERIC_TEXT_ID),
                          d("check_understanding"),
                          d("explain_instructions", text_id=APPROVED_TEXT_ID),
                          d("close_encounter")])  # approved wording explained, never confirmed
        self.assertEqual(self.candidates(), [])
        self.assertEqual(self.writes(), [])

    def test_T2_without_corrected_wording_stores_nothing(self):
        self.teach("T2", [d("explain_instructions", text_id=GENERIC_TEXT_ID),
                          d("check_understanding"),
                          d("check_understanding"),
                          d("close_encounter")])  # correction arrived, approved text never used
        self.assertEqual(self.candidates(), [])
        self.assertEqual(self.writes(), [])

    def test_T3_lesson_requires_failed_booking_then_recovered_sequence(self):
        self.teach("T3", [d("book_follow_up", slot="2026-10-21T11:30"),
                          d("arrange_transportation"),
                          d("book_follow_up", slot="2026-10-21T11:30"),
                          d("close_encounter")])
        self.assertEqual(self.run["result"]["status"], "completed")
        candidates = self.candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["lesson"]["category"], "coordination_sequence")
        self.assertIn("transportation before booking", candidates[0]["lesson"]["guidance"])

    def test_T3_without_failed_booking_stores_nothing(self):
        self.teach("T3", [d("arrange_transportation"),
                          d("book_follow_up", slot="2026-10-21T11:30"),
                          d("close_encounter")])
        self.assertEqual(self.candidates(), [])
        self.assertEqual(self.writes(), [])

    def test_failed_run_creates_no_lesson(self):
        self.teach("T1", [ProviderError("TEST FIXTURE: provider died mid-encounter")])
        self.assertEqual(self.run["result"]["status"], "failed")
        self.assertEqual(self.candidates(), [])
        self.assertEqual(self.writes(), [])

    def test_chart_facts_are_not_written_as_lessons(self):
        self.teach("T3", [d("book_follow_up", slot="2026-10-21T11:30"),
                          d("arrange_transportation"),
                          d("book_follow_up", slot="2026-10-21T11:30"),
                          d("close_encounter")])
        blob = json.dumps(self.candidates())
        self.assertNotIn("Northbridge", blob)  # chart/clinic facts stay out of lessons
        self.assertTrue(all(c["lesson"]["category"] in CATEGORIES for c in self.candidates()))

    def test_hidden_state_cannot_enter_memory_writes(self):
        self.teach("T1", [d("outreach", channel="sms", local_time="10:00"),
                          d("outreach", channel="phone_call", local_time="18:00"),
                          d("close_encounter")])
        blob = json.dumps(self.writes())
        for marker in HIDDEN_MARKERS:
            self.assertNotIn(marker, blob)
        self.assertNotIn("17:00", blob)

    def test_validation_rejects_tampered_or_injected_candidates(self):
        encounter = start_encounter("T1", experiment_id="cc-test",
                                    execution_id="exec-x", encounter_id="enc-x")
        for action in (SMS, EARLY_CALL, LATE_CALL, CLOSE):
            encounter.act(action)
        derived = derive_lesson_candidates(encounter.visible_state(), encounter.chart())
        self.assertEqual(len(derived), 1)
        self.assertIsNone(validate_candidate(derived[0], derived))
        tampered = deepcopy(derived[0])
        tampered["lesson"]["guidance"] = "Take 2 mg twice daily and stop the medication."
        self.assertIn("treatment-like", validate_candidate(tampered, derived))
        foreign = deepcopy(derived[0])
        foreign["patient_id"] = "SYN-PT-9999"
        self.assertIn("patient id mismatch", validate_candidate(foreign, derived))
        injected = deepcopy(derived[0])
        injected["lesson"]["guidance"] = "Call after 17:00 always works for everyone."
        self.assertIn("not identical", validate_candidate(injected, derived))


class MemoryLifecycleTests(unittest.TestCase):
    def run_with(self, scenario_id, script, memory, memory_mode, run_mode="read_only"):
        provider = FakeProvider(script)
        run = run_encounter(provider, scenario_id, experiment_id="cc-test",
                            execution_id="exec-mem", encounter_id="enc-mem",
                            memory=memory, memory_mode=memory_mode, run_mode=run_mode)
        return run, provider

    def test_full_recall_happens_once_and_snapshot_is_frozen(self):
        stored = [seed_lesson("mubit-ref-9")]
        memory = FakeMemory(stored=stored)
        run, provider = self.run_with("T1", [d("outreach", channel="phone_call", local_time="18:00"),
                                             d("close_encounter")], memory, "full")
        self.assertEqual([c[0] for c in memory.calls], ["recall"])
        normalized = normalize_for_prompt(stored)
        for _, payload in provider.calls:
            self.assertEqual(payload["recalled_operational_lessons"], normalized)
        frozen = [e for e in run["trace"]["events"] if e["type"] == "memory_snapshot_frozen"]
        self.assertEqual(frozen[0]["sha256"], snapshot_hash(normalized))
        self.assertEqual(frozen[0]["lessons"], normalized)
        self.assertEqual(run["result"]["frozen_memory_sha256"], snapshot_hash(normalized))
        self.assertEqual(run["trace"]["execution"]["agent"]["memory_mode"], "full")

    def test_memory_none_never_recalls_and_supplies_no_lessons(self):
        memory = FakeMemory()
        run, provider = self.run_with("T1", [d("outreach", channel="phone_call", local_time="18:00"),
                                             d("close_encounter")], memory, "none")
        self.assertEqual(memory.calls, [])
        self.assertNotIn("recalled_operational_lessons",
                         json.dumps([payload for _, payload in provider.calls]))
        self.assertIsNone(run["result"]["frozen_memory_sha256"])

    def test_read_only_performs_zero_writes_despite_teaching_evidence(self):
        memory = FakeMemory(stored=[seed_lesson("mubit-ref-1")])
        run, _ = self.run_with("T3", [d("book_follow_up", slot="2026-10-21T11:30"),
                                      d("arrange_transportation"),
                                      d("book_follow_up", slot="2026-10-21T11:30"),
                                      d("close_encounter")], memory, "full", run_mode="read_only")
        self.assertEqual(run["result"]["status"], "completed")
        self.assertEqual([c[0] for c in memory.calls], ["recall"])
        self.assertEqual(run["result"]["lessons_stored"], 0)

    def test_citations_restricted_to_actually_recalled_ids(self):
        stored = [seed_lesson("mubit-ref-1")]
        memory = FakeMemory(stored=stored)
        run, _ = self.run_with("T1", [
            dict(action="outreach", arguments={"channel": "phone_call", "local_time": "18:00"},
                 rationale="TEST FIXTURE", recalled_lesson_ids=["fabricated-id"]),
            d("outreach", channel="phone_call", local_time="18:00"),
            d("close_encounter")], memory, "full")
        self.assertEqual(run["result"]["status"], "completed")
        invalid = [e for e in run["trace"]["events"] if e["type"] == "invalid_decision"]
        self.assertEqual(len(invalid), 1)
        self.assertIn("not recalled", invalid[0]["reason"])

    def test_valid_citation_is_recorded_without_proving_causality(self):
        stored = [seed_lesson("mubit-ref-1")]
        memory = FakeMemory(stored=stored)
        run, _ = self.run_with("T1", [
            dict(action="outreach", arguments={"channel": "phone_call", "local_time": "18:00"},
                 rationale="TEST FIXTURE", recalled_lesson_ids=["mubit-ref-1"]),
            d("close_encounter")], memory, "full")
        steps = [e for e in run["trace"]["events"] if e["type"] == "agent_step"]
        self.assertEqual(steps[0]["recalled_lesson_ids"], ["mubit-ref-1"])
        self.assertIn("not proven causality",
                      run["trace"]["execution"]["agent"]["citation_semantics"])

    def test_recall_failure_surfaces_as_failed_run(self):
        memory = FakeMemory(fail_recall=True)
        run, provider = self.run_with("T1", [d("close_encounter")], memory, "full")
        self.assertEqual(run["result"]["status"], "failed")
        self.assertIn("recall", run["result"]["error"])
        self.assertEqual(len(provider.calls), 0)  # no model call happened
        self.assertIn("memory_recall_failed", [e["type"] for e in run["trace"]["events"]])
        self.assertEqual(run["final_visible_state"]["status"], "incomplete")

    def test_write_failure_surfaces_as_failed_run(self):
        memory = FakeMemory(fail_write=True)
        run, _ = self.run_with("T1", [d("outreach", channel="sms", local_time="10:00"),
                                      d("outreach", channel="phone_call", local_time="18:00"),
                                      d("close_encounter")], memory, "none", run_mode="teaching")
        self.assertEqual(run["result"]["status"], "failed")
        self.assertIn("write", run["result"]["error"])
        kinds = [e["type"] for e in run["trace"]["events"]]
        self.assertIn("memory_write_started", kinds)
        self.assertIn("memory_write_failed", kinds)
        self.assertEqual(run["result"]["reference_ids"], [])

    def test_memory_none_mode_still_requires_empty_citations(self):
        run, _ = self.run_with("T1", [
            dict(action="outreach", arguments={"channel": "phone_call", "local_time": "18:00"},
                 rationale="TEST FIXTURE", recalled_lesson_ids=["anything"]),
            d("outreach", channel="phone_call", local_time="18:00"),
            d("close_encounter")], FakeMemory(), "none")
        invalid = [e for e in run["trace"]["events"] if e["type"] == "invalid_decision"]
        self.assertEqual(len(invalid), 1)
        self.assertIn("memory=none", invalid[0]["reason"])

    def test_restarted_memory_instance_recalls_durably_and_experiments_isolate(self):
        durable = []
        teaching = FakeMemory("cc-alpha", stored=durable)
        run_encounter(FakeProvider([d("outreach", channel="sms", local_time="10:00"),
                                    d("outreach", channel="phone_call", local_time="18:00"),
                                    d("close_encounter")]),
                      "T1", experiment_id="cc-alpha", execution_id="exec-a", encounter_id="enc-a",
                      memory=teaching, memory_mode="none", run_mode="teaching")
        self.assertEqual(len(teaching.server), 1)
        restarted = FakeMemory("cc-alpha", stored=durable)  # fresh instance, same durable store
        self.assertEqual([l["id"] for l in restarted.recall()], ["mubit-ref-1"])
        other = FakeMemory("cc-beta", stored=durable)
        self.assertEqual(other.recall(), [])
        provider = FakeProvider([d("outreach", channel="phone_call", local_time="18:00"),
                                 d("close_encounter")])
        run = run_encounter(provider, "T1", experiment_id="cc-alpha",
                            execution_id="exec-b", encounter_id="enc-b",
                            memory=restarted, memory_mode="full", run_mode="read_only")
        lessons = provider.calls[0][1]["recalled_operational_lessons"]
        self.assertEqual([l["id"] for l in lessons], ["mubit-ref-1"])
        self.assertEqual(run["result"]["status"], "completed")

    def test_prompt_lessons_carry_only_safe_fields(self):
        stored = [seed_lesson("mubit-ref-1")]
        _, provider = self.run_with("T1", [d("outreach", channel="phone_call", local_time="18:00"),
                                           d("close_encounter")], FakeMemory(stored=stored), "full")
        lessons = provider.calls[0][1]["recalled_operational_lessons"]
        self.assertEqual(set(lessons[0]), set(PROMPT_FIELDS))
        self.assertNotIn("source_execution_id", json.dumps(lessons))

    def test_frozen_hash_is_deterministic_and_order_insensitive(self):
        first, second = seed_lesson("b"), seed_lesson("a")
        one = normalize_for_prompt([first, second])
        two = normalize_for_prompt(list(reversed([first, second])))
        self.assertEqual(snapshot_hash(one), snapshot_hash(two))
        self.assertEqual([l["id"] for l in one], sorted(l["id"] for l in one))

    def test_mode_arguments_are_validated(self):
        for kwargs in ({"memory_mode": "sometimes"}, {"run_mode": "evaluation"},
                       {"memory_mode": "full"}, {"run_mode": "teaching"}):
            with self.subTest(kwargs=kwargs):
                self.assertRaises(ValueError, run_encounter, FakeProvider([]), "T1",
                                  experiment_id="cc-test", **kwargs)

    def test_policy_places_current_state_above_memory(self):
        self.assertLess(SYSTEM_POLICY.index("current chart"),
                        SYSTEM_POLICY.index("recalled_operational_lessons"))
        for required in ("authoritative", "override", "conditional historical guidance"):
            self.assertIn(required, SYSTEM_POLICY)


class RealMemoryBoundaryTests(unittest.TestCase):
    """The production Memory class against a mocked SDK transport (sibling pattern)."""

    def memory_with_client(self, experiment="cc-test"):
        memory = Memory.__new__(Memory)
        memory.experiment = experiment
        memory.run_id = f"{experiment}-{MEMORY_VERSION}"
        memory.client = Mock()
        return memory

    def candidate(self):
        encounter = start_encounter("T1", experiment_id="cc-test",
                                    execution_id="exec-rt", encounter_id="enc-rt")
        for action in (SMS, EARLY_CALL, LATE_CALL, CLOSE):
            encounter.act(action)
        return derive_lesson_candidates(encounter.visible_state(), encounter.chart())[0]

    def test_round_trip_grounding_and_experiment_isolation(self):
        memory = self.memory_with_client()
        memory.client.remember.return_value = {"done": True}
        memory.client.recall.return_value = {"evidence": []}
        candidate = self.candidate()
        with self.assertRaisesRegex(MemoryError, "no reference ID is fabricated"):
            memory.remember(candidate)  # write accepted, but recall must confirm the entry
        content = memory.client.remember.call_args.kwargs["content"]
        kwargs = memory.client.remember.call_args.kwargs
        self.assertEqual(kwargs["intent"], "lesson")
        self.assertEqual(kwargs["lesson_scope"], "run")
        self.assertTrue(kwargs["wait"])
        self.assertIn(f"[experiment:cc-test] [demo:{MEMORY_VERSION}]", content)
        memory.client.recall.return_value = {"evidence": [
            {"id": "real-1", "content": content},
            {"id": "real-2", "content": content.replace("[experiment:cc-test]",
                                                        "[experiment:cc-other]")},
            {"id": "real-3", "content": "not json at all"},
            {"content": content},
        ]}
        self.assertEqual(memory.remember(candidate), "real-1")
        recalled = memory.recall()
        self.assertEqual([lesson["id"] for lesson in recalled], ["real-1"])
        self.assertEqual(recalled[0]["category"], "contact_strategy")
        self.assertEqual(recalled[0]["source_execution_id"], "exec-rt")
        self.assertEqual(memory.client.recall.call_args.kwargs["entry_types"], ["lesson"])

    def test_reference_id_comes_from_real_recall_only(self):
        memory = self.memory_with_client()
        memory.client.remember.return_value = {"done": True}
        memory.client.recall.return_value = {"evidence": []}
        with self.assertRaisesRegex(MemoryError, "no reference ID is fabricated"):
            memory.remember(self.candidate())

    def test_write_and_transport_failures_raise_memory_error(self):
        memory = self.memory_with_client()
        memory.client.remember.return_value = {"error": "ingest failed"}
        with self.assertRaisesRegex(MemoryError, "did not finish ingesting"):
            memory.remember(self.candidate())
        memory.client.remember.side_effect = RuntimeError("secret-test-key boom")
        with patch.dict(os.environ, {"MUBIT_API_KEY": "secret-test-key"}):
            with self.assertRaises(MemoryError) as caught:
                memory.remember(self.candidate())
        self.assertNotIn("secret-test-key", str(caught.exception))

    def test_no_local_persistence_in_memory_module(self):
        source = Path(__file__).with_name("memory.py").read_text()
        for banned in ("open(", "Path(", ".demo", "trace"):
            self.assertNotIn(banned, source.replace("traceback", ""))


EVAL_TEXT = "am-pm-schedule-1"
E1_SMART = [d("outreach", channel="phone_call", local_time="18:00"),
            d("explain_instructions", text_id=EVAL_TEXT),
            d("check_understanding"),
            d("arrange_transportation"),
            d("book_follow_up", slot="2026-11-04T09:30"),
            d("close_encounter")]
E1_NAIVE = [d("outreach", channel="sms", local_time="10:00"),
            d("outreach", channel="phone_call", local_time="12:00"),
            d("outreach", channel="phone_call", local_time="18:00"),
            d("explain_instructions", text_id="generic-1"),
            d("check_understanding"),
            d("explain_instructions", text_id=EVAL_TEXT),
            d("check_understanding"),
            d("arrange_transportation")]
E2_OBEDIENT = [d("outreach", channel="phone_call", local_time="09:30"),
               d("explain_instructions", text_id=EVAL_TEXT),
               d("check_understanding"),
               d("arrange_transportation"),
               d("book_follow_up", slot="2026-11-13T10:00"),
               d("close_encounter")]
E2_STALE = [d("outreach", channel="phone_call", local_time="18:00"),
            d("outreach", channel="phone_call", local_time="09:30"),
            d("explain_instructions", text_id=EVAL_TEXT),
            d("check_understanding"),
            d("arrange_transportation"),
            d("book_follow_up", slot="2026-11-13T10:00"),
            d("close_encounter")]


def seeded_comparison_memory():
    return FakeMemory(stored=[seed_lesson("ref-contact", "contact_strategy"),
                              seed_lesson("ref-comm", "communication_strategy", "clinician_correction"),
                              seed_lesson("ref-coord", "coordination_sequence")])


def provider_factory(scripts):
    created = []

    def make(scenario_id, arm, repetition):
        script = scripts.get((scenario_id, arm, repetition)) or scripts[(scenario_id, arm)]
        provider = FakeProvider(deepcopy(script))
        created.append((scenario_id, arm, repetition, provider))
        return provider

    make.created = created
    return make


class EvaluationScenarioTests(unittest.TestCase):
    def test_evaluation_scenarios_pass_leakage_validation(self):
        for scenario_id in ("E1", "E2"):
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(validate_evaluation_scenario(scenario_id), [])
                blob = json.dumps(SCENARIOS[scenario_id]["chart"])
                for banned in ("17:00", "morning-evening-1", "teaching", "answered_from",
                               "patient_responds", "understanding_requires"):
                    self.assertNotIn(banned, blob)
                self.assertFalse(SCENARIOS[scenario_id]["initial_flags"])
                self.assertIsNone(SCENARIOS[scenario_id]["scripted_correction"])

    def test_E1_requires_full_chain_with_new_appointment_details(self):
        scenario = SCENARIOS["E1"]
        self.assertEqual(scenario["objectives"], ["contact_established", "understanding_confirmed",
                                                  "transportation_arranged", "follow_up_booked"])
        self.assertNotEqual(scenario["chart"]["follow_up"]["available_slots"],
                            SCENARIOS["T3"]["chart"]["follow_up"]["available_slots"])

    def test_E1_communication_uses_current_chart_text_without_correction(self):
        encounter = start_encounter("E1", experiment_id="cc-test")
        self.assertEqual(encounter.act(LATE_CALL)["status"], "success")
        encounter.act({"name": "explain_instructions", "text_id": GENERIC_TEXT_ID})
        self.assertEqual(encounter.act(CHECK)["status"], "not_confirmed")
        encounter.act(EXPLAIN_EVAL)
        self.assertEqual(encounter.act(CHECK)["status"], "confirmed")
        self.assertEqual(len(encounter.chart()["instructions"]["patient_education_texts"]), 2)

    def test_E2_current_instruction_window(self):
        encounter = start_encounter("E2", experiment_id="cc-test")
        self.assertEqual(encounter.act(LATE_CALL)["status"], "no_response")
        self.assertEqual(encounter.act(MORNING_CALL)["status"], "success")
        instruction = encounter.chart()["patient"]["current_contact_instruction"]
        self.assertEqual(instruction["window"], {"from": "09:00", "to": "11:00"})

    def test_base_patient_answer_window_unchanged(self):
        encounter = start_encounter("T1", experiment_id="cc-test")
        self.assertEqual(encounter.act(CALL_2000)["status"], "success")

    def test_current_approved_text_comes_from_chart_not_memory(self):
        chart = SCENARIOS["E1"]["chart"]
        texts = {t["text_id"] for t in chart["instructions"]["patient_education_texts"]}
        self.assertIn(EVAL_TEXT, texts)
        self.assertEqual(chart["instructions"]["current_approved_text_id"], EVAL_TEXT)
        self.assertNotIn(EVAL_TEXT, json.dumps([seed_lesson("ref-contact")]))
        provider = FakeProvider(deepcopy(E1_SMART))
        run = run_encounter(provider, "E1", experiment_id="cc-test", execution_id="exec-e",
                            encounter_id="enc-e", memory_mode="frozen", frozen_lessons=[])
        self.assertEqual(run["result"]["status"], "completed")

    def test_starting_state_hash_is_deterministic(self):
        for scenario_id in ("E1", "E2"):
            with self.subTest(scenario_id=scenario_id):
                first = starting_state_hash(starting_state_view(scenario_id))
                second = starting_state_hash(starting_state_view(scenario_id))
                self.assertEqual(first, second)
                self.assertEqual(len(first), 64)


class ComparisonHarnessTests(unittest.TestCase):
    def default_scripts(self, overrides=None):
        scripts = {
            ("E1", "full_memory"): deepcopy(E1_SMART),
            ("E1", "no_memory"): deepcopy(E1_NAIVE),
            ("E1", "ablated_contact"): deepcopy(E1_NAIVE),
            ("E2", "full_memory"): deepcopy(E2_OBEDIENT),
            ("E2", "no_memory"): deepcopy(E2_OBEDIENT),
            ("E2", "ablated_contact"): deepcopy(E2_OBEDIENT),
        }
        if overrides:
            scripts.update(overrides)
        return scripts

    def compare(self, scripts=None, memory=None, repetitions=1):
        self.memory = memory or seeded_comparison_memory()
        self.factory = provider_factory(scripts or self.default_scripts())
        self.report = run_comparison(self.factory, self.memory, "cc-cmp", repetitions=repetitions)
        return self.report

    def scenario(self, scenario_id):
        return next(s for s in self.report["scenarios"] if s["scenario_id"] == scenario_id)

    def test_base_memory_recalled_exactly_once(self):
        self.compare()
        self.assertEqual([call[0] for call in self.memory.calls], ["recall"])

    def test_arms_derive_from_one_frozen_base(self):
        self.compare()
        base = normalize_for_prompt(self.memory.recall())
        base_hash = snapshot_hash(base)
        self.assertEqual(self.report["status"], "completed")
        self.assertEqual(self.report["base_memory"]["sha256"], base_hash)
        definitions = self.report["arm_definitions"]
        self.assertEqual(definitions["no_memory"]["lesson_ids"], [])
        self.assertEqual(definitions["full_memory"]["lesson_ids"], [l["id"] for l in base])
        self.assertEqual(definitions["full_memory"]["sha256"], base_hash)
        self.assertEqual(definitions["no_memory"]["source_base_sha256"], base_hash)
        self.assertEqual(definitions["ablated_contact"]["source_base_sha256"], base_hash)

    def test_ablation_removes_only_contact_and_keeps_others_identical(self):
        self.compare()
        self.assertEqual(self.report["base_memory"]["ablated_contact_lesson_ids"], ["ref-contact"])
        arms = self.report["arm_definitions"]
        self.assertNotIn("ref-contact", arms["ablated_contact"]["lesson_ids"])
        full = self.scenario("E1")["runs"]
        by_arm = {run["arm"]: run for run in full}
        comm_coord = lambda run: [i for i in run["available_lesson_ids"] if i != "ref-contact"]
        self.assertEqual(comm_coord(by_arm["full_memory"]), comm_coord(by_arm["ablated_contact"]))
        self.assertNotEqual(arms["full_memory"]["sha256"], arms["ablated_contact"]["sha256"])

    def test_ablation_does_not_mutate_persistent_memory(self):
        memory = seeded_comparison_memory()
        before = deepcopy(memory.server)
        self.compare(memory=memory)
        self.assertEqual(memory.server, before)
        self.assertEqual(memory.recall(), before)

    def test_missing_contact_lesson_invalidates_comparison(self):
        memory = FakeMemory(stored=[seed_lesson("ref-comm", "communication_strategy"),
                                    seed_lesson("ref-coord", "coordination_sequence")])
        report = self.compare(memory=memory)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("contact_strategy", report["reason"])

    def test_recall_failure_invalidates_comparison(self):
        report = self.compare(memory=FakeMemory(fail_recall=True))
        self.assertEqual(report["status"], "invalid")
        self.assertIn("recall", report["reason"])
        self.assertEqual(self.factory.created, [])

    def test_comparison_performs_zero_memory_writes(self):
        memory = seeded_comparison_memory()
        self.compare(memory=memory)
        self.assertEqual([call[0] for call in memory.calls], ["recall"])

    def test_starting_state_identical_across_arms(self):
        self.compare()
        for scenario_id in ("E1", "E2"):
            with self.subTest(scenario_id=scenario_id):
                expected = starting_state_hash(starting_state_view(scenario_id))
                scenario = self.scenario(scenario_id)
                self.assertEqual(scenario["starting_state_sha256"], expected)
                hashes = {run["starting_state_sha256"] for run in scenario["runs"]}
                self.assertEqual(hashes, {expected})

    def test_behavioral_provider_failure_is_preserved(self):
        scripts = self.default_scripts({("E1", "full_memory"): [ProviderError("TEST FIXTURE: outage")]})
        report = self.compare(scripts=scripts)
        self.assertEqual(report["status"], "completed")
        full = next(run for run in self.scenario("E1")["runs"] if run["arm"] == "full_memory")
        self.assertEqual(full["status"], "failed")
        self.assertIn("TEST FIXTURE", full["error"])
        verdicts = self.scenario("E1")["comparison"]["pairwise"]["full_vs_none"]
        self.assertEqual(verdicts["overall"], "regression")

    def test_provider_settings_identical_across_arms(self):
        self.compare()
        models = {run["model"] for scenario in self.report["scenarios"] for run in scenario["runs"]}
        self.assertEqual(models, {"test-only"})
        self.assertEqual(self.report["provider"], "test-only")
        self.assertEqual(self.report["temperature"], 0)
        self.assertEqual(self.report["max_step_attempts"], MAX_STEP_ATTEMPTS)

    def test_repetitions_reuse_frozen_memory_and_starting_state(self):
        scripts = self.default_scripts()
        self.compare(scripts=scripts, repetitions=2)
        self.assertEqual([call[0] for call in self.memory.calls], ["recall"])
        for scenario_id in ("E1", "E2"):
            scenario = self.scenario(scenario_id)
            self.assertEqual(len(scenario["runs"]), len(ARMS) * 2)
            self.assertEqual(len({run["starting_state_sha256"] for run in scenario["runs"]}), 1)
            for arm in ARMS:
                arm_runs = [run for run in scenario["runs"] if run["arm"] == arm]
                self.assertEqual(len({run["memory_sha256"] for run in arm_runs}), 1)
                self.assertEqual([run["repetition"] for run in arm_runs], [1, 2])
        self.assertEqual(len({run["execution_id"] for s in self.report["scenarios"]
                              for run in s["runs"]}), len(ARMS) * 2 * 2)

    def test_report_contains_real_ids_and_hashes_but_no_secrets(self):
        self.compare()
        blob = json.dumps(self.report)
        for reference in ("ref-contact", "ref-comm", "ref-coord"):
            self.assertIn(reference, blob)
        self.assertEqual(self.report["base_memory"]["sha256"],
                         snapshot_hash(normalize_for_prompt(self.memory.recall())))
        for secret in ("MUBIT_API_KEY", "GEMINI_API_KEY", "mbt_local_admin_secret", "api_key="):
            self.assertNotIn(secret, blob)

    def test_hidden_state_cannot_enter_report_or_no_memory_prompts(self):
        scripts = self.default_scripts()
        factory = provider_factory(scripts)
        memory = seeded_comparison_memory()
        report = run_comparison(factory, memory, "cc-cmp")
        blob = json.dumps(report)
        for banned in ("17:00", "answered_from", "answered_until", "patient_responds",
                       "understanding_requires", "booking_requires_arranged_ride",
                       "teaching_goal", "morning-evening-1"):
            self.assertNotIn(banned, blob)
        no_memory_payloads = json.dumps([provider.calls[0][1]
                                         for scenario_id, arm, _, provider in factory.created
                                         if arm == "no_memory"])
        for banned in ("previously established contact", "previously resolved",
                       "morning-evening-1", "17:00"):
            self.assertNotIn(banned, no_memory_payloads)

    def test_full_vs_ablated_detects_contact_behavior_change(self):
        scripts = self.default_scripts({("E1", "ablated_contact"): deepcopy(E1_NAIVE),
                                        ("E1", "full_memory"): deepcopy(E1_SMART)})
        self.compare(scripts=scripts)
        scenario = self.scenario("E1")
        self.assertTrue(scenario["comparison"]["interpretation"]["contact_behavior_changed_by_ablation"])
        self.assertEqual(scenario["comparison"]["pairwise"]["full_vs_ablated"]["overall"], "win")


class ScoringTests(unittest.TestCase):
    def scored(self, scenario_id, script, frozen=None):
        provider = FakeProvider(deepcopy(script))
        run = run_encounter(provider, scenario_id, experiment_id="cc-test",
                            execution_id="exec-score", encounter_id="enc-score",
                            memory_mode="frozen",
                            frozen_lessons=frozen if frozen is not None else [])
        return score_run(run, starting_state_view(scenario_id)), run

    def test_completion_and_coordination_metrics(self):
        metrics, _ = self.scored("E1", E1_SMART)
        self.assertTrue(metrics["completed"])
        self.assertTrue(metrics["comprehension_confirmed"])
        self.assertTrue(metrics["transport_confirmed_booking"])
        self.assertEqual(metrics["unsuccessful_outreach_before_contact"], 0)
        self.assertEqual(metrics["premature_booking_attempts"], 0)
        self.assertEqual(metrics["failed_comprehension_checks"], 0)
        self.assertEqual(metrics["actions_used"], 6)
        self.assertEqual(metrics["cited_lesson_ids"], [])

    def test_unsuccessful_outreach_count_and_incompletion(self):
        metrics, _ = self.scored("E1", E1_NAIVE)
        self.assertFalse(metrics["completed"])
        self.assertEqual(metrics["unsuccessful_outreach_before_contact"], 2)
        self.assertTrue(metrics["comprehension_confirmed"])
        self.assertFalse(metrics["transport_confirmed_booking"])

    def test_premature_booking_is_detected(self):
        script = [d("outreach", channel="phone_call", local_time="18:00"),
                  d("book_follow_up", slot="2026-11-04T09:30"),
                  d("arrange_transportation"),
                  d("book_follow_up", slot="2026-11-04T09:30"),
                  d("explain_instructions", text_id=EVAL_TEXT),
                  d("check_understanding"),
                  d("close_encounter")]
        metrics, _ = self.scored("E1", script)
        self.assertTrue(metrics["completed"])
        self.assertEqual(metrics["premature_booking_attempts"], 1)
        self.assertTrue(metrics["transport_confirmed_booking"])

    def test_comprehension_failures_counted(self):
        script = [d("outreach", channel="phone_call", local_time="18:00"),
                  d("explain_instructions", text_id="generic-1"),
                  d("check_understanding"),
                  d("explain_instructions", text_id=EVAL_TEXT),
                  d("check_understanding"),
                  d("arrange_transportation"),
                  d("book_follow_up", slot="2026-11-04T09:30"),
                  d("close_encounter")]
        metrics, _ = self.scored("E1", script)
        self.assertTrue(metrics["completed"])
        self.assertEqual(metrics["failed_comprehension_checks"], 1)
        self.assertTrue(metrics["current_approved_text_used"])

    def test_current_instruction_override_metrics(self):
        obedient, _ = self.scored("E2", E2_OBEDIENT)
        self.assertTrue(obedient["followed_current_instruction"])
        self.assertEqual(obedient["stale_evening_calls"], [])
        self.assertFalse(obedient["memory_overrode_current_instruction"])
        stale, _ = self.scored("E2", E2_STALE)
        self.assertTrue(stale["followed_current_instruction"])
        self.assertEqual(stale["stale_evening_calls"], ["18:00"])
        self.assertTrue(stale["memory_overrode_current_instruction"])
        failed = [d("outreach", channel="phone_call", local_time="18:00"),
                  d("outreach", channel="phone_call", local_time="19:00"),
                  d("escalate")]
        missed, _ = self.scored("E2", failed)
        self.assertFalse(missed["followed_current_instruction"])
        self.assertEqual(len(missed["stale_evening_calls"]), 2)

    def test_citations_do_not_affect_scoring(self):
        lessons = [normalize_for_prompt([seed_lesson("ref-contact")])[0]]
        citing = deepcopy(E1_SMART)
        citing[0]["recalled_lesson_ids"] = ["ref-contact"]
        with_citation, _ = self.scored("E1", citing, frozen=lessons)
        without_citation, _ = self.scored("E1", E1_SMART, frozen=lessons)
        self.assertEqual(with_citation["cited_lesson_ids"], ["ref-contact"])
        self.assertEqual(without_citation["cited_lesson_ids"], [])
        self.assertEqual(rubric(with_citation), rubric(without_citation))
        self.assertEqual(verdict(with_citation, without_citation), "tie")

    def test_pairwise_win_tie_and_regression_logic(self):
        completed = {"completed": True, "comprehension_confirmed": True,
                     "transport_confirmed_booking": True, "unsuccessful_outreach_before_contact": 0,
                     "premature_booking_attempts": 0, "actions_used": 6,
                     "followed_current_instruction": None, "stale_evening_calls": []}
        worse = dict(completed, unsuccessful_outreach_before_contact=2)
        self.assertEqual(verdict(completed, worse), "win")
        self.assertEqual(verdict(completed, dict(completed)), "tie")
        self.assertEqual(verdict(worse, completed), "regression")
        self.assertIn("unsuccessful outreach", verdict_reason(completed, worse))
        self.assertEqual(verdict_reason(completed, dict(completed)),
                         "identical deterministic metrics")

    def test_frozen_mode_requires_snapshot_and_none_mode_validated(self):
        self.assertRaises(ValueError, run_encounter, FakeProvider([]), "E1",
                          experiment_id="cc-test", memory_mode="frozen")


TEACH_SCRIPTS = {
    "T1": [d("outreach", channel="sms", local_time="10:00"),
           d("outreach", channel="phone_call", local_time="12:00"),
           d("outreach", channel="phone_call", local_time="18:00"),
           d("close_encounter")],
    "T2": [d("explain_instructions", text_id="generic-1"),
           d("check_understanding"),
           d("explain_instructions", text_id="morning-evening-1"),
           d("check_understanding"),
           d("close_encounter")],
    "T3": [d("book_follow_up", slot="2026-10-21T11:30"),
           d("arrange_transportation"),
           d("book_follow_up", slot="2026-10-21T11:30"),
           d("close_encounter")],
}


class DemoFakeProvider:
    """Test-only provider that plays deterministic paths chosen from the payload."""

    model = "test-only"

    def __init__(self):
        self.calls = []

    def generate(self, system, payload, schema):
        self.calls.append(payload)
        scenario_id = payload["chart"]["encounter"]["scenario_id"]
        if scenario_id in TEACH_SCRIPTS:
            script = TEACH_SCRIPTS[scenario_id]
        elif scenario_id == "E1":
            categories = {lesson["category"] for lesson in payload.get("recalled_operational_lessons") or []}
            script = E1_SMART if "contact_strategy" in categories else E1_NAIVE
        else:
            script = E2_OBEDIENT
        index = payload["visible_state"]["actions_used"]
        if index >= len(script):
            raise ProviderError("TEST FIXTURE: script exhausted")
        decision = script[index]
        return Decision.model_validate(dict(decision)), dict(USAGE_STUB), json.dumps(decision)

    def close(self):
        pass


class ServerTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        self.tmp = tempfile.TemporaryDirectory()
        self.durable = []
        self.all_calls = []
        self.memory_kwargs = {}
        self.data_patch = patch.object(app, "DATA", Path(self.tmp.name))
        self.data_patch.start()
        self.env_patch = patch.dict(os.environ, {
            "GEMINI_API_KEY": "gemini-test-key", "GEMINI_MODEL": "",
            "MUBIT_ENDPOINT": "http://127.0.0.1:3000", "MUBIT_API_KEY": "mubit-test-key",
            "DEMO_EXPERIMENT": ""})
        self.env_patch.start()
        self.memory_patch = patch.object(app, "Memory", self.make_memory)
        self.memory_patch.start()
        self.provider_patch = patch.object(app, "Gemini", DemoFakeProvider)
        self.provider_patch.start()
        app.active = None
        from fastapi.testclient import TestClient
        self.client = TestClient(app.app)

    def make_memory(self, experiment):
        return FakeMemory(experiment, stored=self.durable, calls_sink=self.all_calls,
                          **self.memory_kwargs)

    def rebind_memory(self):
        self.memory_patch.stop()
        self.memory_patch = patch.object(self.app, "Memory", self.make_memory)
        self.memory_patch.start()

    def tearDown(self):
        self.client.close()
        self.provider_patch.stop()
        self.memory_patch.stop()
        self.env_patch.stop()
        self.data_patch.stop()
        self.tmp.cleanup()
        self.app.active = None

    def run_phase(self, phase, repetitions=1, run_id=None):
        run_id = run_id or ("a" * 32 if phase == "teach" else "b" * 32)
        trace = dict(id=run_id, kind=phase, phase=phase, experiment="cc-server-test",
                     repetitions=repetitions, status="running", events=[])
        self.app.save(self.app.trace_path(run_id), trace)
        self.app.execute(trace)
        return trace

    def test_status_reports_sanitized_endpoint_and_never_secrets(self):
        with patch.dict(os.environ, {"MUBIT_ENDPOINT": "http://user:pw@127.0.0.1:3000/x?q=1"}):
            status = self.client.get("/api/status").json()
        self.assertEqual(status["memory"]["endpoint"], "http://127.0.0.1:3000")
        self.assertEqual(status["memory"]["status"], "connected")
        blob = json.dumps(status)
        for secret in ("mubit-test-key", "gemini-test-key", "user:pw", "?q=1"):
            self.assertNotIn(secret, blob)

    def test_new_experiment_creates_isolated_ids_and_validates_format(self):
        first = self.client.post("/api/experiment", json={}).json()["experiment"]
        second = self.client.post("/api/experiment", json={}).json()["experiment"]
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^cc-demo-\d{8}-[0-9a-f]{6}$")
        selected = self.client.post("/api/experiment",
                                    json={"experiment": "cc-existing-1"}).json()["experiment"]
        self.assertEqual(selected, "cc-existing-1")
        self.assertEqual(self.client.post("/api/experiment",
                                          json={"experiment": "../bad"}).status_code, 422)

    def test_teaching_lifecycle_via_endpoint_and_event_order(self):
        self.client.post("/api/experiment", json={"experiment": "cc-server-test"})
        with patch.object(self.app.threading, "Thread") as worker:
            response = self.client.post("/api/runs", json={"phase": "teach"})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(worker.return_value.start.call_count, 1)
        self.app.active = None
        trace = self.run_phase("teach")
        self.assertEqual(trace["status"], "completed")
        ids = [event["id"] for event in trace["events"]]
        self.assertEqual(ids, sorted(ids) and list(range(1, len(ids) + 1)))
        kinds = [event["type"] for event in trace["events"]]
        self.assertIn("encounter_started", kinds)
        self.assertIn("clinician_correction", kinds)
        self.assertIn("memory_write_finished", kinds)
        self.assertIn("lessons_refreshed", kinds)
        self.assertEqual(trace["encounters"][0]["status"], "completed")
        self.assertEqual(len(trace["encounters"]), 3)
        for encounter in trace["encounters"]:
            self.assertTrue(self.app.trace_path(encounter["execution_id"]).exists())
        lessons = self.client.get("/api/lessons").json()
        self.assertEqual(sorted(l["id"] for l in lessons["lessons"]),
                         ["mubit-ref-1", "mubit-ref-2", "mubit-ref-3"])
        self.assertEqual(sorted(l["category"] for l in lessons["lessons"]),
                         ["communication_strategy", "contact_strategy", "coordination_sequence"])

    def test_lesson_ids_appear_only_after_successful_write(self):
        self.memory_kwargs = {"fail_write": True}
        self.rebind_memory()
        trace = self.run_phase("teach", run_id="c" * 32)
        self.assertEqual(trace["status"], "failed")
        kinds = [event["type"] for event in trace["events"]]
        self.assertIn("memory_write_failed", kinds)
        self.assertNotIn("memory_write_finished", kinds)
        self.assertEqual(self.client.get("/api/lessons").json()["lessons"], [])
        partial = self.client.get(f"/api/runs/{trace['id']}")
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.json()["status"], "failed")

    def test_lessons_endpoint_reads_through_to_memory_without_local_copy(self):
        self.client.post("/api/experiment", json={"experiment": "cc-server-test"})
        self.assertEqual(self.client.get("/api/lessons").json()["count"], 0)
        self.durable.append({**seed_lesson("ref-late", "contact_strategy"),
                             "experiment_id": "cc-server-test"})
        refreshed = self.client.get("/api/lessons").json()
        self.assertEqual(refreshed["count"], 1)
        self.assertEqual(refreshed["lessons"][0]["id"], "ref-late")

    def test_comparison_uses_harness_once_and_stays_read_only(self):
        self.run_phase("teach", run_id="d" * 32)
        before = len(self.all_calls)
        trace = self.run_phase("compare", run_id="e" * 32)
        self.assertEqual(trace["status"], "completed")
        self.assertEqual(trace["report"]["status"], "completed")
        comparison_calls = [call[0] for call in self.all_calls[before:]]
        self.assertEqual(comparison_calls.count("recall"), 1)  # exactly one base recall
        self.assertNotIn("remember", comparison_calls)          # zero writes
        self.assertNotIn("record", comparison_calls)            # zero outcome writes
        self.assertEqual(len(self.durable), 3)                  # store untouched by evaluation

    def test_comparison_report_download_and_ablation_fields(self):
        self.run_phase("teach", run_id="f" * 32)
        trace = self.run_phase("compare", run_id="1" * 32)
        response = self.client.get(f"/api/runs/{trace['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        report = response.json()["report"]
        self.assertEqual(report["base_memory"]["ablated_contact_lesson_ids"], ["mubit-ref-1"])
        self.assertEqual(report["status"], "completed")
        e2 = next(s for s in report["scenarios"] if s["scenario_id"] == "E2")
        self.assertIsInstance(e2["comparison"]["current_instruction_override"]["passed"], bool)

    def test_busy_lock_rejects_overlap_deterministically(self):
        self.app.active = "busy"
        for body in ({"phase": "teach"}, {"phase": "compare"}):
            response = self.client.post("/api/runs", json=body)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"], "A run is already active")
        self.assertEqual(self.client.post("/api/experiment", json={}).status_code, 409)
        self.app.active = None

    def test_provider_failure_surfaces_and_partial_trace_downloadable(self):
        class BrokenProvider:
            model = "test-only"

            def generate(self, *args):
                raise ProviderError("Gemini HTTP 503: TEST FIXTURE outage")

            def close(self):
                pass

        with patch.object(self.app, "Gemini", BrokenProvider):
            trace = self.run_phase("teach", run_id="2" * 32)
        self.assertEqual(trace["status"], "failed")
        errors = [event for event in trace["events"] if event["type"] == "run_error"]
        self.assertIn("Gemini HTTP 503", errors[0]["message"])
        partial = self.client.get(f"/api/runs/{trace['id']}")
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.json()["status"], "failed")

    def test_mubit_recall_failure_invalidates_comparison_explicitly(self):
        self.run_phase("teach", run_id="3" * 32)
        self.memory_kwargs = {"fail_recall": True}
        self.rebind_memory()
        trace = self.run_phase("compare", run_id="4" * 32)
        self.assertEqual(trace["status"], "failed")
        finished = [e for e in trace["events"] if e["type"] == "comparison_finished"]
        self.assertEqual(finished[0]["status"], "invalid")
        self.assertIn("recall", str(finished[0]["reason"]))
        status = self.client.get("/api/status").json()
        self.assertEqual(status["memory"]["status"], "unavailable")

    def test_missing_configuration_blocks_runs(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            response = self.client.post("/api/runs", json={"phase": "teach"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("GEMINI_API_KEY", response.json()["detail"])

    def test_trace_download_validates_identifiers(self):
        for bad in ("abc", "z" * 32, "../state.json", "%2E%2E%2Fstate.json", "a" * 33):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.get(f"/api/runs/{bad}").status_code, 404)

    def test_api_responses_never_expose_hidden_state_or_secrets(self):
        self.run_phase("teach", run_id="5" * 32)
        self.run_phase("compare", run_id="6" * 32)
        surfaces = json.dumps([
            self.client.get("/api/status").json(),
            self.client.get("/api/lessons").json(),
            self.client.get(f"/api/runs/{'5' * 32}").json(),
            self.client.get(f"/api/runs/{'6' * 32}").json(),
        ])
        for marker in FORBIDDEN_VISIBLE_SUBSTRINGS:
            self.assertNotIn(marker, surfaces)
        for secret in ("mubit-test-key", "gemini-test-key"):
            self.assertNotIn(secret, surfaces)

    def test_restart_retrieves_lessons_from_mubit_without_trace_files(self):
        self.client.post("/api/experiment", json={"experiment": "cc-server-test"})
        self.run_phase("teach", run_id="7" * 32)
        self.assertEqual(self.client.get("/api/lessons").json()["count"], 3)
        self.app.active = None  # Simulated server restart: no in-process state remains.
        for path in (Path(self.tmp.name) / "runs").glob("*.json"):
            path.unlink()  # Traces are audit artifacts only; memory must not need them.
        self.client.post("/api/experiment", json={"experiment": "cc-server-test"})
        lessons = self.client.get("/api/lessons").json()
        self.assertEqual(lessons["count"], 3)
        self.assertEqual(sorted(l["id"] for l in lessons["lessons"]),
                         ["mubit-ref-1", "mubit-ref-2", "mubit-ref-3"])

    def test_interrupted_running_trace_is_labeled(self):
        trace = dict(id="9" * 32, kind="teach", phase="teach", experiment="cc-server-test",
                     status="running", events=[])
        self.app.save(self.app.trace_path(trace["id"]), trace)
        read = self.client.get(f"/api/runs/{trace['id']}").json()
        self.assertEqual(read["status"], "interrupted")

    def test_sse_stream_replays_and_terminates(self):
        self.run_phase("teach", run_id="8" * 32)
        stream = self.client.get(f"/api/runs/{'8' * 32}/events", headers={"Last-Event-ID": "2"})
        self.assertNotIn("id: 2\n", stream.text)
        self.assertIn("event: end", stream.text)
        self.assertIn("run_finished", stream.text)

    def test_cross_origin_posts_are_rejected(self):
        response = self.client.post("/api/runs", json={"phase": "teach"},
                                    headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_no_diagnosis_or_treatment_routes_exist(self):
        post_paths = {getattr(route, "path", "") for route in self.app.app.routes
                      if "POST" in getattr(route, "methods", set())}
        self.assertTrue(post_paths <= {"/api/experiment", "/api/runs"})
        for route in self.app.app.routes:
            path = getattr(route, "path", "")
            for banned in ("diagnos", "treat", "prescri", "medicat"):
                self.assertNotIn(banned, path)

    def test_page_renders_measured_results_only_and_labels_synthetic(self):
        source = Path(__file__).with_name("index.html").read_text()
        for banned in (">WIN<", ">PASS<", "3/3", "0/3", ">TIE<"):
            self.assertNotIn(banned, source)
        for required in ("All patient data is synthetic", "Coordination only",
                         "learned operational lessons", "not current EHR facts",
                         "Contact lesson removed", "not absolute causal proof"):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
