"""Offline checks: fixture assembly and window loss."""
import unittest
from demo import FINAL_EXPECTED, STAGES

class FixtureTest(unittest.TestCase):
    def test_expected_answer_assembles_from_stage_clues(self):
        self.assertEqual(FINAL_EXPECTED, "-".join(s["clue"] for s in STAGES))

    def test_window_of_one_clue_cannot_answer(self):
        """The wipe arm keeps only the latest clue — assembly fails."""
        window = [STAGES[-1]["clue"]]
        final = "-".join(window[:3]) if len(window) >= 3 else "incomplete"
        self.assertNotEqual(final, FINAL_EXPECTED)

    def test_full_window_answers(self):
        window = [s["clue"] for s in STAGES]
        self.assertEqual("-".join(window[:3]), FINAL_EXPECTED)

if __name__ == "__main__":
    unittest.main()
