"""Offline checks: routing, resolution, fixture contrast. No server."""
import unittest
from demo import TICKETS, TEAM_LESSONS, Agent, classify

def lesson(content):
    return dict(id="L1", content=content)

class RoutingTest(unittest.TestCase):
    def test_classify_routes_by_keywords(self):
        self.assertEqual(classify(TICKETS["T1"]["text"]), "billing")
        self.assertEqual(classify(TICKETS["T2"]["text"]), "technical")
        self.assertEqual(classify(TICKETS["H3"]["text"]), "technical")

class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.agent = Agent.__new__(Agent)   # pure-logic instance
        self.agent.name = "billing"

    def test_cold_resolution_fails_refund_and_rollback_cases(self):
        for tid in ("T1", "H1", "H3"):
            ticket = TICKETS[tid]
            decided, cited = self.agent.resolve(ticket, [])
            self.assertNotEqual(decided, ticket["resolution"], tid)
            self.assertIsNone(cited)

    def test_team_lessons_fix_every_held_out_case(self):
        lessons = [lesson(TEAM_LESSONS["billing"]), lesson(TEAM_LESSONS["technical"])]
        for tid in ("H1", "H2", "H3", "H4"):
            decided, cited = self.agent.resolve(TICKETS[tid], lessons)
            self.assertEqual(decided, TICKETS[tid]["resolution"], tid)
        # The cases the cold arm fails must cite the team lesson that fixes them.
        for tid in ("H1", "H3", "H4"):
            decided, cited = self.agent.resolve(TICKETS[tid], lessons)
            self.assertEqual(cited, "L1", tid)

    def test_lessons_are_lane_scoped(self):
        """The billing lesson alone must not decide a technical ticket."""
        decided, _ = self.agent.resolve(TICKETS["H3"], [lesson(TEAM_LESSONS["billing"])])
        self.assertNotEqual(decided, TICKETS["H3"]["resolution"])

    def test_fixture_contrast_holds(self):
        lessons = [lesson(TEAM_LESSONS["billing"]), lesson(TEAM_LESSONS["technical"])]
        cold = sum(self.agent.resolve(TICKETS[t], [])[0] == TICKETS[t]["resolution"]
                   for t in ("H1", "H2", "H3", "H4"))
        warm = sum(self.agent.resolve(TICKETS[t], lessons)[0] == TICKETS[t]["resolution"]
                   for t in ("H1", "H2", "H3", "H4"))
        self.assertGreater(warm, cold)
        self.assertEqual(warm, 4)

if __name__ == "__main__":
    unittest.main()
