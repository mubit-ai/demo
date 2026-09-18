"""Offline checks: engine arithmetic, decision rules, fixture contrast."""
import unittest
from demo import run_query, decide, NET_RULE, ACTIVE_RULE, SCHEMA_V2

class EngineTest(unittest.TestCase):
    def test_net_revenue_arithmetic(self):
        self.assertEqual(run_query({"op": "net_revenue", "customer": "acme"})["value"], 3700)
        self.assertEqual(run_query({"op": "net_revenue", "region": "AMER"})["value"], 4800)

    def test_renamed_table_errors(self):
        self.assertIn("no such table: refunds", run_query({"op": "count", "table": "refunds"})["error"])
        self.assertEqual(run_query({"op": "count", "table": "refund_events"})["value"], 3)

    def test_active_customers(self):
        self.assertEqual(run_query({"op": "active_customers"})["value"], 2)

class DecideTest(unittest.TestCase):
    def test_cold_decisions_use_naive_paths(self):
        spec, cited = decide("What is the net revenue for customer acme?", [])
        self.assertEqual(spec["op"], "sum")            # gross, not net
        spec, cited = decide("How many refunds were processed in August 2026?", [])
        self.assertEqual(spec["table"], "refunds")     # stale table name
        spec, cited = decide("How many active customers are there?", [])
        self.assertEqual(spec["op"], "count")          # counts everyone

    def test_memory_decisions_use_rules(self):
        rules = [NET_RULE, ACTIVE_RULE, SCHEMA_V2]
        spec, cited = decide("What is the net revenue for customer acme?", rules)
        self.assertEqual(spec["op"], "net_revenue")
        self.assertEqual(cited, ["net"])
        spec, cited = decide("How many refunds were processed in August 2026?", rules)
        self.assertEqual(spec["table"], "refund_events")
        self.assertEqual(cited, ["schema"])
        spec, cited = decide("How many active customers are there?", rules)
        self.assertEqual(spec["op"], "active_customers")
        self.assertEqual(cited, ["active"])

if __name__ == "__main__":
    unittest.main()
