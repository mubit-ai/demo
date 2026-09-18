"""Offline boundary checks. Only a live server can verify Mubit lesson extraction."""
import contextlib
import io
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from demo import Memory, TRAIN, HELD_OUT, TRUTH, baseline_plan, decide, handle, run_case, symptom_class


def lesson_entry(lesson_id, run_id="state::actor::oncall-agent-v1-test"):
    return dict(id=lesson_id, content="Checkout deploy-day error spikes need dependency restarts",
                entry_type="lesson", run_id=run_id)


class DemoTest(unittest.TestCase):
    def test_launcher_shares_experiment_and_stops_on_failure(self):
        launch = runpy.run_path(str(Path(__file__).with_name("__main__.py")))["main"]
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launch(), 0)
            commands = [c.args[0] for c in run.call_args_list]
            self.assertEqual([c[2] for c in commands], ["teach", "evaluate"])
            self.assertEqual(commands[0][-1], commands[1][-1])
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=1)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launch(), 1)
            run.assert_called_once()

    def test_baseline_is_wrong_for_teach_cases_and_right_for_controls(self):
        for case in TRAIN:
            plan = baseline_plan(case[2])
            truth = TRUTH[(case[1], symptom_class(case[2]))]
            self.assertNotEqual(plan["fix"], truth["fix"], case[0])
        for case in HELD_OUT[3:]:
            plan = baseline_plan(case[2])
            truth = TRUTH[(case[1], symptom_class(case[2]))]
            self.assertEqual(plan["fix"], truth["fix"], case[0])

    def test_teach_pass_one_instruments_then_pass_two_attributes(self):
        client = Mock()
        client.remember.return_value = {"status": "completed"}
        client.record_step_outcome.return_value = {"accepted": True}
        client.recall.return_value = {"evidence": []}
        client.advanced.reflect.return_value = {
            "lessons_stored": 1, "degraded": False,
            "lessons": [{"lesson_id": "server-id", "content": "Server-generated guidance"}]}
        memory = Memory(client, "test")
        with contextlib.redirect_stdout(io.StringIO()):
            for case in TRAIN:  # pass 1: cold policy, no lessons to cite yet
                decision = decide(case, [])
                memory.learn(case, decision, handle(case, decision), f"exec1/{case[0]}")
                self.assertIsNone(memory.attribute(decision, handle(case, decision), f"exec1/{case[0]}"))
            memory.reflect()
            # pass 2: lessons now exist; the decision cites them and the verdict is attributed.
            client.recall.return_value = {"evidence": [lesson_entry("L1")]}
            model = Mock()
            # per teaching class, the plan the lessons would teach (matches hidden truth)
            taught = {"T1": ("dependency_health", "restart_dependency"),
                      "T2": ("recent_deploys", "rollback"),
                      "T3": ("infra_capacity", "scale_out")}
            for case in TRAIN:
                probe, fix = taught[case[0]]
                model.models.generate_content.return_value = SimpleNamespace(text=json.dumps(dict(
                    probes=[probe], fix=fix, lesson_ids=["L1"], reason="lesson matches this incident class")))
                decision, outcome = run_case(case, memory, learn=True,
                                             execution="exec2", model=model)
                self.assertEqual(decision["lesson_ids"], ["L1"])
                self.assertTrue(outcome["resolved"], case[0])
        # 6 attempts observed (2 passes x 3): pass 1 uses the 2-probe baseline plan
        # (3 steps/case), pass 2 applies a 1-probe plan (2 steps/case): 9 + 6 = 15.
        self.assertEqual(client.remember.call_count, 6)
        self.assertEqual(client.record_step_outcome.call_count, 15)
        for call in client.remember.call_args_list:
            self.assertEqual(call.kwargs["intent"], "fact")
            self.assertTrue(call.kwargs["wait"])
        # Pass 2 attributed every verdict to the cited entries, with multi-entry + idempotency.
        outcomes = client.record_outcome.call_args_list
        self.assertEqual(len(outcomes), 3)
        for call in outcomes:
            self.assertEqual(call.kwargs["reference_id"], "L1")
            self.assertEqual(call.kwargs["entry_ids"], ["L1"])
            self.assertEqual(call.kwargs["outcome"], "success")
            self.assertEqual(call.kwargs["signal"], 1.0)
            self.assertTrue(call.kwargs["idempotency_key"].startswith("oncall-agent-v1:"))
            self.assertFalse(call.kwargs["verified_in_production"])
        client.advanced.reflect.assert_called_once_with(dict(
            run_id="oncall-agent-v1-test", include_linked_runs=False, include_step_outcomes=True))

    def test_step_outcomes_carry_observational_signals_only(self):
        client = Mock()
        client.remember.return_value = {"status": "completed"}
        client.record_step_outcome.return_value = {"accepted": True}
        memory = Memory(client, "test")
        case = TRAIN[0]  # checkout deploy_error: informative probe is dependency_health
        decision = dict(probes=["recent_deploys", "dependency_health"], fix="rollback",
                        lesson_ids=[], reason="baseline")
        outcome = handle(case, decision)
        with contextlib.redirect_stdout(io.StringIO()):
            memory.learn(case, decision, outcome, "exec/T1")
        calls = client.record_step_outcome.call_args_list
        self.assertEqual([c.kwargs["outcome"] for c in calls], ["neutral", "success", "failure"])
        self.assertEqual([c.kwargs["signal"] for c in calls], [0.0, 1.0, -1.0])
        self.assertIsNone(calls[1].kwargs["directive_hint"])  # informative probe: no hint needed
        self.assertIn("uninformative", calls[0].kwargs["directive_hint"])
        # Hints are observational; neither may name the hidden correct fix or probe.
        for call in calls:
            hint = call.kwargs["directive_hint"] or ""
            truth = TRUTH[("checkout", "deploy_error")]
            self.assertNotIn(truth["fix"], hint)
        self.assertIn("did not resolve", calls[2].kwargs["directive_hint"])

    def test_retrieval_is_scoped_and_decision_has_no_outcome(self):
        client = Mock()
        entry = lesson_entry("lesson-1")
        client.recall.return_value = {"evidence": [entry, dict(entry, id="stale", is_stale=True),
            dict(entry, id="other", run_id="oncall-agent-v0-other"), dict(entry, entry_type="fact")]}
        memory = Memory(client, "test")
        self.assertEqual(memory.recall("checkout errors after deploy"),
                         [{"id": "lesson-1", "content": entry["content"], "conditions": []}])
        args = client.recall.call_args.kwargs
        self.assertEqual(args["entry_types"], ["lesson"])
        self.assertTrue(args["evidence_only"])
        self.assertFalse(args["include_working_memory"])
        model = Mock()
        model.models.generate_content.return_value = SimpleNamespace(text=json.dumps(dict(
            probes=["dependency_health"], fix="restart_dependency",
            lesson_ids=["lesson-1"], reason="applies")))
        with contextlib.redirect_stdout(io.StringIO()):
            run_case(HELD_OUT[0], memory, model=model)
        payload = json.loads(model.models.generate_content.call_args.kwargs["contents"])
        self.assertEqual(set(payload), {"incident", "baseline", "lessons"})
        client.remember.assert_not_called()
        client.record_step_outcome.assert_not_called()
        client.record_outcome.assert_not_called()
        client.advanced.reflect.assert_not_called()
        # An override citing an ID that was never supplied is rejected, not trusted.
        model.models.generate_content.return_value.text = json.dumps(dict(
            probes=["dependency_health"], fix="scale_out", lesson_ids=["invented"], reason="no"))
        with self.assertRaises(ValueError):
            decide(HELD_OUT[0], memory.recall(HELD_OUT[0][2]), model)

    def test_failed_reflection_and_cold_arms(self):
        client = Mock()
        client.advanced.reflect.return_value = {"lessons": [], "lessons_stored": 0}
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            Memory(client, "test").reflect()
        cold = decide(HELD_OUT[0], [])
        self.assertIn(cold["fix"], ("rollback", "restart_dependency", "scale_out"))
        model = Mock()
        decide(HELD_OUT[0][2] if False else HELD_OUT[0], [], model)
        model.models.generate_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
