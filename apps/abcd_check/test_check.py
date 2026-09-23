"""Offline suite for the two-arm check. No keys, no network, no instance.

Written before the code (TDD). The Mubit client is faked at the method level
(`remember`, `lookup`, `get_context`) and replays what was seeded: the keyed lookup
returns the records whose `subject` or `entry_type` matches a clause, the semantic lane
the lessons; the provider is faked through the SDK's seam. The real `decide()` runs on top
of both. Covers, in order: the question; the seeded entries and the seeding calls; the
bare arm; the Mubit arm; the recall-hit columns; the per-arm totals; the verdict; the
report files; one end-to-end offline run; and the retry around each arm call.

Needs the SDK release that carries `decide()`; every case is skipped otherwise (see
requirements.txt).
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


def _sdk_has_decisions():
    try:
        return importlib.util.find_spec("mubit.decisions") is not None
    except ModuleNotFoundError:
        return False


HAS_DECISIONS = _sdk_has_decisions()
SKIP_REASON = "mubit-sdk with decide() is not installed (set MUBIT_SDK, see requirements.txt)"

if HAS_DECISIONS:
    from mubit.decisions import (ESCALATE, ProviderAnswer, ProviderError, ProviderResponse, ProviderUnavailable,
                                 ProviderUsage)

    import check
    from check import (JEV_PRICE_PER_M_INPUT_TOKENS, RUN, THRESHOLD, RecordingProvider, bare_arm, build_report,
                       mubit_arm, question_for, render_markdown, run_check, seed, seed_entries, state_hits,
                       summarise, verdict, with_retry)


class SdkCase(unittest.TestCase):
    """Skips the whole class when the installed SDK has no decide()."""

    @classmethod
    def setUpClass(cls):
        if not HAS_DECISIONS:
            raise unittest.SkipTest(SKIP_REASON)


# The metadata keys the ingestion agent stamps itself; a seeded entry must not set them.
RESERVED_METADATA_KEYS = ("item_id", "content_type", "source", "lane", "entry_type")
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
TICKETS = [
    dict(convo_id=101, subflow="return_size", text="Hi, the jeans I ordered are too small.\nCan I return them?",
         subject={"customer": "cminh730", "order": "3348917502"}, tier="silver", purchase_date="2020-01-15",
         packaging=False, expected="accept"),
    dict(convo_id=102, subflow="return_stain", text="My jacket arrived with a stain on the sleeve.",
         subject={"customer": "asanders813", "order": "7629401853"}, tier="bronze", purchase_date="2019-08-01",
         packaging=False, expected="ask_for_receipt"),
    dict(convo_id=103, subflow="return_color", text="The shirt is the wrong color, I want to send it back.",
         subject={"customer": "jwu1", "order": "1122334455"}, tier="gold", purchase_date="2018-02-02",
         packaging=False, expected="accept"),
    dict(convo_id=104, subflow="return_size", text="These boots do not fit at all.",
         subject={"customer": "guest9", "order": "9988776655"}, tier="guest", purchase_date="2019-12-01",
         packaging=True, expected="ask_for_receipt"),
]


def pinned(tickets=TICKETS):
    rows = []
    for t in tickets:
        row = dict(t)
        row["rule"] = RULES[t["tier"]]
        row["agent_action"] = {"membership_entered": t["tier"], "return_processed": True}
        rows.append(row)
    return {"question": "return_path", "options": ["accept", "ask_for_receipt"], "reference_date": "2020-04-01",
            "seed": 7, "count": len(rows), "subflows": ["return_color", "return_size", "return_stain"],
            "source": {"dataset": "ABCD v1.1", "license": "MIT"}, "rules": dict(RULES), "tickets": rows}


class FakeClient:
    """`remember`, `lookup` and `get_context` as the SDK client exposes them, replaying what was seeded.

    The keyed lookup returns, in the server's reply shape, every record of the run whose
    metadata `subject` or `entry_type` equals a match clause's. Recall returns the records
    of the requested entry types that are rules or whose entities appear in the query, as
    `sources` in the context response shape. Each remembered item gets a fresh record id."""

    def __init__(self):
        self.remember_calls = []
        self.lookup_calls = []
        self.context_calls = []
        self.records = []

    def remember(self, **kwargs):
        self.remember_calls.append(kwargs)
        record_id = f"rec-{len(self.records) + 1}"
        metadata = kwargs.get("metadata") or {}
        self.records.append({"id": record_id, "entry_type": kwargs.get("intent"), "content": kwargs["content"],
                             "entities": list(metadata.get("entities") or []), "subject": metadata.get("subject"),
                             "run": kwargs.get("session_id")})
        return {"job_id": f"job-{record_id}", "status": "completed", "done": True, "error": "",
                "traces": [{"item_id": "x", "writes": [
                    {"memory_type": "history", "record_id": f"hist-{record_id}", "success": True, "error": ""},
                    {"memory_type": "knowledge", "record_id": record_id, "success": True, "error": ""}]}]}

    def lookup(self, **kwargs):
        self.lookup_calls.append(kwargs)
        clauses = kwargs.get("match") or []
        subjects = [clause["subject"] for clause in clauses if "subject" in clause]
        entry_types = [clause["entry_type"] for clause in clauses if "entry_type" in clause]
        return [{"id": n, "run_id": record["run"],
                 "metadata": {"id": record["id"], "content": record["content"], "entry_type": record["entry_type"],
                              "subject": record["subject"], "entities": record["entities"]},
                 "created_at": n, "updated_at": n}
                for n, record in enumerate(self.records, 1)
                if record["run"] == kwargs.get("session_id")
                and (record["subject"] in subjects or record["entry_type"] in entry_types)]

    def get_context(self, **kwargs):
        self.context_calls.append(kwargs)
        query, entry_types = kwargs["query"], kwargs.get("entry_types")
        sources = []
        for record in self.records:
            if record["run"] != kwargs.get("session_id"):
                continue
            if entry_types is not None and record["entry_type"] not in entry_types:
                continue
            about_subject = any(entity in query for entity in record["entities"])
            if record["entry_type"] == "rule" or about_subject:
                sources.append({"id": record["id"], "entry_type": record["entry_type"], "content": record["content"],
                                "reference_id": record["id"], "referenceable": True})
        return {"context_block": "", "sources": sources}


def scripted(chosen, p):
    """An answer's probabilities and confidence when `chosen` holds probability `p`."""
    other = "ask_for_receipt" if chosen == "accept" else "accept"
    return {chosen: p, other: round(1 - p, 6)}, abs(2 * p - 1)


class FakeProvider:
    """Answers through the seam. With the subject's tier fact in the state it answers the
    expected option confidently; on bare text it guesses `accept` at 0.6, as a provider
    without the facts would. Records every call."""

    name = "fake"
    model = "fake-1"

    def __init__(self, tickets, informed_p=0.95, bare_p=0.6, latency_ms=100.0):
        self.by_text = {t["text"]: t for t in tickets}
        self.informed_p, self.bare_p, self.latency_ms = informed_p, bare_p, latency_ms
        self.calls = []

    def decide(self, state, questions):
        self.calls.append({"state": state, "questions": dict(questions)})
        ticket = self.by_text[state["text"]]
        informed = any(ticket["tier"] in fact for fact in state.get("facts", []))
        chosen, p = (ticket["expected"], self.informed_p) if informed else ("accept", self.bare_p)
        probabilities, confidence = scripted(chosen, p)
        answer = ProviderAnswer(chosen=chosen, confidence=confidence, probabilities=probabilities)
        return ProviderResponse(provider=self.name, model=self.model, answers={"return_path": answer},
                                usage=ProviderUsage(input_tokens=len(json.dumps(state)) // 4, output_tokens=20),
                                latency_ms=self.latency_ms)


class QuestionTest(SdkCase):
    def test_question_from_the_pinned_file(self):
        q = question_for(pinned())
        self.assertEqual(q.name, "return_path")
        self.assertEqual(q.type, "choice")
        self.assertEqual(list(q.options), ["accept", "ask_for_receipt"])
        self.assertEqual(q.act_threshold, THRESHOLD)
        self.assertEqual(THRESHOLD, 0.9)
        self.assertIn("2020-04-01", q.instructions)
        for option, criteria in q.options.items():
            self.assertTrue(criteria.strip(), option)


class SeedEntriesTest(SdkCase):
    def test_one_rule_per_tier_and_three_facts_per_ticket(self):
        entries = seed_entries(pinned())
        rules = [e for e in entries if e["intent"] == "rule"]
        facts = [e for e in entries if e["intent"] == "fact"]
        self.assertEqual(len(rules), 4)
        self.assertEqual(len(facts), 3 * len(TICKETS))
        self.assertEqual(len(entries), len(rules) + len(facts))
        self.assertEqual({r["content"] for r in rules}, set(RULES.values()))
        self.assertEqual({r["metadata"]["tier"] for r in rules}, {"gold", "silver", "bronze", "guest"})

    def test_facts_name_the_subject_under_entities_and_the_primary_under_subject(self):
        facts = [e for e in seed_entries(pinned([TICKETS[0]])) if e["intent"] == "fact"]
        for fact in facts:
            self.assertEqual(fact["metadata"]["entities"], ["cminh730", "3348917502"])
            self.assertIn(fact["metadata"]["subject"], ("cminh730", "3348917502"))
        kinds = {f["metadata"]["fact"]: f for f in facts}
        self.assertEqual(set(kinds), {"tier", "purchase_date", "packaging"})
        self.assertEqual(kinds["tier"]["metadata"]["subject"], "cminh730")
        self.assertEqual(kinds["purchase_date"]["metadata"]["subject"], "3348917502")
        self.assertEqual(kinds["packaging"]["metadata"]["subject"], "3348917502")

    def test_fact_content_states_the_tier_the_purchase_date_and_the_packaging(self):
        facts = {e["metadata"]["fact"]: e["content"] for e in seed_entries(pinned([TICKETS[0], TICKETS[3]]))
                 if e["intent"] == "fact" and e["metadata"]["subject"] in ("cminh730", "3348917502")}
        self.assertIn("cminh730", facts["tier"])
        self.assertIn("silver member", facts["tier"])
        self.assertIn("3348917502", facts["purchase_date"])
        self.assertIn("2020-01-15", facts["purchase_date"])
        self.assertIn("3348917502", facts["packaging"])
        self.assertIn("not", facts["packaging"])
        guest = {e["metadata"]["fact"]: e["content"] for e in seed_entries(pinned([TICKETS[3]]))
                 if e["intent"] == "fact"}
        self.assertIn("guest member", guest["tier"])
        self.assertNotIn("not", guest["packaging"])
        self.assertIn("original packaging", guest["packaging"])

    def test_no_entry_uses_a_reserved_metadata_key(self):
        for entry in seed_entries(pinned()):
            self.assertFalse(set(entry["metadata"]) & set(RESERVED_METADATA_KEYS), entry)


class SeedTest(SdkCase):
    def test_seeding_remembers_every_entry_into_the_run_and_waits(self):
        client = FakeClient()
        count = seed(client, "abcd-test", pinned())
        self.assertEqual(count, 4 + 3 * len(TICKETS))
        self.assertEqual(len(client.remember_calls), count)
        for call in client.remember_calls:
            self.assertEqual(call["session_id"], "abcd-test")
            self.assertIn(call["intent"], ("fact", "rule"))
            self.assertTrue(call.get("wait", False))
            self.assertIsInstance(call["metadata"], dict)
        self.assertEqual([c["intent"] for c in client.remember_calls[:4]], ["rule"] * 4)

    def test_run_name_is_fixed(self):
        self.assertEqual(RUN, "abcd-check")


class BareArmTest(SdkCase):
    def test_state_holds_only_the_ticket_text(self):
        provider = RecordingProvider(FakeProvider(TICKETS))
        result = bare_arm(provider, question_for(pinned()), pinned()["tickets"][0])
        self.assertEqual(provider.calls[-1]["state"], {"text": TICKETS[0]["text"]})
        self.assertEqual(list(provider.calls[-1]["questions"]), ["return_path"])
        self.assertEqual(result["chosen"], "accept")
        self.assertAlmostEqual(result["probabilities"]["accept"], 0.6)
        self.assertEqual(result["action"], ESCALATE)
        self.assertEqual(result["latency_ms"], 100.0)
        self.assertGreater(result["input_tokens"], 0)
        self.assertEqual(result["output_tokens"], 20)

    def test_an_answer_over_other_options_is_malformed(self):
        class WrongOptions(FakeProvider):
            def decide(self, state, questions):
                response = super().decide(state, questions)
                answer = ProviderAnswer(chosen="accept", confidence=0.5, probabilities={"accept": 0.7, "refund": 0.3})
                return ProviderResponse(response.provider, response.model, {"return_path": answer},
                                        response.usage, response.latency_ms)
        with self.assertRaises(ProviderError):
            bare_arm(RecordingProvider(WrongOptions(TICKETS)), question_for(pinned()), pinned()["tickets"][0])

    def test_action_is_the_chosen_option_at_or_above_the_threshold(self):
        provider = RecordingProvider(FakeProvider(TICKETS, bare_p=0.9))
        result = bare_arm(provider, question_for(pinned()), pinned()["tickets"][1])
        self.assertEqual(result["action"], "accept")
        provider = RecordingProvider(FakeProvider(TICKETS, bare_p=0.8999))
        result = bare_arm(provider, question_for(pinned()), pinned()["tickets"][1])
        self.assertEqual(result["action"], ESCALATE)


class MubitArmTest(SdkCase):
    def setUp(self):
        self.pinned = pinned()
        self.client = FakeClient()
        seed(self.client, "abcd-test", self.pinned)
        self.provider = RecordingProvider(FakeProvider(TICKETS))
        self.question = question_for(self.pinned)

    def test_decides_against_the_seeded_run_with_the_same_question(self):
        ticket = self.pinned["tickets"][1]
        result = mubit_arm(self.client, self.provider, self.question, ticket, "abcd-test")
        lookup = self.client.lookup_calls[-1]
        self.assertEqual(lookup["session_id"], "abcd-test")
        self.assertEqual(lookup["match"], [{"subject": "asanders813"}, {"subject": "7629401853"}, {"entry_type": "rule"}])
        call = self.client.context_calls[-1]
        self.assertEqual(call["session_id"], "abcd-test")
        self.assertEqual(call["entry_types"], ["lesson"])
        self.assertIn(ticket["text"], call["query"])
        self.assertIn("asanders813", call["query"])
        state = self.provider.calls[-1]["state"]
        self.assertEqual(state["text"], ticket["text"])
        self.assertEqual(state["subject"], {"customer": "asanders813", "order": "7629401853"})
        self.assertEqual(state["rules"], [RULES[tier] for tier in ("gold", "silver", "bronze", "guest")])
        self.assertEqual(state["facts"], [
            "Customer asanders813 is a bronze member.",
            "Order 7629401853 of customer asanders813 was purchased on 2019-08-01.",
            "Order 7629401853 of customer asanders813 is not in its original packaging any more.",
        ])
        self.assertTrue(result["tier_fact_present"])
        self.assertEqual(self.provider.calls[-1]["questions"]["return_path"].criteria, dict(self.question.options))

    def test_result_carries_the_decision_and_the_recall_hits(self):
        ticket = self.pinned["tickets"][1]
        result = mubit_arm(self.client, self.provider, self.question, ticket, "abcd-test")
        self.assertEqual(result["chosen"], "ask_for_receipt")
        self.assertEqual(result["action"], "ask_for_receipt")
        self.assertAlmostEqual(result["probabilities"]["ask_for_receipt"], 0.95)
        self.assertTrue(result["decision_id"].startswith("rec-"))
        self.assertTrue(result["tier_fact_present"])
        self.assertTrue(result["matching_rule_present"])
        self.assertEqual(result["latency_ms"], 100.0)
        self.assertGreaterEqual(result["total_ms"], 0.0)
        self.assertGreater(result["input_tokens"], 0)
        self.assertEqual(sorted(result["state"]), ["decisions", "facts", "lessons", "rules", "subject", "text"])
        self.assertGreaterEqual(len(result["state_refs"]), 4)

    def test_the_decision_is_recorded_in_the_run(self):
        before = len(self.client.records)
        mubit_arm(self.client, self.provider, self.question, self.pinned["tickets"][0], "abcd-test")
        self.assertEqual(len(self.client.records), before + 1)
        record = self.client.remember_calls[-1]
        self.assertEqual(record["intent"], "trace")
        self.assertEqual(record["session_id"], "abcd-test")
        self.assertEqual(record["metadata"]["entities"], ["cminh730", "3348917502"])


class StateHitsTest(SdkCase):
    TICKET = dict(TICKETS[0], rule=RULES["silver"])

    def test_both_present(self):
        state = {"facts": ["Customer cminh730 is a silver member.", "Order 3348917502 of customer cminh730 was purchased on 2020-01-15."],
                 "rules": [RULES["gold"], RULES["silver"]]}
        self.assertEqual(state_hits(self.TICKET, state), {"tier_fact_present": True, "matching_rule_present": True})

    def test_the_tier_fact_must_name_the_customer_and_the_tier(self):
        state = {"facts": ["Customer other1 is a silver member.", "Order 3348917502 of customer cminh730 was purchased on 2020-01-15."],
                 "rules": [RULES["silver"]]}
        self.assertFalse(state_hits(self.TICKET, state)["tier_fact_present"])
        state = {"facts": ["Customer cminh730 is a gold member."], "rules": [RULES["silver"]]}
        self.assertFalse(state_hits(self.TICKET, state)["tier_fact_present"])

    def test_the_rule_must_be_the_tier_s_own(self):
        state = {"facts": ["Customer cminh730 is a silver member."], "rules": [RULES["gold"], RULES["bronze"]]}
        self.assertFalse(state_hits(self.TICKET, state)["matching_rule_present"])

    def test_matching_ignores_case_and_surrounding_whitespace(self):
        state = {"facts": ["  customer CMINH730 is a Silver member. "], "rules": ["silver MEMBERS: > anything"]}
        self.assertEqual(state_hits(self.TICKET, state), {"tier_fact_present": True, "matching_rule_present": True})

    def test_empty_state(self):
        self.assertEqual(state_hits(self.TICKET, {}), {"tier_fact_present": False, "matching_rule_present": False})


def row(expected, bare_chosen, bare_p, mubit_chosen, mubit_p, *, tier_fact=True, rule=True,
        bare_ms=100.0, mubit_ms=150.0, mubit_total_ms=900.0, bare_tokens=100, mubit_tokens=400):
    def arm(chosen, p, ms, tokens, **extra):
        probabilities, confidence = scripted(chosen, p)
        return dict(chosen=chosen, action=chosen if p >= THRESHOLD else ESCALATE, probabilities=probabilities,
                    confidence=confidence, latency_ms=ms, input_tokens=tokens, output_tokens=20, **extra)
    return dict(convo_id=1, tier="silver", expected=expected,
                bare=arm(bare_chosen, bare_p, bare_ms, bare_tokens, total_ms=bare_ms),
                mubit=arm(mubit_chosen, mubit_p, mubit_ms, mubit_tokens, total_ms=mubit_total_ms,
                          tier_fact_present=tier_fact, matching_rule_present=rule, decision_id="rec-9",
                          state_refs=["rec-1"], state={}))


class SummariseTest(SdkCase):
    def test_per_arm_totals(self):
        rows = [row("accept", "accept", 0.95, "accept", 0.97),
                row("ask_for_receipt", "accept", 0.6, "ask_for_receipt", 0.92),
                row("ask_for_receipt", "ask_for_receipt", 0.91, "ask_for_receipt", 0.7),
                row("accept", "ask_for_receipt", 0.95, "accept", 0.99)]
        s = summarise(rows)
        self.assertEqual(s["bare"]["correct"], 2)
        self.assertEqual(s["mubit"]["correct"], 4)
        self.assertEqual(s["bare"]["automated"], 3)
        self.assertEqual(s["mubit"]["automated"], 3)
        self.assertAlmostEqual(s["bare"]["automated_share"], 0.75)
        self.assertAlmostEqual(s["mubit"]["automated_share"], 0.75)
        self.assertEqual(s["bare"]["automated_correct"], 2)
        self.assertEqual(s["mubit"]["automated_correct"], 3)
        self.assertEqual(s["bare"]["automated_wrong"], 1)
        self.assertEqual(s["mubit"]["automated_wrong"], 0)
        self.assertEqual(s["bare"]["count"], 4)

    def test_latency_and_cost_from_usage(self):
        rows = [row("accept", "accept", 0.95, "accept", 0.97, bare_ms=100, mubit_ms=200, mubit_total_ms=1000,
                    bare_tokens=100, mubit_tokens=300),
                row("accept", "accept", 0.95, "accept", 0.97, bare_ms=300, mubit_ms=400, mubit_total_ms=2000,
                    bare_tokens=200, mubit_tokens=500)]
        s = summarise(rows)
        self.assertAlmostEqual(s["bare"]["mean_latency_ms"], 200.0)
        self.assertAlmostEqual(s["mubit"]["mean_latency_ms"], 300.0)
        self.assertAlmostEqual(s["mubit"]["mean_total_ms"], 1500.0)
        self.assertEqual(s["bare"]["input_tokens"], 300)
        self.assertEqual(s["mubit"]["input_tokens"], 800)
        self.assertAlmostEqual(s["bare"]["cost_usd"], 300 * JEV_PRICE_PER_M_INPUT_TOKENS / 1e6)
        self.assertAlmostEqual(s["mubit"]["cost_usd"], 800 * JEV_PRICE_PER_M_INPUT_TOKENS / 1e6)
        self.assertAlmostEqual(s["mubit"]["mean_cost_usd"], 400 * JEV_PRICE_PER_M_INPUT_TOKENS / 1e6)

    def test_recall_hit_totals(self):
        rows = [row("accept", "accept", 0.9, "accept", 0.9, tier_fact=True, rule=True),
                row("accept", "accept", 0.9, "accept", 0.9, tier_fact=True, rule=False),
                row("accept", "accept", 0.9, "accept", 0.9, tier_fact=False, rule=True),
                row("accept", "accept", 0.9, "accept", 0.9, tier_fact=False, rule=False)]
        s = summarise(rows)
        self.assertEqual(s["mubit"]["tier_fact_present"], 2)
        self.assertEqual(s["mubit"]["matching_rule_present"], 2)
        self.assertEqual(s["mubit"]["both_present"], 1)
        self.assertAlmostEqual(s["mubit"]["both_present_share"], 0.25)

    def test_empty(self):
        s = summarise([])
        self.assertEqual(s["bare"]["count"], 0)
        self.assertEqual(s["bare"]["correct"], 0)
        self.assertEqual(s["mubit"]["automated_share"], 0.0)
        self.assertEqual(s["mubit"]["mean_latency_ms"], 0.0)


class VerdictTest(SdkCase):
    def rows(self, bare_correct, mubit_correct, both_present, n=10):
        rows = []
        for i in range(n):
            rows.append(row("accept", "accept" if i < bare_correct else "ask_for_receipt", 0.95,
                            "accept" if i < mubit_correct else "ask_for_receipt", 0.95,
                            tier_fact=i < both_present, rule=i < both_present))
        return rows

    def test_pass_needs_strictly_more_correct_and_ninety_percent_recall(self):
        v = verdict(summarise(self.rows(5, 6, 9)))
        self.assertTrue(v["passed"])
        self.assertTrue(v["mubit_more_correct"])
        self.assertTrue(v["recall_at_least_ninety_percent"])
        self.assertAlmostEqual(v["both_present_share"], 0.9)
        self.assertEqual(v["required_share"], 0.9)

    def test_equal_correct_fails(self):
        v = verdict(summarise(self.rows(6, 6, 10)))
        self.assertFalse(v["passed"])
        self.assertFalse(v["mubit_more_correct"])
        self.assertTrue(v["recall_at_least_ninety_percent"])

    def test_recall_below_ninety_percent_fails(self):
        v = verdict(summarise(self.rows(2, 9, 8)))
        self.assertFalse(v["passed"])
        self.assertTrue(v["mubit_more_correct"])
        self.assertFalse(v["recall_at_least_ninety_percent"])

    def test_no_rows_fails(self):
        self.assertFalse(verdict(summarise([]))["passed"])


class ReportTest(SdkCase):
    def report(self):
        rows = [dict(row("accept", "accept", 0.95, "accept", 0.97), convo_id=101, subflow="return_size",
                     tier="silver", text="Hi", subject={"customer": "cminh730", "order": "3348917502"}),
                dict(row("ask_for_receipt", "accept", 0.6, "ask_for_receipt", 0.92), convo_id=102,
                     subflow="return_stain", tier="bronze", text="Stain",
                     subject={"customer": "asanders813", "order": "7629401853"})]
        return build_report(pinned(), rows, run="abcd-test", provider="cloudflare-jev", model="jev-1.13.0",
                            endpoint="http://127.0.0.1:3000")

    def test_report_shape(self):
        r = self.report()
        self.assertEqual(r["question"]["name"], "return_path")
        self.assertEqual(r["question"]["threshold"], 0.9)
        self.assertEqual(r["reference_date"], "2020-04-01")
        self.assertEqual(r["seed"], 7)
        self.assertEqual(r["run"], "abcd-test")
        self.assertEqual(r["provider"], {"name": "cloudflare-jev", "model": "jev-1.13.0"})
        self.assertEqual(r["pricing"]["input_usd_per_m_tokens"], JEV_PRICE_PER_M_INPUT_TOKENS)
        self.assertIn("arms", r)
        self.assertEqual(set(r["arms"]), {"bare", "mubit"})
        self.assertEqual(r["arms"]["mubit"]["correct"], 2)
        self.assertEqual(r["arms"]["bare"]["correct"], 1)
        self.assertTrue(r["verdict"]["passed"])
        self.assertEqual(len(r["tickets"]), 2)
        self.assertEqual(r["tickets"][0]["expected"], "accept")
        self.assertIn("generated_at", r)
        self.assertEqual(r["ticket_count"], 2)

    def test_markdown_states_the_verdict_the_reference_date_the_seed_and_every_ticket(self):
        md = render_markdown(self.report())
        self.assertIn("PASS", md)
        self.assertIn("2020-04-01", md)
        self.assertIn("seed 7", md)
        self.assertIn("| 101 |", md)
        self.assertIn("| 102 |", md)
        self.assertIn("ask_for_receipt", md)
        self.assertIn("escalate", md)
        self.assertIn("bare", md.lower())
        self.assertIn("mubit", md.lower())

    def test_markdown_states_a_failing_verdict(self):
        r = self.report()
        r["verdict"] = dict(r["verdict"], passed=False, mubit_more_correct=False)
        self.assertIn("FAIL", render_markdown(r))


class RunCheckTest(SdkCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.client = FakeClient()
        self.provider = FakeProvider(TICKETS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seeds_runs_both_arms_and_writes_both_files(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report = run_check(pinned(), self.client, self.provider, run="abcd-test",
                               report_path=self.dir / "r.json", markdown_path=self.dir / "r.md")
        self.assertEqual(len(self.client.remember_calls), 4 + 3 * len(TICKETS) + len(TICKETS))
        self.assertEqual(len(self.provider.calls), 2 * len(TICKETS))
        self.assertEqual(report["arms"]["mubit"]["correct"], len(TICKETS))
        self.assertEqual(report["arms"]["bare"]["correct"], 2)
        self.assertEqual(report["arms"]["mubit"]["both_present"], len(TICKETS))
        self.assertTrue(report["verdict"]["passed"])
        written = json.loads((self.dir / "r.json").read_text())
        self.assertEqual(written["verdict"], report["verdict"])
        self.assertEqual([t["convo_id"] for t in written["tickets"]], [101, 102, 103, 104])
        self.assertIn("PASS", (self.dir / "r.md").read_text())
        self.assertIn("PASS", out.getvalue())

    def test_skip_seed_and_limit(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report = run_check(pinned(), self.client, self.provider, run="abcd-test", seed_run=False, limit=2,
                               report_path=self.dir / "r.json", markdown_path=self.dir / "r.md")
        self.assertEqual(len(report["tickets"]), 2)
        self.assertEqual(report["ticket_count"], 2)
        self.assertEqual(len(self.client.remember_calls), 2)
        self.assertEqual(report["arms"]["mubit"]["both_present"], 0)
        self.assertFalse(report["verdict"]["passed"])

    def test_ticket_rows_carry_both_arms_and_the_recall_columns(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report = run_check(pinned(), self.client, self.provider, run="abcd-test",
                               report_path=self.dir / "r.json", markdown_path=self.dir / "r.md")
        t = report["tickets"][1]
        self.assertEqual(t["expected"], "ask_for_receipt")
        self.assertEqual(t["bare"]["action"], ESCALATE)
        self.assertEqual(t["mubit"]["action"], "ask_for_receipt")
        self.assertTrue(t["mubit"]["tier_fact_present"])
        self.assertTrue(t["mubit"]["matching_rule_present"])
        self.assertTrue(t["mubit"]["decision_id"])
        self.assertIn(RULES["bronze"], t["mubit"]["state"]["rules"])


class FlakyProvider(FakeProvider):
    """Raises the scripted failures first, one per call, then answers like FakeProvider."""

    def __init__(self, tickets, failures):
        super().__init__(tickets)
        self.failures = list(failures)

    def decide(self, state, questions):
        if self.failures:
            raise self.failures.pop(0)
        return super().decide(state, questions)


class RetryTest(SdkCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.client = FakeClient()
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, provider):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_check(pinned(), self.client, provider, run="abcd-test", report_path=self.dir / "r.json",
                             markdown_path=self.dir / "r.md", sleep=self.sleeps.append)

    def test_an_unavailable_provider_is_retried_and_the_run_completes(self):
        provider = FlakyProvider(TICKETS, [ProviderUnavailable("HTTP 503: busy"), ProviderUnavailable("timed out")])
        report = self.run_check(provider)
        self.assertEqual(report["ticket_count"], len(TICKETS))
        self.assertEqual(len(provider.calls), 2 * len(TICKETS))
        self.assertEqual(self.sleeps, [2, 5])
        self.assertTrue(report["verdict"]["passed"])

    def test_a_provider_error_aborts_without_retry(self):
        provider = FlakyProvider(TICKETS, [ProviderError("HTTP 400: bad request")])
        with self.assertRaises(ProviderError):
            self.run_check(provider)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(provider.calls, [])

    def test_persistent_unavailability_aborts_naming_the_ticket(self):
        provider = FlakyProvider(TICKETS, [ProviderUnavailable("HTTP 503: busy")] * 3)
        with self.assertRaises(RuntimeError) as ctx:
            self.run_check(provider)
        self.assertIn("101", str(ctx.exception))
        self.assertIn("3 attempts", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, ProviderUnavailable)
        self.assertEqual(self.sleeps, [2, 5])
        self.assertEqual(provider.calls, [])

    def test_with_retry_returns_the_first_success_and_logs_each_retry(self):
        attempts = []
        logs = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise ProviderUnavailable("busy")
            return "answer"

        result = with_retry(flaky, what="convo 7 bare arm", sleep=self.sleeps.append, log=logs.append)
        self.assertEqual(result, "answer")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.sleeps, [2, 5])
        self.assertEqual(len(logs), 2)
        self.assertTrue(all("convo 7 bare arm" in line for line in logs))

    def test_with_retry_gives_up_after_the_attempts(self):
        with self.assertRaises(RuntimeError) as ctx:
            with_retry(lambda: (_ for _ in ()).throw(ProviderUnavailable("busy")), what="convo 7 mubit arm",
                       attempts=2, sleep=self.sleeps.append, log=lambda line: None)
        self.assertIn("convo 7 mubit arm", str(ctx.exception))
        self.assertEqual(self.sleeps, [2])


class MainTest(SdkCase):
    def test_missing_configuration_is_an_error_exit(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            check.main(["--tickets", str(Path(check.__file__).with_name("data") / "tickets.json")], env={})
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertIn("MUBIT_ENDPOINT", err.getvalue())


if __name__ == "__main__":
    unittest.main()
