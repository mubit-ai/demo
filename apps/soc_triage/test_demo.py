"""Offline checks: look-alike disposition, fixture contrast."""
import unittest
from demo import ALERTS, TRAIN, triage

LESSONS = [(f"L{i}", a["lesson"]) for i, (tid, a) in enumerate(TRAIN.items())]

class TriageTest(unittest.TestCase):
    def test_memory_closes_known_benign_with_citation(self):
        action, probes, cited = triage(ALERTS["A1"], LESSONS)
        self.assertEqual((action, cited), ("close_benign", "L0"))
        self.assertEqual(probes, 1)

    def test_look_alike_from_new_host_escalates_with_memory(self):
        action, probes, cited = triage(ALERTS["A5"], LESSONS)
        self.assertEqual(action, "escalate")
        self.assertTrue(cited)   # the drift rule, not the benign one

    def test_cold_false_closes_the_look_alike(self):
        action, _, _ = triage(ALERTS["A5"], [])
        self.assertEqual(action, "close_benign")

    def test_memory_arm_beats_cold_on_fixture(self):
        cold = sum(triage(a, [])[0] == a["expected"] for a in ALERTS.values())
        warm = sum(triage(a, LESSONS)[0] == a["expected"] for a in ALERTS.values())
        self.assertGreater(warm, cold)
        self.assertEqual(warm, len(ALERTS))

if __name__ == "__main__":
    unittest.main()
