"""Offline checks with a fake Mubit client. No server, no keys."""
import contextlib
import io
import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from demo import (FACTS, QUESTIONS, ADJUDICATIONS, Memory, answer_at,
                  answer_current, collect_facts, covers, parse_fact_index, ts,
                  run_setup)

HISTORY_CUE = "before the changes"


def evidence_for(index, *, stale=False, run_id="state::actor::policy-analyst-v1-test"):
    fact = FACTS[index]
    return dict(id=f"entry-{index}", content=f"[fact:{index}] {fact['text']}",
                entry_type="fact", run_id=run_id, is_stale=stale,
                metadata_json=json.dumps(dict(policy_key=fact["key"],
                                              customer=fact["customer"],
                                              valid_from=fact["valid_from"],
                                              valid_to=fact["valid_to"])))


def store():
    """Two read views of the same store, as the server produces them:
    current recall excludes superseded beliefs; history recall keeps them."""
    current = {c: [evidence_for(i) for i, f in enumerate(FACTS)
                   if f["customer"] == c and i not in (0, 2)]
               for c in ("acme", "blobfax")}
    history = {c: [evidence_for(i, stale=(i in (0, 2))) for i, f in enumerate(FACTS)
                   if f["customer"] == c]
               for c in ("acme", "blobfax")}
    return current, history


class WindowLogicTest(unittest.TestCase):
    def test_covers_boundaries_are_from_inclusive_to_exclusive(self):
        water_v1, water_v2 = FACTS[0], FACTS[1]
        self.assertTrue(covers(water_v1, ts("2026-01-01")))
        self.assertTrue(covers(water_v1, ts("2026-06-30")))
        self.assertFalse(covers(water_v1, ts("2026-07-01")))   # v1 ends here
        self.assertTrue(covers(water_v2, ts("2026-07-01")))
        self.assertTrue(covers(water_v2, ts("2026-09-18")))    # open-ended
        self.assertFalse(covers(water_v2, ts("2026-06-30")))

    def test_parse_fact_index(self):
        self.assertEqual(parse_fact_index("[fact:3] hello"), (3, "hello"))
        self.assertIsNone(parse_fact_index("no marker")[0])

    def test_collect_facts_filters_by_key_and_marks_stale(self):
        _, history = store()
        facts = collect_facts(history["acme"], "coverage_water")
        self.assertEqual(sorted(f["index"] for f in facts), [0, 1])
        by_index = {f["index"]: f for f in facts}
        self.assertTrue(by_index[0]["stale"])
        self.assertFalse(by_index[1]["stale"])


class ReadDisciplineTest(unittest.TestCase):
    def test_bi_temporal_read_answers_history_from_superseded_entries(self):
        _, history = store()
        as_of_april = answer_at(history["acme"], "coverage_water", ts("2026-04-10"))
        self.assertEqual(as_of_april["index"], 0)          # history: covered $50k
        self.assertTrue(as_of_april["stale"])              # the superseded entry is the evidence
        self.assertEqual(answer_at(history["acme"], "coverage_water", ts("2026-09-18"))["index"], 1)

    def test_bi_temporal_read_picks_newest_valid_window(self):
        _, history = store()
        self.assertEqual(answer_at(history["acme"], "deductible", ts("2026-04-10"))["index"], 2)
        self.assertEqual(answer_at(history["acme"], "deductible", ts("2026-05-01"))["index"], 3)

    def test_current_read_answers_today(self):
        current, _ = store()
        self.assertEqual(answer_current(current["acme"], "coverage_water")["index"], 1)
        self.assertEqual(answer_current(current["acme"], "deductible")["index"], 3)
        self.assertEqual(answer_current(current["blobfax"], "coverage_water")["index"], 4)

    def test_current_state_only_arm_fails_every_as_of_question(self):
        """The naive arm answers as-of questions from the current snapshot —
        exactly the overwrite-in-place failure the demo exists to show."""
        current, _ = store()
        for question in (q for q in QUESTIONS if q["mode"] == "as_of"):
            fact = answer_current(current[question["customer"]], question["key"])
            self.assertIsNotNone(fact, question["qid"])
            self.assertNotEqual(fact["index"], question["expected"], question["qid"])

    def test_fixture_contrast_holds_without_a_server(self):
        current, history = store()
        for q in QUESTIONS:
            at = ts(q["at"]) if q["at"] else ts("2026-09-18")
            if q["mode"] == "as_of":
                got = answer_at(history[q["customer"]], q["key"], at)["index"]
            else:
                got = answer_current(current[q["customer"]], q["key"])["index"]
            self.assertEqual(got, q["expected"], q["qid"])


class MemoryTest(unittest.TestCase):
    def test_writes_carry_occurrence_time_user_scope_and_metadata(self):
        client = Mock()
        client.remember.return_value = {"status": "completed"}
        memory = Memory(client, "test")
        with contextlib.redirect_stdout(io.StringIO()):
            memory.write_fact(FACTS[1], 1)
        kwargs = client.remember.call_args.kwargs
        self.assertEqual(kwargs["user_id"], "acme")
        self.assertEqual(kwargs["occurrence_time"], ts("2026-07-01"))
        self.assertEqual(kwargs["metadata"]["policy_key"], "coverage_water")
        self.assertTrue(kwargs["wait"])

    def test_recall_uses_history_intent_only_in_history_mode(self):
        client = Mock()
        client.recall.return_value = {"evidence": []}
        memory = Memory(client, "test")
        with contextlib.redirect_stdout(io.StringIO()):
            memory.recall("acme", mode="current")
            memory.recall("acme", mode="history")
        queries = [c.kwargs["query"] for c in client.recall.call_args_list]
        self.assertIn("current policy terms", queries[0])
        self.assertIn(HISTORY_CUE, queries[1])
        for c in client.recall.call_args_list:
            self.assertEqual(c.kwargs["user_id"], "acme")
            self.assertTrue(c.kwargs["evidence_only"])

    def test_recall_scopes_to_the_customers_run(self):
        client = Mock()
        client.recall.return_value = {"evidence": [dict(
            id="e1", entry_type="fact",
            run_id="state::actor::policy-analyst-v1-test-acme",
            metadata_json="{}", content="[fact:0] x"),
            dict(id="e2", entry_type="fact", run_id="policy-analyst-v1-test-blobfax",
                 content="[fact:4] y")]}
        memory = Memory(client, "test")
        with contextlib.redirect_stdout(io.StringIO()):
            evidence = memory.recall("acme")
        self.assertEqual([e["id"] for e in evidence], ["e1"])   # blobfax's run dropped
        self.assertEqual(client.recall.call_args.kwargs["session_id"],
                         "policy-analyst-v1-test-acme")

    def test_attribute_credits_entry_ids_idempotently_and_in_scope(self):
        client = Mock()
        memory = Memory(client, "test")
        with contextlib.redirect_stdout(io.StringIO()):
            memory.attribute(["a", "b"], True, "claim CLM-1042 adjudicated covered", "acme")
        kwargs = client.record_outcome.call_args.kwargs
        self.assertEqual(kwargs["reference_id"], "a")
        self.assertEqual(kwargs["entry_ids"], ["a", "b"])
        self.assertEqual(kwargs["outcome"], "success")
        self.assertEqual(kwargs["user_id"], "acme")
        self.assertEqual(kwargs["session_id"], "policy-analyst-v1-test-acme")
        self.assertTrue(kwargs["idempotency_key"].startswith("policy-analyst-v1:"))
        self.assertFalse(kwargs["verified_in_production"])

    def test_setup_retries_unverifiable_facts(self):
        client = Mock()
        client.remember.return_value = {"status": "completed"}
        # Verification round 0 sees nothing (one recall per customer);
        # round 1 and the store-state pass see everything.
        calls = {"n": 0}
        def recall_side_effect(**kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                return {"evidence": []}
            customer = kwargs["session_id"].rsplit("-", 1)[-1]
            return {"evidence": [evidence_for(i, run_id=f"state::actor::policy-analyst-v1-test-{customer}")
                                 for i, f in enumerate(FACTS) if f["customer"] == customer]}
        client.recall.side_effect = recall_side_effect
        memory = Memory(client, "test")
        with contextlib.redirect_stdout(io.StringIO()):
            run_setup(memory)
        # 5 initial writes + 5 retries in round 0; round 1 verifies clean.
        self.assertEqual(client.remember.call_count, 10)


class FlowTest(unittest.TestCase):
    def test_launcher_runs_three_phases_and_stops_on_failure(self):
        launch = runpy.run_path(str(Path(__file__).with_name("__main__.py")))["main"]
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launch(), 0)
            commands = [c.args[0] for c in run.call_args_list]
            self.assertEqual([c[2] for c in commands], ["setup", "adjudicate", "evaluate"])
            self.assertEqual(len({c[-1] for c in commands}), 1)
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=1)) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launch(), 1)
            run.assert_called_once()

    def test_adjudication_fixture_points_at_valid_facts(self):
        for adj in ADJUDICATIONS:
            fact = FACTS[adj["fact"]]
            self.assertEqual(fact["customer"], adj["customer"])
            self.assertEqual(fact["key"], adj["key"])
            self.assertTrue(covers(fact, ts(adj["filed"])))


if __name__ == "__main__":
    unittest.main()
