"""Offline contract simulation, not verification of a live Mubit server."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from demo import Memory, TRAIN, HELD_OUT, compare, features, handle, reflect, route, run_case


class FakeClient:
    """Disk-backed test double only; deliberately returns irrelevant entries too."""
    def __init__(self, path):
        self.path = path

    def recall(self, **kwargs):
        assert kwargs["entry_types"] == ["lesson"]
        assert kwargs["include_working_memory"] is False
        assert kwargs["include_linked_runs"] is False
        return {"evidence": json.loads(self.path.read_text()) if self.path.exists() else []}

    def remember(self, **kwargs):
        assert kwargs["wait"] is True and kwargs["intent"] == "lesson"
        entries = self.recall(entry_types=["lesson"], include_working_memory=False,
                              include_linked_runs=False)["evidence"]
        entries.append(dict(id=f"fake-{len(entries)+1}", content=kwargs["content"]))
        self.path.write_text(json.dumps(entries))
        return {"status": "completed"}

    def record_outcome(self, **kwargs):
        assert kwargs["verified_in_production"] is False


class DemoTest(unittest.TestCase):
    def test_learning_and_read_only_held_out_comparison(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory)/"fake.json"
            memory = Memory(FakeClient(path), "test")
            self.assertEqual(route(HELD_OUT[0][1], memory.recall(HELD_OUT[0][1]))["initial"], "technical_agent")
            for case in TRAIN:
                decision, outcome = run_case(case, memory, learn=True, execution="teaching")
                self.assertEqual(outcome["initial_status"], "handoff")
                self.assertEqual(decision["lesson_ids"], [])
            before = path.read_bytes()
            # New adapter/client, with no Python lesson cache carried across executions.
            memory = Memory(FakeClient(path), "test")
            totals = compare(memory)
            self.assertEqual(totals["OFF"], dict(correct_first_route_rate=0.5,
                unnecessary_handoffs=3, resolution_steps=9))
            self.assertEqual(totals["ON"], dict(correct_first_route_rate=1.0,
                unnecessary_handoffs=0, resolution_steps=6))
            self.assertEqual(path.read_bytes(), before, "evaluation must not write")
            for case in HELD_OUT[:3]:
                self.assertNotIn(case[1], [c[1] for c in TRAIN])
                lessons = memory.recall(case[1])
                self.assertEqual(len(lessons), 1)
                self.assertTrue(route(case[1], lessons)["lesson_ids"])
                self.assertNotEqual(route(case[1], lessons)["initial"], route(case[1], [])["initial"])
            for case in HELD_OUT[3:]:
                self.assertEqual(memory.recall(case[1]), [])
            self.assertEqual(Memory(FakeClient(path), "other").recall(TRAIN[0][1]), [])

    def test_outcomes_and_grounding(self):
        request = TRAIN[0][1]
        decision = route(request, [])
        for resolver in ("billing_agent", "account_agent"):
            outcome = handle(decision["initial"], resolver)
            self.assertEqual(reflect(request, decision, outcome, "test")["prefer"], resolver)
        self.assertEqual(handle("technical_agent", None)["initial_status"], "failed")
        self.assertIsNone(reflect(request, decision, handle("technical_agent", None), "test"))
        self.assertIsNone(reflect(request, decision, handle("technical_agent", "technical_agent"), "test"))
        conflicting = [dict(id=str(i), when=features(request), prefer=a)
                       for i, a in enumerate(("billing_agent", "account_agent"))]
        self.assertEqual(route(request, conflicting), route(request, []))

    def test_ingestion_must_be_retrievable(self):
        class LostWrite(FakeClient):
            def remember(self, **kwargs):
                return {"status": "completed"}
        with tempfile.TemporaryDirectory() as directory:
            memory = Memory(LostWrite(Path(directory)/"missing.json"), "test")
            request = TRAIN[0][1]
            decision = route(request, [])
            with self.assertRaisesRegex(RuntimeError, "not retrievable"):
                memory.learn(request, decision, handle(decision["initial"], "billing_agent"), "test")


if __name__ == "__main__":
    unittest.main()
