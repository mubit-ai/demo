"""Offline checks: rule parsing, executor semantics, fixture contrast."""
import unittest
from demo import V1_PROMPT, EMAILS, parse_rules, execute

class ExecutorTest(unittest.TestCase):
    def test_parse_rules_reads_when_then_lines(self):
        rules = parse_rules(V1_PROMPT)
        self.assertEqual(rules[0], ("legal or lawyer or lawsuit", "legal_review"))
        self.assertTrue(all(len(k) > 0 for k, _ in rules))

    def test_any_keyword_matches(self):
        """Rule keywords are OR semantics: one hit decides."""
        email = dict(text="Our lawyer sent a letter.")
        queue, _ = execute(V1_PROMPT, email, probe_count=0)
        self.assertEqual(queue, "legal_review")

    def test_v1_misses_the_large_refund_class(self):
        for e in EMAILS["train"] + EMAILS["held_out"]:
            if e["expected"] == "large_refund":
                queue, _ = execute(V1_PROMPT, e, probe_count=0)
                self.assertNotEqual(queue, e["expected"], e["id"])

    def test_repeated_probe_shape_is_counted(self):
        email = dict(text="Our lawyer sent a letter.")
        self.assertEqual(execute(V1_PROMPT, email, 0)[1], 2)
        email2 = dict(text="The dashboard is down.")
        self.assertEqual(execute(V1_PROMPT, email2, 0)[1], 1)

    def test_v1_held_out_baseline_is_three_of_four(self):
        acc = sum(execute(V1_PROMPT, e, 0)[0] == e["expected"] for e in EMAILS["held_out"])
        self.assertEqual(acc, 3)

if __name__ == "__main__":
    unittest.main()
