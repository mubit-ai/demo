"""Offline checks: resolution logic, guardrail, fixture contrast."""
import unittest
from demo import EMPLOYEES, REQUESTS, RUNBOOK_LESSON, ADMIN_GUARDRAIL, decide

L = lambda c: ("L1", c)

class DecideTest(unittest.TestCase):
    def test_cold_fails_all_held_out(self):
        for r in REQUESTS:
            action, turns, cited, violated = decide(r, None, [], False)
            self.assertNotEqual(action, r["expected"], r["id"])
        # and the admin request is granted without the guardrail — the violation
        r3 = [r for r in REQUESTS if r["id"] == "H3"][0]
        self.assertTrue(decide(r3, None, [], False)[3])

    def test_memory_resolves_all_and_respects_guardrail(self):
        lessons = [L(RUNBOOK_LESSON)]
        for r in REQUESTS:
            emp = EMPLOYEES.get(r["user"])
            action, turns, cited, violated = decide(r, emp, lessons, True)
            self.assertEqual(action, r["expected"], r["id"])
            self.assertFalse(violated)

    def test_runbook_reduces_turns(self):
        r = REQUESTS[0]
        self.assertLess(decide(r, None, [L(RUNBOOK_LESSON)], True)[1],
                        decide(r, None, [], False)[1])

if __name__ == "__main__":
    unittest.main()
