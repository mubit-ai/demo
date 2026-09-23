"""Offline suite for the ABCD ticket set. No network: every test runs on a small
ABCD-shaped fixture. Written before the code (TDD). Covers, in order: the opening
utterances and the cut at the agent's details request; leak filtering and the tier
and purchase-age exclusions; the expected option per tier against the reference date;
the rule text pulled from the guidelines; the agent's recorded action; the stratified,
seeded selection; the pinned file's shape; byte-identical output; and the source fetch
with its hash check.
"""
import contextlib
import datetime as dt
import gzip
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tickets
from tickets import (OPTIONS, REFERENCE_DATE, RETURN_SUBFLOWS, SEED, TICKET_COUNT, TIERS,
                     SourceMismatch, agent_action, build, candidate_rows, expected_option,
                     fetch, opening_utterances, render, rule_text, select_tickets, ticket_text)

D = dt.date

RULES = {
    "gold": "Gold members: > Gold members get unlimited returns",
    "silver": "Silver members: > Ask for the purchase date, return possible within the last 6 months "
              "<or> Ask if they have a receipt, get to return if user has receipt <or> Ask if in "
              "original packaging, get to return if in original packaging",
    "bronze": "Bronze members: > Ask for the purchase date, return possible within the last 90 days "
              "<or> Ask if they have a receipt, get to return if user has receipt <or> Ask if in "
              "original packaging, get to return if in original packaging",
    "guest": "Guest members: > Ask for the purchase date, return possible within the last 30 days "
             "<or> Ask if they have a receipt, get to return if user has receipt",
}


def guidelines():
    policy = {"type": "faq/policy", "button": "Membership Privileges",
              "text": "Confirm their order can be returned, by checking their membership level.",
              "subtext": [RULES["gold"], RULES["silver"], RULES["bronze"], RULES["guest"],
                          "Enter the member level and then click the [Membership Privileges] option"]}
    subflow = {"instructions": ["In all three cases for return, follow the same set of actions:"],
               "actions": [{"type": "interaction", "button": "Pull up Account", "text": "Get Full Name"},
                           policy]}
    return {"Product Defect": {"subflows": {"Return Due to Stain": subflow,
                                            "Return Due to Color": subflow,
                                            "Return Due to Size": subflow}},
            "Order Issue": {"subflows": {}}}


def convo(convo_id=1, tier="silver", purchase_date="2020-01-15", packaging="yes", turns=None,
          subflow="return_size", name="Crystal Minh", username="cminh730", order_id="3348917502",
          actions=None):
    """One ABCD-shaped conversation. `turns` are (speaker, text) in order; an ("action", button,
    values) tuple becomes an action turn whose delexed targets carry the button and values."""
    turns = turns if turns is not None else [
        ("agent", "Hi! How can I help you?"),
        ("customer", "Hi! I need to return an item, can you help me with that?"),
        ("agent", "sure, may I have your name please?"),
        ("customer", name),
        ("action", "pull-up-account", [name.lower()]),
    ]
    if actions:
        turns = list(turns) + [("action", button, values) for button, values in actions]
    original, delexed = [], []
    for i, turn in enumerate(turns, start=1):
        if turn[0] == "action":
            _, button, values = turn
            original.append(["action", f"{button} in progress ..."])
            delexed.append({"speaker": "action", "text": f"{button} in progress ...", "turn_count": i,
                            "targets": [subflow, "take_action", button, list(values), -1]})
        else:
            speaker, text = turn
            original.append([speaker, text])
            step = "retrieve_utterance" if speaker == "agent" else None
            delexed.append({"speaker": speaker, "text": text.lower(), "turn_count": i,
                            "targets": [subflow, step, None, [], -1]})
    return {
        "convo_id": convo_id,
        "scenario": {
            "personal": {"customer_name": name.lower(), "email": f"{username}@email.com",
                         "member_level": tier, "phone": "(977) 625-2661", "username": username},
            "order": {"street_address": "6821 1st ave", "full_address": "6821 1st ave  san mateo, ny 75227",
                      "city": "san mateo", "num_products": "1", "order_id": order_id,
                      "packaging": packaging, "payment_method": "credit card",
                      "products": "[{'brand': 'guess', 'product_type': 'jeans', 'amount': 94}]",
                      "purchase_date": purchase_date, "state": "ny", "zip_code": "75227"},
            "product": {"names": ["guess jeans"], "amounts": [94]},
            "flow": "product_defect", "subflow": subflow,
        },
        "original": original,
        "delexed": delexed,
    }


def pool(count_per_cell=9, start_id=100):
    """A dataset whose return conversations cover every tier-option cell with unique customers,
    plus one conversation from another subflow that must be ignored."""
    convos, cid = [], start_id
    plans = {("gold", "accept"): ("2019-08-01", "no"),
             ("silver", "accept"): ("2020-01-15", "no"),
             ("silver", "ask_for_receipt"): ("2019-08-01", "no"),
             ("bronze", "accept"): ("2019-08-01", "yes"),
             ("bronze", "ask_for_receipt"): ("2019-08-01", "no"),
             ("guest", "accept"): ("2020-03-20", "no"),
             ("guest", "ask_for_receipt"): ("2019-08-01", "yes")}
    for (tier, _expected), (purchase_date, packaging) in plans.items():
        for k in range(count_per_cell):
            subflow = RETURN_SUBFLOWS[k % len(RETURN_SUBFLOWS)]
            convos.append(convo(cid, tier, purchase_date, packaging, subflow=subflow,
                                username=f"user{cid}", order_id=str(9000000000 + cid),
                                actions=[("membership", [tier]), ("update-order", ["by mail"])]))
            cid += 1
    convos.append(convo(cid, "gold", "2020-01-01", "yes", subflow="refund_initiate"))
    return {"train": convos[: len(convos) // 2], "dev": convos[len(convos) // 2:], "test": []}


class OpeningUtterancesTest(unittest.TestCase):
    def test_customer_lines_before_the_agent_asks_for_details(self):
        c = convo(turns=[("agent", "Hello, how can I help you?"),
                         ("customer", "hi, I received a jacket but it's the wrong size"),
                         ("agent", "What is the reason for the return?"),
                         ("customer", "it is too small"),
                         ("agent", "I'd be happy to help. Can I get a name please."),
                         ("customer", "Chloe Zhang"),
                         ("action", "pull-up-account", ["chloe zhang"])])
        self.assertEqual(opening_utterances(c),
                         ["hi, I received a jacket but it's the wrong size", "it is too small"])

    def test_cut_at_the_first_action_turn(self):
        c = convo(turns=[("customer", "Hello, my name is Albert Sanders and I would like to make a return."),
                         ("action", "pull-up-account", ["albert sanders"]),
                         ("customer", "it has a stain")])
        self.assertEqual(opening_utterances(c),
                         ["Hello, my name is Albert Sanders and I would like to make a return."])

    def test_agent_requests_for_each_detail_cut(self):
        for ask in ("May I have your full name or account ID", "what is your username, email and order id?",
                    "What is your membership level?", "When did you purchase it?",
                    "Do you still have the receipt?", "Is it in the original packaging?",
                    "Can I get your phone number?", "please give me your address"):
            c = convo(turns=[("customer", "I want to return an item"), ("agent", ask),
                             ("customer", "sure, here it is")])
            self.assertEqual(opening_utterances(c), ["I want to return an item"], ask)

    def test_agent_small_talk_does_not_cut(self):
        for line in ("I am sorry to hear that.", "Of course. Have you already received the item?",
                     "Thanks for contacting AcmeBrands!", "I'd be happy to help you with that"):
            c = convo(turns=[("customer", "I want to return an item"), ("agent", line),
                             ("customer", "it came with a stain")])
            self.assertEqual(opening_utterances(c), ["I want to return an item", "it came with a stain"], line)

    def test_customer_may_speak_first(self):
        c = convo(turns=[("customer", "I bought the wrong size, I want to return it."),
                         ("agent", "how may I help you today?"),
                         ("agent", "No problem, can I please get your name?")])
        self.assertEqual(opening_utterances(c), ["I bought the wrong size, I want to return it."])


class TicketTextTest(unittest.TestCase):
    def test_joins_utterances_with_newlines(self):
        c = convo(turns=[("customer", "hello"), ("customer", "I need to return jeans"),
                         ("agent", "your name please?")])
        self.assertEqual(ticket_text(c), "hello\nI need to return jeans")

    def test_caps_at_three_after_filtering(self):
        c = convo(turns=[("customer", "one"), ("customer", "my name is Crystal Minh"), ("customer", "two"),
                         ("customer", "three"), ("customer", "four"), ("agent", "your name please?")])
        self.assertEqual(ticket_text(c), "one\ntwo\nthree")

    def test_drops_utterances_that_leak_identity(self):
        leaks = ["My name is Crystal Minh", "it's Crystal", "Minh here", "username: cminh730",
                 "cminh730@email.com", "Order ID: 3348917502", "my number is (977) 625-2661",
                 "call me at 9776252661", "I live at 6821 1st ave", "zip 75227"]
        for leak in leaks:
            c = convo(turns=[("customer", "I want to return an item"), ("customer", leak),
                             ("agent", "your name please?")])
            self.assertEqual(ticket_text(c), "I want to return an item", leak)

    def test_name_match_is_case_insensitive_and_whole_word(self):
        c = convo(name="Joyce Wu", username="jwu1", turns=[("customer", "I want to return an item"),
                                                           ("customer", "this is JOYCE"),
                                                           ("customer", "would you help me"),
                                                           ("customer", "Wu is my last name"),
                                                           ("agent", "your name please?")])
        self.assertEqual(ticket_text(c), "I want to return an item\nwould you help me")

    def test_none_when_every_utterance_leaks(self):
        c = convo(turns=[("customer", "Hi, Crystal Minh here, order 3348917502"), ("agent", "your name?")])
        self.assertIsNone(ticket_text(c))

    def test_none_when_the_customer_never_speaks_before_the_cut(self):
        c = convo(turns=[("agent", "Hi, what is your name?"), ("customer", "Crystal Minh")])
        self.assertIsNone(ticket_text(c))

    def test_excluded_when_the_ticket_states_the_tier(self):
        for line in ("I'm a gold member and want to return this", "Silver member here", "as a bronze customer",
                     "I am a guest", "I don't have an account", "I do not have an account with you"):
            c = convo(turns=[("customer", line), ("agent", "your name please?")])
            self.assertIsNone(ticket_text(c), line)

    def test_excluded_when_the_ticket_states_a_purchase_age(self):
        for line in ("I bought this two weeks ago", "I received it yesterday", "I just bought a jacket",
                     "I just received my order and it doesn't fit", "I ordered it last month",
                     "it arrived 3 days ago", "I purchased it on March 3", "bought it 2/14",
                     "I recently purchased jeans", "I got the package today and it has a stain",
                     "I know it is within 30 days", "I've had it for a month",
                     "I received my boots just now"):
            c = convo(turns=[("customer", line), ("agent", "your name please?")])
            self.assertIsNone(ticket_text(c), line)

    def test_excluded_when_the_ticket_mentions_packaging_or_a_receipt(self):
        for line in ("it is still in the original packaging", "I have the receipt", "unopened package"):
            c = convo(turns=[("customer", "I want to return an item"), ("customer", line),
                             ("agent", "your name please?")])
            self.assertIsNone(ticket_text(c), line)

    def test_kept_when_the_ticket_only_mentions_the_purchase(self):
        for line in ("Hi, how are you today?", "I purchased jeans and they don't fit",
                     "I bought the wrong size, I want to return it", "the jacket I ordered is the wrong color",
                     "when it arrived it had a stain on it", "I want to return a $94 pair of jeans"):
            c = convo(turns=[("customer", line), ("agent", "your name please?")])
            self.assertEqual(ticket_text(c), line, line)

    def test_tier_and_age_are_judged_on_the_kept_text_only(self):
        c = convo(turns=[("customer", "I want to return an item"),
                         ("customer", "Crystal Minh, gold member, bought it yesterday"),
                         ("agent", "your name please?")])
        self.assertEqual(ticket_text(c), "I want to return an item")
        c = convo(turns=[("customer", "one"), ("customer", "two"), ("customer", "three"),
                         ("customer", "I'm a gold member"), ("agent", "your name please?")])
        self.assertEqual(ticket_text(c), "one\ntwo\nthree")


class ExpectedOptionTest(unittest.TestCase):
    REF = D(2020, 4, 1)

    def test_reference_date_is_pinned(self):
        self.assertEqual(REFERENCE_DATE, self.REF)

    def test_gold_always_accepts(self):
        self.assertEqual(expected_option("gold", D(2015, 1, 1), False, self.REF), "accept")

    def test_silver_six_calendar_months_or_packaging(self):
        self.assertEqual(expected_option("silver", D(2019, 10, 1), False, self.REF), "accept")
        self.assertEqual(expected_option("silver", D(2019, 9, 30), False, self.REF), "ask_for_receipt")
        self.assertEqual(expected_option("silver", D(2019, 1, 1), True, self.REF), "accept")

    def test_six_months_back_clamps_the_day_of_month(self):
        self.assertEqual(expected_option("silver", D(2020, 2, 29), False, D(2020, 8, 31)), "accept")
        self.assertEqual(expected_option("silver", D(2020, 2, 28), False, D(2020, 8, 31)), "ask_for_receipt")

    def test_bronze_ninety_days_or_packaging(self):
        self.assertEqual(expected_option("bronze", D(2020, 1, 2), False, self.REF), "accept")
        self.assertEqual(expected_option("bronze", D(2020, 1, 1), False, self.REF), "ask_for_receipt")
        self.assertEqual(expected_option("bronze", D(2019, 1, 1), True, self.REF), "accept")

    def test_guest_thirty_days_and_packaging_does_not_help(self):
        self.assertEqual(expected_option("guest", D(2020, 3, 2), False, self.REF), "accept")
        self.assertEqual(expected_option("guest", D(2020, 3, 1), False, self.REF), "ask_for_receipt")
        self.assertEqual(expected_option("guest", D(2020, 3, 1), True, self.REF), "ask_for_receipt")

    def test_unknown_tier_raises(self):
        with self.assertRaises(ValueError):
            expected_option("platinum", D(2020, 3, 1), True, self.REF)


class RuleTextTest(unittest.TestCase):
    def test_each_tier_gets_its_membership_privileges_line(self):
        for tier in TIERS:
            self.assertEqual(rule_text(guidelines(), tier), RULES[tier])

    def test_missing_rule_raises(self):
        g = guidelines()
        g["Product Defect"]["subflows"]["Return Due to Size"]["actions"][1]["subtext"] = ["Something else"]
        for sub in ("Return Due to Stain", "Return Due to Color"):
            del g["Product Defect"]["subflows"][sub]
        with self.assertRaises(LookupError):
            rule_text(g, "gold")


class AgentActionTest(unittest.TestCase):
    def test_membership_entered_and_return_processed(self):
        c = convo(actions=[("membership", ["silver"]), ("enter-details", ["6821 1st ave"]),
                           ("update-order", ["by mail"])])
        self.assertEqual(agent_action(c), {"membership_entered": "silver", "return_processed": True})

    def test_refused_return_without_membership_lookup(self):
        c = convo(actions=[("validate-purchase", ["cminh730", "x", "y"])])
        self.assertEqual(agent_action(c), {"membership_entered": None, "return_processed": False})

    def test_enter_details_alone_counts_as_processed(self):
        c = convo(actions=[("membership", ["gold"]), ("enter-details", ["6821 1st ave"])])
        self.assertEqual(agent_action(c)["return_processed"], True)

    def test_last_tier_valued_membership_entry_wins(self):
        c = convo(actions=[("membership", ["albertsanders813@email.com"]), ("membership", ["silver"]),
                           ("update-order", ["by mail"])])
        self.assertEqual(agent_action(c)["membership_entered"], "silver")
        c = convo(actions=[("membership", ["what is membership level"])])
        self.assertIsNone(agent_action(c)["membership_entered"])


class CandidateRowsTest(unittest.TestCase):
    def test_rows_come_from_the_three_return_subflows_only_sorted_by_convo_id(self):
        rows = candidate_rows(pool(1), guidelines(), D(2020, 4, 1))
        self.assertEqual([r["convo_id"] for r in rows], sorted(r["convo_id"] for r in rows))
        self.assertTrue(all(r["subflow"] in RETURN_SUBFLOWS for r in rows))
        self.assertEqual(len(rows), 7)

    def test_row_shape(self):
        data = {"train": [convo(42, "bronze", "2019-11-06", "yes", username="cminh730",
                                order_id="3348917502",
                                actions=[("membership", ["bronze"]), ("update-order", ["by mail"])])],
                "dev": [], "test": []}
        [row] = candidate_rows(data, guidelines(), D(2020, 4, 1))
        self.assertEqual(row, {
            "convo_id": 42,
            "subflow": "return_size",
            "text": "Hi! I need to return an item, can you help me with that?",
            "subject": {"customer": "cminh730", "order": "3348917502"},
            "tier": "bronze",
            "purchase_date": "2019-11-06",
            "packaging": True,
            "rule": RULES["bronze"],
            "expected": "accept",
            "agent_action": {"membership_entered": "bronze", "return_processed": True},
        })

    def test_conversations_without_a_ticket_text_are_dropped(self):
        data = {"train": [convo(1, turns=[("customer", "I'm a gold member"), ("agent", "name?")]),
                          convo(2, turns=[("customer", "I need to return jeans"), ("agent", "name?")])],
                "dev": [], "test": []}
        rows = candidate_rows(data, guidelines(), D(2020, 4, 1))
        self.assertEqual([r["convo_id"] for r in rows], [2])


class SelectTicketsTest(unittest.TestCase):
    def rows(self, per_cell=9):
        return candidate_rows(pool(per_cell), guidelines(), D(2020, 4, 1))

    def test_exactly_the_requested_count_sorted_by_convo_id(self):
        chosen = select_tickets(self.rows(), seed=SEED, count=50)
        self.assertEqual(len(chosen), 50)
        self.assertEqual([r["convo_id"] for r in chosen], sorted(r["convo_id"] for r in chosen))

    def test_every_tier_and_both_options_appear_and_cells_are_balanced(self):
        chosen = select_tickets(self.rows(), seed=SEED, count=50)
        self.assertEqual({r["tier"] for r in chosen}, set(TIERS))
        self.assertEqual({r["expected"] for r in chosen}, set(OPTIONS))
        cells = {}
        for r in chosen:
            cells[(r["tier"], r["expected"])] = cells.get((r["tier"], r["expected"]), 0) + 1
        self.assertEqual(len(cells), 7)
        self.assertLessEqual(max(cells.values()) - min(cells.values()), 1)

    def test_one_ticket_per_customer(self):
        rows = self.rows()
        for r in rows[:20]:
            r["subject"]["customer"] = "shared"
        chosen = select_tickets(rows, seed=SEED, count=40)
        customers = [r["subject"]["customer"] for r in chosen]
        self.assertEqual(len(customers), len(set(customers)))

    def test_same_seed_same_tickets_different_seed_different_tickets(self):
        a = [r["convo_id"] for r in select_tickets(self.rows(), seed=SEED, count=50)]
        b = [r["convo_id"] for r in select_tickets(self.rows(), seed=SEED, count=50)]
        c = [r["convo_id"] for r in select_tickets(self.rows(), seed=SEED + 1, count=50)]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_selection_does_not_depend_on_input_order(self):
        rows = self.rows()
        a = [r["convo_id"] for r in select_tickets(rows, seed=SEED, count=50)]
        b = [r["convo_id"] for r in select_tickets(list(reversed(rows)), seed=SEED, count=50)]
        self.assertEqual(a, b)

    def test_too_small_a_pool_raises(self):
        with self.assertRaises(ValueError):
            select_tickets(self.rows(per_cell=2), seed=SEED, count=50)

    def test_a_missing_tier_raises(self):
        rows = [r for r in self.rows() if r["tier"] != "guest"]
        with self.assertRaises(ValueError):
            select_tickets(rows, seed=SEED, count=20)


class BuildTest(unittest.TestCase):
    def test_pinned_file_shape(self):
        pinned = build(pool(), guidelines())
        self.assertEqual(pinned["question"], "return_path")
        self.assertEqual(pinned["options"], list(OPTIONS))
        self.assertEqual(pinned["reference_date"], "2020-04-01")
        self.assertEqual(pinned["seed"], SEED)
        self.assertEqual(pinned["count"], TICKET_COUNT)
        self.assertEqual(pinned["subflows"], list(RETURN_SUBFLOWS))
        self.assertEqual(pinned["rules"], RULES)
        self.assertEqual(pinned["source"]["dataset_sha256"], tickets.DATASET_SHA256)
        self.assertEqual(pinned["source"]["guidelines_sha256"], tickets.GUIDELINES_SHA256)
        self.assertIn("MIT", pinned["source"]["license"])
        self.assertEqual(len(pinned["tickets"]), TICKET_COUNT)
        self.assertTrue(all(t["rule"] == RULES[t["tier"]] for t in pinned["tickets"]))

    def test_render_is_byte_identical_across_builds_and_ends_with_a_newline(self):
        first = render(build(pool(), guidelines()))
        second = render(build(pool(), guidelines()))
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        self.assertEqual(json.loads(first)["count"], TICKET_COUNT)

    def test_render_keeps_non_ascii_text(self):
        data = pool()
        data["train"][0]["original"][1][1] = "I’d like to return my order"
        out = render(build(data, guidelines()))
        self.assertIn("I’d like".encode("utf-8"), out)


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.gz = gzip.compress(json.dumps(pool(1)).encode("utf-8"))
        self.gl = json.dumps(guidelines()).encode("utf-8")
        self.hashes = patch.multiple(tickets, DATASET_SHA256=hashlib.sha256(self.gz).hexdigest(),
                                     GUIDELINES_SHA256=hashlib.sha256(self.gl).hexdigest())
        self.hashes.start()
        self.calls = []

    def tearDown(self):
        self.hashes.stop()
        self.tmp.cleanup()

    def opener(self, url, timeout=None):
        self.calls.append(url)
        return io.BytesIO(self.gz if url == tickets.DATASET_URL else self.gl)

    def test_downloads_both_files_once_and_verifies(self):
        dataset, guide = fetch(self.dir, opener=self.opener)
        self.assertEqual(self.calls, [tickets.DATASET_URL, tickets.GUIDELINES_URL])
        self.assertEqual(dataset.read_bytes(), self.gz)
        self.assertEqual(guide.read_bytes(), self.gl)
        self.calls.clear()
        fetch(self.dir, opener=self.opener)
        self.assertEqual(self.calls, [])

    def test_a_failed_download_leaves_no_file_behind(self):
        def broken(url, timeout=None):
            raise OSError("connection reset")
        with self.assertRaises(OSError):
            fetch(self.dir, opener=broken)
        self.assertFalse((self.dir / tickets.DATASET_FILE).exists())

    def test_a_hash_mismatch_is_an_error(self):
        (self.dir / tickets.GUIDELINES_FILE).write_bytes(b"{}")
        with self.assertRaises(SourceMismatch) as ctx:
            fetch(self.dir, opener=self.opener)
        self.assertIn("guidelines.json", str(ctx.exception))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        gz = gzip.compress(json.dumps(pool()).encode("utf-8"))
        gl = json.dumps(guidelines()).encode("utf-8")
        (self.dir / tickets.DATASET_FILE).write_bytes(gz)
        (self.dir / tickets.GUIDELINES_FILE).write_bytes(gl)
        self.hashes = patch.multiple(tickets, DATASET_SHA256=hashlib.sha256(gz).hexdigest(),
                                     GUIDELINES_SHA256=hashlib.sha256(gl).hexdigest())
        self.hashes.start()
        self.out = self.dir / "tickets.json"

    def tearDown(self):
        self.hashes.stop()
        self.tmp.cleanup()

    def run_main(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tickets.main(["--data-dir", str(self.dir), "--out", str(self.out), *extra])
        return code, out.getvalue()

    def test_build_writes_the_file_and_a_second_run_is_byte_identical(self):
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("wrote 50 tickets", printed)
        first = self.out.read_bytes()
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.out.read_bytes(), first)
        self.assertEqual(len(json.loads(first)["tickets"]), TICKET_COUNT)

    def test_check_passes_on_a_fresh_file_and_fails_on_a_stale_one(self):
        self.run_main()
        self.assertEqual(self.run_main("--check")[0], 0)
        self.out.write_text(self.out.read_text().replace('"seed"', '"seeds"'))
        self.assertEqual(self.run_main("--check")[0], 1)

    def test_check_fails_when_the_file_is_missing(self):
        code, printed = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn("is missing", printed)


@unittest.skipUnless((tickets.DATA_DIR / tickets.DATASET_FILE).exists()
                     and (tickets.DATA_DIR / tickets.GUIDELINES_FILE).exists(),
                     "the raw ABCD data is not downloaded (make abcd-tickets)")
class CommittedFileTest(unittest.TestCase):
    def test_committed_file_is_reproduced_from_the_raw_data(self):
        dataset, guide = fetch(tickets.DATA_DIR)
        fresh = render(build(tickets.load_dataset(dataset), tickets.load_guidelines(guide)))
        self.assertEqual(fresh, tickets.PINNED_PATH.read_bytes())


if __name__ == "__main__":
    unittest.main()
