"""Offline checks: convention matching, contrast."""
import unittest
from demo import CONVENTIONS, PRS, review

LESSONS = [(f"L{i}", "[rev-v1] " + c) for i, c in enumerate(CONVENTIONS)]

class ReviewTest(unittest.TestCase):
    def test_cold_catches_only_the_obvious_leak(self):
        for pid, pr in PRS.items():
            pr = dict(pr, id=pid)
            findings, cited, _ = review(pr, [])
            if pid == "PR-3":
                self.assertEqual(len(findings), 1)
            else:
                self.assertEqual(len(findings), 0, pid)

    def test_memory_catches_all_violations_and_cites(self):
        caught = cited = 0
        for pid, pr in PRS.items():
            pr = dict(pr, id=pid)
            findings, c, _ = review(pr, LESSONS)
            caught += len(findings)
            cited += len(c)
        self.assertEqual(caught, 3)
        self.assertEqual(cited, 3)

    def test_pr5_is_clean_and_not_blocked(self):
        pr = dict(PRS["PR-5"], id="PR-5")
        findings, _, fb = review(pr, LESSONS)
        self.assertEqual(findings, [])

    def test_contrast(self):
        cold = sum(len(review(dict(p, id=i), [])[0]) for i, p in PRS.items())
        warm = sum(len(review(dict(p, id=i), LESSONS)[0]) for i, p in PRS.items())
        self.assertGreater(warm, cold)

if __name__ == "__main__":
    unittest.main()
