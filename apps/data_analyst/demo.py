"""Internal data analyst: text-to-SQL where the tribal knowledge lives in Mubit.

Deterministic (no LLM). The warehouse is an in-code fixture; the analyst
emits a structured query spec and the fixture engine evaluates it exactly.
The schema never documents the business logic — "net revenue = bookings
minus refunds", "active = ordered in 180 days" — and a mid-stream migration
renames the refunds table. Both facts live only in Mubit.

Implements: write-time reconciliation + supersession (schema notes),
operator-verbatim business rules, attribution, and drift survival.

Phases (separate processes, one experiment):
  seed      write the operator-confirmed business rules + schema note v1
  migrate   the warehouse is migrated: refunds -> refund_events; the analyst
            writes schema note v2 over the same slot (supersedes v1)
  evaluate  6 held-out questions in two arms — cold (no memory) vs analyst
            memory (rules + current schema note). No writes.
"""
import argparse
import json
import os
import re
from pathlib import Path

VERSION = "data-analyst-v1"
DEFAULT_QUEUE = "inbox"  # unused; keeps linters quiet about module scope

# ---------------------------------------------------------------- fixture --
TODAY = "2026-09-18"

ORDERS = [
    dict(order_id="o1", customer="acme", region="EMEA", amount=1200, date="2026-06-10"),
    dict(order_id="o2", customer="acme", region="EMEA", amount=800, date="2026-07-01"),
    dict(order_id="o3", customer="globex", region="AMER", amount=5000, date="2026-08-12"),
    dict(order_id="o4", customer="initech", region="AMER", amount=300, date="2026-01-05"),
    dict(order_id="o5", customer="acme", region="EMEA", amount=2000, date="2026-08-20"),
]
REFUNDS = [  # pre-migration table name
    dict(refund_id="r1", order_id="o2", amount=200, date="2026-07-05"),
    dict(refund_id="r2", order_id="o3", amount=500, date="2026-08-15"),
]
REFUND_EVENTS = REFUNDS + [  # post-migration rename, plus new rows
    dict(refund_id="r3", order_id="o5", amount=100, date="2026-08-25"),
]
CUSTOMERS = [
    dict(customer="acme", segment="enterprise"),
    dict(customer="globex", segment="enterprise"),
    dict(customer="initech", segment="smb"),
]
DRIFT_DATE = "2026-08-15"

NET_RULE = ("Net revenue = bookings (orders.amount) minus refunds on those orders, "
            "computed per customer or aggregated per region.")
ACTIVE_RULE = "An active customer has at least one order in the last 180 days."
SCHEMA_V1 = "Refund rows live in the `refunds` table, keyed by order_id."
SCHEMA_V2 = ("Schema migration 2026-08-15: the `refunds` table was renamed to "
             "`refund_events` (same columns). `refunds` no longer exists.")


# ------------------------------------------------------------------ engine --
def run_query(spec):
    """The deterministic warehouse engine. Returns rows/numbers or an error
    string exactly like a database would."""
    if spec.get("op") in ("net_revenue", "active_customers"):
        table = None          # aggregate ops compute over the whole fixture
    else:
        table = spec.get("table")
    if table is None and spec.get("op") not in ("net_revenue", "active_customers"):
        return dict(error="no table specified")
    if table == "orders":
        rows = ORDERS
    elif table == "refund_events":
        rows = REFUND_EVENTS
    elif table == "refunds":
        return dict(error=f"no such table: refunds (renamed to refund_events on {DRIFT_DATE})")
    elif table == "customers":
        rows = CUSTOMERS
    elif table is not None:
        return dict(error=f"no such table: {table}")
    for col, val in (spec.get("filter") or {}).items():
        rows = [r for r in rows if str(r.get(col)).startswith(str(val))]
    op = spec.get("op")
    if op == "net_revenue":
        def bookings(pred):
            return sum(o["amount"] for o in ORDERS if pred(o))
        def refunded(pred_o):
            ids = {o["order_id"] for o in ORDERS if pred_o(o)}
            return sum(x["amount"] for x in REFUND_EVENTS if x["order_id"] in ids)
        if spec.get("customer"):
            pred = lambda o: o["customer"] == spec["customer"]
        elif spec.get("region"):
            pred = lambda o: o["region"] == spec["region"]
        else:
            pred = lambda o: True
        return dict(value=bookings(pred) - refunded(pred))
    if op == "active_customers":
        import datetime
        cutoff = datetime.date(2026, 9, 18) - datetime.timedelta(days=180)
        actives = {o["customer"] for o in ORDERS
                   if datetime.date.fromisoformat(o["date"]) >= cutoff}
        return dict(value=len(actives), customers=sorted(actives))
    if op == "count":
        return dict(value=len(rows))
    if op == "sum":
        return dict(value=sum(r.get(spec["column"], 0) for r in rows))
    return dict(error=f"unsupported op: {op}")


# ------------------------------------------------------------- the analyst --
def parse_rule_note(content):
    return content


def decide(question, rules):
    """Deterministic analyst: question -> query spec, shaped only by the
    business rules and schema note recalled from memory. Returns
    (spec, cited_rule_ids)."""
    q = question.lower()
    cited = []
    spec = None
    if "net revenue" in q:
        has_rule = any("net revenue" in c.lower() for c in rules)
        if has_rule:
            cited.append("net")
            spec = dict(op="net_revenue")
            m = re.search(r"customer (\w+)", question, re.IGNORECASE)
            if m:
                spec["customer"] = m.group(1).lower()
            m = re.search(r"region (\w+)", question, re.IGNORECASE)
            if m:
                spec["region"] = m.group(1).upper()
        else:
            # naive: gross bookings only
            spec = dict(op="sum", table="orders", column="amount")
            m = re.search(r"customer (\w+)", question, re.IGNORECASE)
            if m:
                spec["filter"] = {"customer": m.group(1).lower()}
    elif "refund" in q and "how many" in q:
        has_v2 = any("refund_events" in c for c in rules)
        spec = dict(op="count", table="refund_events" if has_v2 else "refunds",
                    filter={"date": "2026-08"})
        if has_v2:
            cited.append("schema")
    elif "active" in q:
        has_rule = any("180 days" in c for c in rules)
        if has_rule:
            cited.append("active")
            spec = dict(op="active_customers")
        else:
            spec = dict(op="count", table="customers")
    elif "orders" in q and "how many" in q:
        spec = dict(op="count", table="orders")
        m = re.search(r"region (\w+)", question, re.IGNORECASE)
        if m:
            spec["filter"] = {"region": m.group(1).upper()}
    elif "how many" in q and "customers" in q:
        spec = dict(op="count", table="customers")
        m = re.search(r"segment (\w+)", question, re.IGNORECASE)
        if m:
            spec["filter"] = {"segment": m.group(1).lower()}
    return spec, cited


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


class Memory:
    """The analyst's memory: run-scoped rules and schema notes, upserted by
    slot so updates supersede instead of duplicate."""

    SLOTS = dict(net="rule:net-revenue", active="rule:active-customer",
                 schema="schema:refunds")

    def __init__(self, client, experiment):
        self.client = client
        self.run_id = f"{VERSION}-{experiment}"

    def __init__(self, client, experiment):
        self.client = client
        self.run_id = f"{VERSION}-{experiment}"
        self.slot_ids = {}

    def write(self, slot, content, intent="fact"):
        stored = self.client.remember(
            content=content, intent=intent, agent_id="data-analyst",
            session_id=self.run_id, user_id="analyst-team",
            upsert_key=f"{VERSION}:{self.SLOTS[slot]}",
            item_id=f"{self.SLOTS[slot]}-{abs(hash(content)) % 99999}",
            wait=True, timeout_ms=60000,
            metadata=dict(slot=slot))
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError(f"write failed for slot {slot}")
        ids = [w["record_id"] for t in stored.get("traces", [])
               for w in t.get("writes", []) if w.get("success")]
        if ids:
            self.slot_ids[slot] = ids[0]

    def recall_rules(self):
        r = self.client.recall(
            query="net revenue active customer refunds table schema",
            user_id="analyst-team", limit=10, evidence_only=True,
            include_working_memory=False, include_linked_runs=False,
            prefer_current_run=True)
        if r.get("error"):
            raise RuntimeError("recall failed")
        out = []
        for e in (r.get("evidence") or []):
            if e.get("id") and not e.get("is_stale") and \
                    e.get("run_id", "").split("::")[-1] == self.run_id:
                out.append(e.get("content") or "")
        return out

    def attribute(self, rule_ids, correct, question):
        real = [self.slot_ids[tag] for tag in rule_ids if tag in self.slot_ids]
        if not real:
            return
        for rid in real:
            self.client.record_outcome(
                reference_id=rid, outcome="success" if correct else "failure",
                signal=1.0 if correct else -1.0, agent_id="data-analyst",
                user_id="analyst-team", session_id=self.run_id,
                idempotency_key=f"{VERSION}:{rid}:{abs(hash(question)) % 99999}",
                rationale=f"answer to '{question[:50]}' "
                          f"{'verified' if correct else 'wrong'} against the warehouse")


def run_seed(client, experiment):
    memory = Memory(client, experiment)
    memory.write("net", NET_RULE)
    memory.write("active", ACTIVE_RULE)
    memory.write("schema", SCHEMA_V1)
    emit("seed_done", rules=2, schema_note="v1")


def run_migrate(client, experiment):
    memory = Memory(client, experiment)
    memory.write("schema", SCHEMA_V2)
    emit("migration_recorded", note="v2 (supersedes v1 via upsert)")


def run_evaluate(client, experiment):
    memory = Memory(client, experiment)
    rules_mem = memory.recall_rules()
    rules_cold = []
    # fixture truth for every held-out question
    QUESTIONS = [
        dict(qid="Q1", cls="basic", ask="How many orders in region EMEA?", truth=3),
        dict(qid="Q2", cls="business", ask="What is the net revenue for customer acme?",
             truth=ORDERS[0]["amount"] + ORDERS[1]["amount"] + ORDERS[4]["amount"]
                   - (REFUND_EVENTS[0]["amount"] + REFUND_EVENTS[2]["amount"])),
        dict(qid="Q3", cls="basic", ask="How many customers in segment enterprise?", truth=2),
        dict(qid="Q4", cls="business", ask="What is the net revenue in region AMER?",
             truth=5000 + 300 - 500),
        dict(qid="Q5", cls="drift", ask="How many refunds were processed in August 2026?",
             truth=len([x for x in REFUND_EVENTS if x["date"].startswith("2026-08")])),
        dict(qid="Q6", cls="business", ask="How many active customers are there?",
             truth=2),
    ]
    totals = {}
    for arm, rules in (("cold", rules_cold), ("with_memory", rules_mem)):
        results = []
        for qd in QUESTIONS:
            spec, cited = decide(qd["ask"], rules)
            result = run_query(spec) if spec else dict(error="no plan")
            value = result.get("value")
            correct = value == qd["truth"]
            results.append(correct)
            if arm == "with_memory":
                memory.attribute(cited, correct, qd["ask"])
            emit("answer", qid=qd["qid"], arm=arm, cls=qd["cls"], ask=qd["ask"],
                 spec=json.dumps(spec, sort_keys=True) if spec else None,
                 value=value, expected=qd["truth"], correct=correct,
                 error=result.get("error"))
        totals[arm] = dict(correct=sum(results), of=len(results),
                           business=sum(c for c, q in zip(results, QUESTIONS)
                                        if q["cls"] != "basic"))
    emit("metrics", **totals)
    if totals["with_memory"]["correct"] != totals["with_memory"]["of"]:
        raise RuntimeError("the analyst with memory missed questions; run seed and "
                           "migrate with the same experiment first")
    if totals["cold"]["correct"] >= totals["with_memory"]["correct"]:
        raise RuntimeError("no memory contrast measured")
    return totals


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "migrate", "evaluate"))
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.experiment):
        parser.error("experiment must be 1-80 letters, digits, underscores or hyphens")
    if not os.getenv("MUBIT_ENDPOINT") or not os.getenv("MUBIT_API_KEY"):
        parser.error("Set MUBIT_ENDPOINT and MUBIT_API_KEY in .env.")

    from mubit import Client
    client = Client(endpoint=os.environ["MUBIT_ENDPOINT"], api_key=os.environ["MUBIT_API_KEY"],
                    transport="http", run_id=f"{VERSION}-{args.experiment}", timeout_ms=120000)
    emit("start", backend="Mubit", experiment=args.experiment, phase=args.phase)
    if args.phase == "seed":
        run_seed(client, args.experiment)
    elif args.phase == "migrate":
        run_migrate(client, args.experiment)
    else:
        run_evaluate(client, args.experiment)


if __name__ == "__main__":
    main()
