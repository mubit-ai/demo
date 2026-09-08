"""Synthetic routing policy learning; Mubit is the only runtime lesson store."""
import argparse
import json
import os
import re
import uuid


AGENTS = ("billing_agent", "technical_agent", "account_agent")
# ponytail: explicit vocabulary generalizes only within this controlled domain.
# Replace extraction/router with a model when testing open-ended language.
VOCAB = {
    "api": ("api", "endpoint"),
    "renewal": ("renewal", "renewed", "subscription rolled over"),
    "invoice": ("invoice", "receipt"),
    "workspace": ("workspace", "organisation", "organization"),
    "login": ("login", "sign in", "log in"),
    "outage": ("outage", "everyone", "all users"),
}

# Ground truth belongs to the synthetic environment, never the router input.
TRAIN = [
    ("T1", "API access stopped immediately after renewal.", "billing_agent"),
    ("T2", "Invoice shows the wrong workspace name.", "account_agent"),
    ("T3", "Login fails for everyone during an outage.", "technical_agent"),
]
HELD_OUT = [
    ("H1", "Our endpoint stopped working when the subscription rolled over.", "billing_agent"),
    ("H2", "Please correct the organisation printed on my receipt.", "account_agent"),
    ("H3", "All users are unable to sign in this morning.", "technical_agent"),
    ("C1", "API returns a parser error on malformed JSON.", "technical_agent"),
    ("C2", "Please send a duplicate invoice.", "billing_agent"),
    ("C3", "I forgot my login password.", "account_agent"),
]


def features(request):
    return sorted(k for k, phrases in VOCAB.items()
                  if any(re.search(r"\b" + re.escape(p) + r"\b", request.lower()) for p in phrases))


def route(request, lessons):
    """Only current text and reusable rules enter the decision boundary."""
    tags = set(features(request))
    baseline = ("technical_agent" if "api" in tags else
                "billing_agent" if "invoice" in tags else "account_agent")
    applicable = [l for l in lessons if set(l["when"]) <= tags]
    targets = {l["prefer"] for l in applicable}
    # Conflicting evidence abstains instead of depending on retrieval order.
    use = applicable if len(targets) == 1 else []
    return dict(initial=use[0]["prefer"] if use else baseline, baseline=baseline,
                lesson_ids=[l["id"] for l in use],
                reason="applicable outcome-derived lesson" if use else "baseline (no unambiguous lesson)")


def specialist(agent, resolver):
    """Three synthetic specialists share this tiny handler; each owns its cases."""
    if agent not in AGENTS:
        raise ValueError("Unknown specialist")
    if agent == resolver:
        return dict(agent=agent, status="resolved")
    if resolver in AGENTS:
        return dict(agent=agent, status="handoff", next=resolver)
    return dict(agent=agent, status="failed")


def handle(initial, resolver):
    steps = [specialist(initial, resolver)]
    if steps[0]["status"] == "handoff":
        steps.append(specialist(steps[0]["next"], resolver))
    resolved = steps[-1]["status"] == "resolved"
    return dict(initial_status="succeeded" if len(steps) == 1 and resolved else
                "handoff" if len(steps) > 1 else "failed",
                resolved=resolved, resolved_by=steps[-1]["agent"] if resolved else None,
                handoffs=len(steps)-1, resolution_steps=len(steps), steps=steps)


def reflect(request, decision, outcome, source):
    """Derive target from observed resolution, never a pre-written correction."""
    if not outcome["resolved"] or outcome["initial_status"] != "handoff":
        return None
    return dict(when=features(request), prefer=outcome["resolved_by"],
                avoid=decision["initial"], source=source)


class Memory:
    def __init__(self, client, experiment):
        self.client = client
        self.marker = f"[memory-router-v1:{experiment}]\n"

    def recall(self, request):
        result = self.client.recall(
            query=f"{self.marker} routing lesson {' '.join(features(request))}: {request}",
            entry_types=["lesson"], limit=5, evidence_only=True,
            include_working_memory=False, include_linked_runs=False, prefer_current_run=True)
        if result.get("error"):
            raise RuntimeError("Mubit recall failed")
        lessons = []
        for entry in result.get("evidence") or []:
            content = entry.get("content") or entry.get("text") or ""
            if not content.startswith(self.marker) or not entry.get("id") or entry.get("is_stale"):
                continue
            try:
                rule = json.loads(content[len(self.marker):])["lesson"]
                valid = (isinstance(rule["when"], list) and bool(rule["when"])
                         and all(isinstance(k, str) and k in VOCAB for k in rule["when"])
                         and rule["prefer"] in AGENTS and rule["avoid"] in AGENTS
                         and isinstance(rule["source"], str))
                if valid and set(rule["when"]) <= set(features(request)):
                    lessons.append(dict(id=str(entry["id"]), **rule))
            except (ValueError, KeyError, TypeError):
                continue
        return lessons

    def learn(self, request, decision, outcome, source):
        # Update only lessons actually used, against FIRST-route success.
        for reference in decision["lesson_ids"]:
            ok = outcome["initial_status"] == "succeeded"
            self.client.record_outcome(reference_id=reference,
                outcome="success" if ok else "failure", signal=float(ok),
                rationale=f"Synthetic {source}: initial route {outcome['initial_status']}",
                verified_in_production=False)
        lesson = reflect(request, decision, outcome, source)
        if lesson is None:
            return None
        result = self.client.remember(
            content=self.marker + json.dumps(dict(lesson=lesson, evidence=dict(
                request=request, decision=decision, outcome=outcome))),
            intent="lesson", lesson_type="success", lesson_scope="run",
            lesson_importance="high", agent_id="router",
            upsert_key="routing:" + ":".join(lesson["when"]), wait=True,
            metadata={"source": source}, timeout_ms=60000)
        if result.get("error") or result.get("status") in ("failed", "error"):
            raise RuntimeError("Mubit lesson ingestion failed")
        recalled = [l for l in self.recall(request) if l["source"] == source]
        if not recalled:
            raise RuntimeError("Lesson was submitted but is not retrievable; do not claim learning")
        return recalled


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def run_case(case, memory=None, learn=False, execution="evaluation", arm="ON"):
    case_id, request, resolver = case
    lessons = memory.recall(request) if memory else []
    decision = route(request, lessons)
    emit("decision", case=case_id, arm=arm, request=request, lessons=lessons, **decision)
    # Only after the decision does the environment disclose success/handoff.
    outcome = handle(decision["initial"], resolver)
    emit("outcome", case=case_id, arm=arm, **outcome)
    if learn:
        stored = memory.learn(request, decision, outcome, f"{execution}/{case_id}")
        emit("learning", case=case_id, lessons=stored)
    return decision, outcome


def compare(memory):
    # No writes, outcome reinforcement, or evaluation-label access in retrieval/routing.
    totals = {}
    for arm in ("OFF", "ON"):
        outcomes = [run_case(c, memory if arm == "ON" else None, arm=arm)[1] for c in HELD_OUT]
        totals[arm] = dict(
            correct_first_route_rate=sum(o["initial_status"] == "succeeded" for o in outcomes)/len(outcomes),
            unnecessary_handoffs=sum(o["handoffs"] for o in outcomes),
            resolution_steps=sum(o["resolution_steps"] for o in outcomes))
    emit("metrics", cases=len(HELD_OUT), **totals)
    if totals["ON"]["correct_first_route_rate"] <= totals["OFF"]["correct_first_route_rate"]:
        raise RuntimeError("No first-route improvement measured; run teach with the same experiment first")
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("teach", "evaluate"))
    parser.add_argument("--experiment", required=True, help="Reuse this ID across processes; use a new ID for a clean experiment")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.experiment):
        parser.error("experiment must be 1–80 letters, digits, underscores or hyphens")
    if any(not os.getenv(k) for k in ("MUBIT_ENDPOINT", "MUBIT_API_KEY")):
        parser.error("Set MUBIT_ENDPOINT and MUBIT_API_KEY. There is no local memory fallback.")
    from mubit import Client
    # ponytail: stable Mubit run scopes one experiment; execution IDs track process runs.
    client = Client(endpoint=os.environ["MUBIT_ENDPOINT"], api_key=os.environ["MUBIT_API_KEY"],
                    transport="http", run_id=f"memory-router-v1-{args.experiment}", timeout_ms=60000)
    memory = Memory(client, args.experiment)
    execution = uuid.uuid4().hex
    emit("start", backend="Mubit", experiment=args.experiment, execution=execution, phase=args.phase)
    if args.phase == "teach":
        for case in TRAIN:
            run_case(case, memory, learn=True, execution=execution)
    else:
        compare(memory)


if __name__ == "__main__":
    main()
