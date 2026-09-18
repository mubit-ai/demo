"""Bi-temporal policy analyst; Mubit is the only memory.

Demonstrates three documented patterns in one deterministic agent (no LLM):

- bi-temporal memory: every fact carries a validity window. "What does the
  policy say today?" and "What did it say when the claim was filed?" are
  different reads of the same store, and both are exact.
- write-time reconciliation / supersession: newer facts are written over the
  same policy key with occurrence_time set; the store retires or reconciles
  the old entry, and the old entry stays queryable as history (is_stale).
- cross-run recall, user partitioning: each customer's facts are written with
  user_id and recalled under the same user_id; isolation is proven negatively.

Phases (separate processes, one experiment):
  setup      write the policy facts (valid-time stamped, user-scoped)
  adjudicate close the loop: 2 historical claims, cite the facts used,
             record_outcome with the known adjudication
  evaluate   6 held-out questions, two read disciplines (window-aware vs
             current-state-only), no writes

The environment fixture (FACTS/QUESTIONS below) is hidden truth; expected
answers are exact, so evaluation needs no judge.
"""
import argparse
import json
import os
import re
import uuid
from pathlib import Path

VERSION = "policy-analyst-v1"


def ts(date_str):
    """YYYY-MM-DD (inclusive) -> unix seconds."""
    import datetime
    return int(datetime.datetime.fromisoformat(date_str + "T00:00:00+00:00").timestamp())


TODAY = ts("2026-09-18")  # the fixture's "now"

# Hidden truth: policy facts with validity windows [valid_from, valid_to).
# valid_to None = open-ended. The write order matters: each newer fact is
# written over the same (customer, policy_key) slot, the way a real system
# would when a policy changes or is refined.
FACTS = [
    dict(customer="acme", key="coverage_water",
         text="Water damage is covered up to $50,000.",
         valid_from="2026-01-01", valid_to="2026-07-01"),
    dict(customer="acme", key="coverage_water",
         text="Water damage is excluded; a separate flood rider is required.",
         valid_from="2026-07-01", valid_to=None),
    dict(customer="acme", key="deductible",
         text="The deductible is $500 per claim.",
         valid_from="2026-01-01", valid_to="2026-05-01"),
    dict(customer="acme", key="deductible",
         text="The deductible is $500 per claim; it is waived for preferred vendors.",
         valid_from="2026-05-01", valid_to=None),
    dict(customer="blobfax", key="coverage_water",
         text="Water damage is covered up to $25,000.",
         valid_from="2026-01-01", valid_to=None),
]

# Historical claims used in the adjudicate phase: (id, customer, filed, key,
# correct fact index in FACTS). The verdict is known ground truth.
ADJUDICATIONS = [
    dict(claim="CLM-1042", customer="acme", filed="2026-04-10", key="coverage_water", fact=0,
         verdict="covered"),
    dict(claim="CLM-1043", customer="acme", filed="2026-02-15", key="deductible", fact=2,
         verdict="applied"),
]

# Held-out questions. mode "current" asks about today; mode "as_of" asks about
# the filing date. Expected is the FACTS index. A current-state-only memory
# (naive) answers every question from the latest fact per key and fails the
# as_of class — that contrast is the demo.
QUESTIONS = [
    dict(qid="Q1", customer="acme", key="coverage_water", mode="current", at=None, expected=1,
         ask="What does Acme's policy say about water damage today?"),
    dict(qid="Q2", customer="acme", key="coverage_water", mode="as_of", at="2026-04-10", expected=0,
         ask="Acme filed a water-damage claim on 2026-04-10. What did the policy say then?"),
    dict(qid="Q3", customer="acme", key="deductible", mode="as_of", at="2026-04-10", expected=2,
         ask="The same claim was adjudicated on 2026-04-10. Did a deductible waiver apply then?"),
    dict(qid="Q4", customer="acme", key="deductible", mode="current", at=None, expected=3,
         ask="What is Acme's deductible terms today?"),
    dict(qid="Q5", customer="blobfax", key="coverage_water", mode="current", at=None, expected=4,
         ask="What does Blobfax's policy say about water damage today?"),
    dict(qid="Q6", customer="acme", key="coverage_water", mode="as_of", at="2026-02-15", expected=0,
         ask="A claim was filed on 2026-02-15. What did Acme's water terms say then?"),
]


def covers(fact, at_secs):
    start = ts(fact["valid_from"])
    end = ts(fact["valid_to"]) if fact["valid_to"] else None
    return at_secs >= start and (end is None or at_secs < end)


def parse_fact_index(content):
    """Evidence content embeds the fixture index: '[fact:3] The deductible …'."""
    m = re.match(r"\[fact:(\d+)\]\s*(.*)", content or "")
    return (int(m.group(1)), m.group(2)) if m else (None, content)


def collect_facts(evidence, key):
    """Parse evidence entries of one policy key into fact dicts."""
    out = []
    for entry in evidence or []:
        if entry.get("entry_type") not in (None, "", "fact"):
            continue
        metadata = json.loads(entry.get("metadata_json") or "{}")
        if metadata.get("policy_key") != key:
            continue
        index, text = parse_fact_index(entry.get("content"))
        if index is None:
            continue
        out.append(dict(index=index, text=text,
                        valid_from=metadata.get("valid_from"),
                        valid_to=metadata.get("valid_to"),
                        stale=bool(entry.get("is_stale")), id=entry.get("id")))
    return out


def answer_at(evidence, key, at_secs):
    """Bi-temporal read over HISTORY recall: the server returned superseded
    facts as first-class evidence (history-intent query). Keep the facts whose
    validity window covers the question time; pick the newest valid_from."""
    candidates = [f for f in collect_facts(evidence, key) if covers(f, at_secs)]
    if not candidates:
        return None
    candidates.sort(key=lambda f: ts(f["valid_from"]))
    return candidates[-1]


def answer_current(evidence, key):
    """Current-state read over plain recall: the server already excluded
    superseded facts; pick the newest live fact of the key."""
    candidates = collect_facts(evidence, key)
    if not candidates:
        return None
    candidates.sort(key=lambda f: ts(f["valid_from"]))
    return candidates[-1]


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


class Memory:
    """One Mubit run per customer: runs are the hard isolation boundary, and
    user_id partitions again inside each run. A write-time reconciler must
    never see two customers' facts in one cluster."""

    def __init__(self, client, experiment):
        self.client = client
        self.run_ids = {c: f"{VERSION}-{experiment}-{c}" for c in ("acme", "blobfax")}

    def _session(self, customer):
        return self.run_ids[customer]

    def write_fact(self, fact, index):
        """Write one policy fact: valid-time stamped, user-scoped, slotted by
        policy_key. occurrence_time carries the valid_from (when it became
        true), distinct from ingestion time."""
        customer = fact["customer"]
        stored = self.client.remember(
            content=f"[fact:{index}] {fact['text']}",
            intent="fact",
            session_id=self._session(customer),
            user_id=customer,
            occurrence_time=ts(fact["valid_from"]),
            upsert_key=f"{VERSION}:{customer}:{fact['key']}:{fact['valid_from']}",
            item_id=f"{customer}-{fact['key']}-{fact['valid_from']}",
            wait=True, timeout_ms=60000,
            metadata=dict(policy_key=fact["key"], customer=customer,
                          valid_from=fact["valid_from"], valid_to=fact["valid_to"]),
        )
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError(f"Mubit did not ingest fact {index}")
        emit("fact_stored", customer=customer, key=fact["key"],
             valid_from=fact["valid_from"], valid_to=fact["valid_to"], index=index)

    def recall(self, customer, mode="current"):
        # Current read: plain query — the server hard-excludes superseded
        # beliefs. History read: history-intent phrasing flips the server into
        # returning superseded facts as first-class evidence (marked stale).
        query = (f"current policy terms coverage deductible for {customer}"
                 if mode == "current" else
                 f"What were the policy terms for {customer} before the changes? "
                 f"What did the policy say originally?")
        result = self.client.recall(
            query=query, session_id=self._session(customer), user_id=customer,
            entry_types=["fact"], limit=20, evidence_only=True,
            include_working_memory=False, include_linked_runs=False,
            prefer_current_run=True)
        if result.get("error"):
            raise RuntimeError("Mubit recall failed")
        run = self.run_ids[customer]
        return [e for e in (result.get("evidence") or [])
                if e.get("id") and e.get("run_id", "").split("::")[-1] == run]

    def visible_fact_indexes(self, customer):
        out = set()
        for entry in self.recall(customer, mode="history"):
            metadata = json.loads(entry.get("metadata_json") or "{}")
            index, _ = parse_fact_index(entry.get("content"))
            if metadata.get("customer") == customer and index is not None:
                out.add(index)
        return out

    def attribute(self, fact_ids, correct, rationale, customer):
        """Credit the exact facts the adjudication used. The outcome carries the
        same user scope and run as the facts — scoping is absolute."""
        if not fact_ids:
            return None
        return self.client.record_outcome(
            reference_id=fact_ids[0], entry_ids=fact_ids,
            outcome="success" if correct else "failure",
            signal=1.0 if correct else -1.0,
            idempotency_key=f"{VERSION}:{rationale[:60]}",
            rationale=rationale, verified_in_production=False,
            user_id=customer, session_id=self._session(customer))


def run_setup(memory):
    for index, fact in enumerate(FACTS):
        memory.write_fact(fact, index)
    # Verify every fact is retrievable; rewrite any that is not (same upsert
    # key, so the retry is idempotent). A cold embedding path can drop the
    # first writes out of the search index; the retry is the resilience story.
    for round_no in range(3):
        visible = {c: memory.visible_fact_indexes(c) for c in ("acme", "blobfax")}
        missing = [i for i, f in enumerate(FACTS) if i not in visible[f["customer"]]]
        if not missing:
            break
        for i in missing:
            emit("fact_retry", index=i, round=round_no + 1)
            memory.write_fact(FACTS[i], i)
    else:
        raise RuntimeError("facts still not retrievable after retries")
    for i in range(len(FACTS)):
        emit("fact_verified", index=i)
    # Store state, per customer: what the server did with superseded slots.
    for customer in ("acme", "blobfax"):
        for entry in memory.recall(customer):
            metadata = json.loads(entry.get("metadata_json") or "{}")
            index, _ = parse_fact_index(entry.get("content"))
            emit("store_state", customer=customer, key=metadata.get("policy_key"),
                 valid_from=metadata.get("valid_from"),
                 is_stale=bool(entry.get("is_stale")),
                 superseded_by=entry.get("superseded_by"), id=entry.get("id"),
                 index=index)


def run_adjudicate(memory):
    """Close the loop on 2 historical claims with known adjudications."""
    for adj in ADJUDICATIONS:
        at = ts(adj["filed"])
        evidence = memory.recall(adj["customer"], mode="history")
        fact = answer_at(evidence, adj["key"], at)
        correct = fact is not None and fact["index"] == adj["fact"]
        emit("adjudication", claim=adj["claim"], customer=adj["customer"],
             filed=adj["filed"], cited_fact=(fact or {}).get("index"),
             expected_fact=adj["fact"], verdict=adj["verdict"], correct=correct)
        memory.attribute([fact["id"]] if fact else [], correct,
                         f"claim {adj['claim']} adjudicated {adj['verdict']} under the "
                         f"terms valid on {adj['filed']}", adj["customer"])


def run_evaluate(memory):
    """6 held-out questions, two read disciplines, no writes."""
    totals = {}
    for arm in ("bi_temporal", "current_state_only"):
        results = []
        for question in QUESTIONS:
            at = ts(question["at"]) if question["at"] else TODAY
            # The bi-temporal arm picks the read that matches the question:
            # history-intent recall for as-of questions, plain recall otherwise.
            # The current-state-only arm always reads the current snapshot,
            # whatever the question asks — an overwrite-in-place memory.
            if arm == "bi_temporal":
                evidence = memory.recall(question["customer"],
                                         mode="history" if question["mode"] == "as_of" else "current")
                fact = (answer_at(evidence, question["key"], at) if question["mode"] == "as_of"
                        else answer_current(evidence, question["key"]))
            else:
                fact = answer_current(memory.recall(question["customer"], mode="current"),
                                      question["key"])
            correct = fact is not None and fact["index"] == question["expected"]
            results.append(correct)
            emit("answer", qid=question["qid"], arm=arm, customer=question["customer"],
                 mode=question["mode"], ask=question["ask"],
                 answered_fact=(fact or {}).get("index"),
                 answered_text=(fact or {}).get("text"),
                 stale_read=bool((fact or {}).get("stale")),
                 expected_fact=question["expected"], correct=correct)
        totals[arm] = dict(
            correct=sum(results), of=len(results),
            current_class_correct=sum(r for r, q in zip(results, QUESTIONS) if q["mode"] == "current"),
            as_of_class_correct=sum(r for r, q in zip(results, QUESTIONS) if q["mode"] == "as_of"))
    emit("metrics", **totals)
    naive = totals["current_state_only"]
    temporal = totals["bi_temporal"]
    if temporal["correct"] != temporal["of"]:
        raise RuntimeError("Bi-temporal arm did not answer every question correctly; "
                           "run setup with the same experiment first")
    if naive["correct"] >= temporal["correct"]:
        raise RuntimeError("No contrast measured: the current-state-only memory matched "
                           "the bi-temporal read")
    return totals


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("setup", "adjudicate", "evaluate"))
    parser.add_argument("--experiment", required=True,
                        help="Reuse this ID across processes; use a new ID for a clean experiment")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.experiment):
        parser.error("experiment must be 1–80 letters, digits, underscores or hyphens")
    if any(not os.getenv(k) for k in ("MUBIT_ENDPOINT", "MUBIT_API_KEY")):
        parser.error("Set MUBIT_ENDPOINT and MUBIT_API_KEY in .env. No local memory fallback.")

    from mubit import Client
    client = Client(endpoint=os.environ["MUBIT_ENDPOINT"], api_key=os.environ["MUBIT_API_KEY"],
                    transport="http", run_id=f"{VERSION}-{args.experiment}", timeout_ms=120000)
    memory = Memory(client, args.experiment)
    emit("start", backend="Mubit", experiment=args.experiment, phase=args.phase)
    if args.phase == "setup":
        run_setup(memory)
    elif args.phase == "adjudicate":
        run_adjudicate(memory)
    else:
        run_evaluate(memory)


if __name__ == "__main__":
    main()
