"""Support team pipeline: intake -> specialist, reviewed via events.

Deterministic (no LLM). Implements four documented patterns:

- memory-backed-handoff: intake hands a ticket to a specialist as a
  structured handoff entry (findings / open questions / next step), not a
  transcript dump.
- event-driven-agents: the reviewer wakes on `context.handoff_created`
  from the control event stream (SSE) — it never polls.
- subagent-isolation: every agent owns its own run; the handoff entry is
  the only bridge between them.
- shared-team-memory: reviewer corrections are written as GLOBAL lessons,
  so the other specialist's fresh run picks them up automatically (the
  server's global lesson overlay).

Phases (separate processes, one experiment):
  teach     3 tickets flow intake -> specialist; the reviewer corrects one;
            corrections become global team lessons
  evaluate  4 held-out tickets, two arms: with team memory (recalls the
            global lessons) and without (fresh cold policy). No writes.
"""
import argparse
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from pathlib import Path

VERSION = "team-pipeline-v1"
SPECIALISTS = ("billing", "technical")

# Hidden truth: ticket class -> the resolution that the fixture scores.
TICKETS = {
    # teach
    "T1": dict(text="Refund requested for duplicate charge on card.", cat="billing",
               resolution="refund_duplicate"),
    "T2": dict(text="Login fails with error 502 for one user.", cat="technical",
               resolution="reset_session"),
    "T3": dict(text="Refund requested for annual plan after 90 days.", cat="billing",
               resolution="manual_review"),   # outside the 60-day self-serve window
    # held out
    "H1": dict(text="Duplicate charge refund asked by customer.", cat="billing",
               resolution="refund_duplicate"),
    "H2": dict(text="Payment retry keeps failing with timeout.", cat="billing",
               resolution="manual_review"),   # amount uncapped policy: needs review
    "H3": dict(text="API returns 502 for every request since deploy.", cat="technical",
               resolution="rollback_deploy"),
    "H4": dict(text="Cannot sign in, session errors for a single account.", cat="technical",
               resolution="reset_session"),
}

COLD_RESOLUTION = {  # the conservative default without lessons
    "billing": "manual_review",
    "technical": "collect_diagnostics",
}

# The team lessons the reviewer teaches on corrections: one per lane.
TEAM_LESSONS = {
    "billing": ("Billing refunds: self-serve refund is only for duplicate charges within the "
                "60-day window. Anything else (late-window, uncapped retry) goes to manual_review."),
    "technical": ("Technical 502 incidents: roll back the deploy when failures affect many "
                  "requests after a deploy. For a single user failing to log in or sign in, "
                  "reset the session instead."),
}


def classify(text):
    """Intake's deterministic router: keyword -> specialist."""
    return "technical" if re.search(r"login|api|sign in|session|deploy|502", text.lower()) else "billing"


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def subscribe_events(endpoint, api_key, run_id, wanted, timeout_s, out):
    """Consume the control event stream (SSE) until one wanted event arrives."""
    req = urllib.request.Request(
        f"{endpoint}/v2/control/events/subscribe?run_id={urllib.parse.quote(run_id)}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"})
    deadline = time.monotonic() + timeout_s
    with urllib.request.urlopen(req, timeout=timeout_s + 10) as resp:
        event_name, data = None, ""
        while time.monotonic() < deadline:
            line = resp.readline()
            if not line:
                break
            text = line.decode().strip()
            if text.startswith("event:"):
                event_name = text.split(":", 1)[1].strip()
            elif text.startswith("data:") and event_name in wanted:
                out.append(dict(type=event_name, payload=json.loads(text.split(":", 1)[1] or "{}")))
                return


class Agent:
    """One agent = one run. Runs are the isolation boundary between agents."""

    def __init__(self, client, experiment, name):
        self.client = client
        self.name = name
        self.run_id = f"{VERSION}-{experiment}-{name}"
        self._sid = None

    @property
    def session(self):
        return self.run_id

    def handoff_to(self, to_agent, task_id, content):
        r = self.client.handoff(
            task_id=task_id, from_agent_id=self.name, to_agent_id=to_agent,
            content=content, session_id=self.session,
            metadata=dict(handoff_version=VERSION))
        if r.get("error"):
            raise RuntimeError(f"handoff failed: {r['error']}")
        return r

    def feedback(self, handoff_id, verdict, comments, reviewer):
        r = self.client.feedback(handoff_id=handoff_id, verdict=verdict, comments=comments,
                                 from_agent_id=reviewer, session_id=self.session)
        if r.get("error"):
            raise RuntimeError(f"feedback failed: {r['error']}")
        return r

    def recall_team_lessons(self, query):
        """Global lessons overlay into every run automatically."""
        r = self.client.recall(query=query, entry_types=["lesson"], limit=5, evidence_only=True,
                               include_working_memory=False, include_linked_runs=False,
                               prefer_current_run=True)
        if r.get("error"):
            raise RuntimeError("recall failed")
        return [e for e in (r.get("evidence") or [])
                if e.get("id") and not e.get("is_stale")]

    def teach_global_lesson(self, content, conditions):
        r = self.client.remember(
            content=content, intent="lesson", lesson_scope="global",
            lesson_importance="high", agent_id="reviewer",
            lesson_conditions=conditions, wait=True, timeout_ms=60000)
        if r.get("error") or r.get("status") in ("failed", "error"):
            raise RuntimeError("team lesson ingest failed")
        return r

    def resolve(self, ticket, lessons):
        """Deterministic resolution: apply a cited team lesson when it matches,
        else the conservative cold default."""
        text = ticket["text"].lower()
        for lesson in lessons:
            lid = lesson["id"]
            content = (lesson.get("content") or "").lower()
            if ticket["cat"] == "billing" and "refund" in text and "60-day" in content:
                if "duplicate" in text:
                    return "refund_duplicate", lid
                return "manual_review", lid
            if ticket["cat"] == "technical" and "502" in content:
                if "502" in text and "deploy" in text and "roll back" in content:
                    return "rollback_deploy", lid
                if ("one user" in text or "single" in text) and "reset the session" in content:
                    return "reset_session", lid
        return COLD_RESOLUTION[ticket["cat"]], None


def run_teach(client, experiment, endpoint, api_key):
    intake = Agent(client, experiment, "intake")
    billing = Agent(client, experiment, "billing")
    reviewer = Agent(client, experiment, "review")

    # The reviewer wakes on the event stream, never polls.
    events = []
    listener = threading.Thread(
        target=subscribe_events,
        args=(endpoint, api_key, intake.run_id, {"context.handoff_created"}, 60, events),
        daemon=True)
    listener.start()

    specialists = {"billing": billing}
    for tid in ("T1", "T2", "T3"):
        ticket = TICKETS[tid]
        cat = classify(ticket["text"])
        if cat == "technical":
            technical = Agent(client, experiment, "technical")
            specialists["technical"] = technical
        specialist = specialists[cat]
        # Intake hands off structured work — findings and next step, not a transcript.
        content = (f"Findings: {ticket['text']} Classified: {cat}. "
                   f"Open question: does policy allow self-serve? Next: resolve per policy.")
        h = intake.handoff_to(cat, tid, content)
        emit("handoff", ticket=tid, handoff_id=(h or {}).get("handoff_id"),
             from_agent="intake", to_agent=cat)
        specialist_decided, cited = specialist.resolve(ticket, specialist.recall_team_lessons(
            f"team policy for {cat} ticket {tid}"))
        correct = specialist_decided == ticket["resolution"]
        emit("specialist_decision", ticket=tid, lane=cat, decision=specialist_decided,
             expected=ticket["resolution"], correct=correct, cited_lesson=cited)
        if not correct:
            # The reviewer (woken by the event) requests changes and teaches the lane.
            reviewer.feedback((h or {}).get("handoff_id"), "request_changes",
                              f"Correct resolution for {tid} is {ticket['resolution']}.",
                              reviewer="review")
            reviewer.teach_global_lesson(
                TEAM_LESSONS[cat],
                [f"{cat} resolution rules taught after ticket {tid}"])
            emit("review_correction", ticket=tid, lane=cat, verdict="request_changes",
                 team_lesson_written=True)

    listener.join(timeout=2)
    emit("events_seen", count=len(events), types=[e["type"] for e in events])


def run_evaluate(client, experiment):
    billing = Agent(client, experiment, "billing")
    totals = {}
    for arm in ("no_team_memory", "with_team_memory"):
        results = []
        for tid in ("H1", "H2", "H3", "H4"):
            ticket = TICKETS[tid]
            lessons = billing.recall_team_lessons(f"billing refund policy for {tid}") \
                if arm == "with_team_memory" else []
            decided, cited = billing.resolve(ticket, lessons)
            correct = decided == ticket["resolution"]
            results.append(correct)
            emit("resolution", ticket=tid, arm=arm, decided=decided,
                 expected=ticket["resolution"], correct=correct, cited_lesson=cited)
        totals[arm] = dict(correct=sum(results), of=len(results))
    emit("metrics", **totals)
    if totals["with_team_memory"]["correct"] <= totals["no_team_memory"]["correct"]:
        raise RuntimeError("No team-memory improvement measured; run teach with the "
                           "same experiment first")
    return totals


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("teach", "evaluate"))
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.experiment):
        parser.error("experiment must be 1–80 letters, digits, underscores or hyphens")
    if any(not os.getenv(k) for k in ("MUBIT_ENDPOINT", "MUBIT_API_KEY")):
        parser.error("Set MUBIT_ENDPOINT and MUBIT_API_KEY in .env. No local memory fallback.")

    from mubit import Client
    client = Client(endpoint=os.environ["MUBIT_ENDPOINT"], api_key=os.environ["MUBIT_API_KEY"],
                    transport="http", run_id=f"{VERSION}-{args.experiment}-intake",
                    timeout_ms=120000)
    emit("start", backend="Mubit", experiment=args.experiment, phase=args.phase)
    if args.phase == "teach":
        run_teach(client, args.experiment, os.environ["MUBIT_ENDPOINT"], os.environ["MUBIT_API_KEY"])
    else:
        run_evaluate(client, args.experiment)


if __name__ == "__main__":
    main()
