"""Offline boundary checks. Only a live server can verify Mubit lesson extraction."""
import contextlib
import io
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from demo import Memory, TRAIN, HELD_OUT, handle, route, run_case


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

    def test_instrumentation_precedes_sdk_reflection(self):
        client = Mock()
        client.remember.return_value = {"status": "completed"}
        client.record_step_outcome.return_value = {"accepted": True}
        client.advanced.reflect.return_value = {
            "lessons_stored": 1, "degraded": False,
            "lessons": [{"lesson_id": "server-id", "content": "Server-generated guidance"}]}
        memory = Memory(client, "test")
        with contextlib.redirect_stdout(io.StringIO()):
            for case in TRAIN:
                request = case[1]
                decision = route(request, [])
                memory.learn(request, decision, handle(decision["initial"], case[2]), case[0])
            memory.reflect()
        self.assertEqual(client.remember.call_count, 3)
        self.assertEqual(client.record_step_outcome.call_count, 6)
        for call in client.remember.call_args_list:
            args = call.kwargs
            self.assertEqual(args["intent"], "fact")
            self.assertTrue(args["wait"])
            self.assertNotIn("lesson", json.loads(args["content"].split(": ", 1)[1]))
            self.assertNotIn("lesson_type", args)
        self.assertEqual([c.kwargs["signal"] for c in client.record_step_outcome.call_args_list], [-1, 1]*3)
        self.assertTrue(all("directive_hint" not in c.kwargs for c in client.record_step_outcome.call_args_list))
        client.advanced.reflect.assert_called_once_with(dict(
            run_id="memory-router-v2-test", include_linked_runs=False, include_step_outcomes=True))
        self.assertEqual(client.mock_calls[-1][0], "advanced.reflect")

    def test_retrieval_is_scoped_and_decision_has_no_outcome(self):
        client = Mock()
        entry = dict(id="lesson-1", content="Server guidance", entry_type="lesson",
                     run_id="state::actor::memory-router-v2-test")
        client.recall.return_value = {"evidence": [entry, dict(entry, id="stale", is_stale=True),
            dict(entry, id="other", run_id="memory-router-v1-other"), dict(entry, entry_type="fact")]}
        memory = Memory(client, "test")
        self.assertEqual(memory.recall("new request"), [{"id": "lesson-1", "content": "Server guidance", "conditions": []}])
        args = client.recall.call_args.kwargs
        self.assertEqual(args["entry_types"], ["lesson"])
        self.assertFalse(args["include_working_memory"])
        self.assertFalse(args["include_linked_runs"])
        model = Mock()
        model.models.generate_content.return_value = SimpleNamespace(text=json.dumps(dict(
            initial="billing_agent", lesson_ids=["lesson-1"], reason="applies")))
        with contextlib.redirect_stdout(io.StringIO()):
            run_case(HELD_OUT[0], memory, model=model)
        payload = json.loads(model.models.generate_content.call_args.kwargs["contents"])
        self.assertEqual(set(payload), {"request", "baseline", "lessons"})
        client.remember.assert_not_called()
        client.record_step_outcome.assert_not_called()
        client.advanced.reflect.assert_not_called()
        model.models.generate_content.return_value.text = json.dumps(dict(
            initial="billing_agent", lesson_ids=["invented"], reason="invalid"))
        with self.assertRaises(ValueError):
            route(HELD_OUT[0][1], memory.recall(HELD_OUT[0][1]), model)

    def test_failed_reflection_and_outcomes(self):
        client = Mock()
        client.advanced.reflect.return_value = {"lessons": [], "lessons_stored": 0}
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            Memory(client, "test").reflect()
        self.assertEqual(handle("technical_agent", None)["initial_status"], "failed")
        self.assertEqual(handle("technical_agent", "technical_agent")["initial_status"], "succeeded")
        model = Mock()
        self.assertEqual(route(HELD_OUT[0][1], [], model)["initial"], "technical_agent")
        model.models.generate_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
