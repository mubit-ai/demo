"""Offline checks: fixture integrity for the hygiene demo."""
import unittest
from demo import ARCHIVE_ITEMS, DUPLICATES, ENTITY_FACTS

class FixtureTest(unittest.TestCase):
    def test_duplicates_are_restated_lead_times(self):
        self.assertEqual(len(DUPLICATES), 6)
        for text in DUPLICATES:
            self.assertIn("business day", text.lower())

    def test_entity_facts_are_distinct(self):
        self.assertEqual(len(ENTITY_FACTS), 4)
        self.assertEqual(len({f.split()[1] for f in ENTITY_FACTS}), 4)

    def test_archive_items_have_kinds(self):
        for item in ARCHIVE_ITEMS:
            self.assertEqual(item["kind"], "export")

if __name__ == "__main__":
    unittest.main()
