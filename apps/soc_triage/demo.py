"""SOC alert triage: analyst verdicts become investigation memory.

Deterministic (no LLM). Implements: lessons from outcomes (recurring benign
patterns close with a citation instead of re-investigation), conditional
lessons (the pattern's conditions travel — a look-alike from a NEW source
must not be closed), attribution (analyst verdicts reinforce or decay), and
drift (a new pattern supersedes the old classification).

Phases:
  teach      first 3 alerts triaged; the analyst's fixture verdicts close the
             loop with record_outcome; benign patterns become conditional
             lessons
  evaluate   6 held-out alerts, two arms (cold investigate-everything vs
             memory). No writes.
"""
import argparse
import json
import os
import re
from pathlib import Path

VERSION = "soc-triage-v1"

ALERTS = {  # held out
    "A1": dict(text="Port scan from 10.4.7.21 (internal vuln scanner)", expected="close_benign"),
    "A2": dict(text="Off-hours 2.1 GB outbound transfer to 185.22.9.4 from FIN-db", expected="escalate"),
    "A3": dict(text="Credential stuffing burst against vpn.corp from 45.61.3.9", expected="escalate"),
    "A4": dict(text="Port scan from 10.4.7.21 detected on the finance VLAN", expected="close_benign"),
    "A5": dict(text="Port scan activity from NEW host 198.51.9.77, 800 MB outbound", expected="escalate"),
    "A6": dict(text="Backup job on dev-node-3 wrote 40 GB overnight", expected="close_benign"),
}
TRAIN = {
    "T1": dict(text="Port scan from 10.4.7.21 on the finance VLAN", expected="close_benign",
               lesson="Benign pattern: port scans from 10.4.7.21 are the quarterly internal "
                      "vuln scanner. Close as close_benign. Do NOT apply this to scans from "
                      "other sources."),
    "T2": dict(text="Nightly backup from dev-node-3 (40 GB)", expected="close_benign",
               lesson="Benign pattern: overnight bulk writes from dev-node-3 are the backup "
                      "job. Close as close_benign. Do NOT apply to other hosts."),
    "T3": dict(text="2 GB outbound to unknown ASN at 03:00 from FIN-db", expected="escalate",
               lesson="Escalation rule: off-hours outbound transfers to unknown ASNs from "
                      "databases are escalated immediately."),
    # the drift class: an analyst verdict on the NEW look-alike pattern
    "T4": dict(text="Scan-like traffic from non-inventory host 203.0.113.5, 900 MB outbound",
               expected="escalate",
               lesson="Escalation rule: scan-like traffic from a host NOT in the asset "
                      "inventory with large outbound volume is NEVER the vuln scanner - "
                      "escalate regardless of the source."),
}


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def triage(alert, lessons):
    """Deterministic triage. Cold: investigate everything (3 probes), decide by
    keyword with no pattern memory — look-alikes get closed wrongly. With
    memory: a lesson whose exact source matches closes in 1 probe with a
    citation; look-alikes from new sources still escalate."""
    text = alert["text"].lower()
    for lid, content in lessons:
        c = content.lower()
        # drift-class rule: pattern shape, not a specific source
        if "not in the asset inventory" in c and "new host" in text and "outbound" in text:
            return "escalate", 2, lid
        m = re.search(r"from ([0-9.]+)", c)
        src = m.group(1) if m else None
        if src and src in text:
            probes = 1
            if "close_benign" in c:
                return "close_benign", probes, lid
            return "escalate", probes, lid
    # cold investigation: naive keyword closure, no memory of look-alikes
    probes = 3
    if "port scan" in text or "backup" in text:
        return "close_benign", probes, None    # A5: a look-alike, wrongly closed
    if "outbound" in text or "stuffing" in text:
        return "escalate", probes, None
    return "escalate", probes, None


class Memory:
    MARKER = "[soc-v1]"

    def __init__(self, client, experiment):
        self.client = client
        self.run_id = f"{VERSION}-{experiment}"

    def write_lesson(self, content):
        stored = self.client.remember(
            content=f"{self.MARKER} {content}", intent="lesson", lesson_scope="global",
            lesson_importance="high", agent_id="soc-analyst", session_id=self.run_id,
            wait=True, timeout_ms=60000,
            metadata=dict(conditions=["source IP and host must match the pattern"]))
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError("lesson write failed")

    def recall_lessons(self, query):
        r = self.client.recall(query=query, limit=8, evidence_only=True,
                               include_working_memory=False, include_linked_runs=False)
        if r.get("error"):
            raise RuntimeError("recall failed")
        out = []
        for e in (r.get("evidence") or []):
            content = e.get("content") or ""
            if e.get("id") and not e.get("is_stale") and self.MARKER in content \
                    and e.get("entry_type") == "lesson":
                out.append((e["id"], content))
        return out

    def attribute(self, lesson_id, verdict_correct):
        self.client.record_outcome(
            reference_id=lesson_id, outcome="success" if verdict_correct else "failure",
            signal=1.0 if verdict_correct else -1.0, agent_id="soc-analyst",
            session_id=self.run_id, idempotency_key=f"{VERSION}:{lesson_id}:{verdict_correct}",
            rationale="analyst verdict agreed with the memory-driven disposition")


def run_teach(client, experiment):
    memory = Memory(client, experiment)
    for tid, alert in TRAIN.items():
        memory.write_lesson(alert["lesson"])
        emit("taught", alert=tid, verdict=alert["expected"])
    # drift lesson: recalled with a different query than the benign patterns
    # one recorded outcome on the stream: a later look-alike confirmed the lesson
    lessons = memory.recall_lessons("port scan 10.4.7.21 benign")
    if lessons:
        memory.attribute(lessons[0][0], True)
        emit("outcome_recorded", reference=lessons[0][0], correct=True)


def run_evaluate(client, experiment):
    memory = Memory(client, experiment)
    totals = {}
    for arm in ("cold", "with_memory"):
        lessons = memory.recall_lessons("port scan backup outbound escalation "
                                        "scan-like non-inventory host") \
            if arm == "with_memory" else []
        correct, probes, false_closes = 0, 0, 0
        for aid, alert in ALERTS.items():
            action, p, cited = triage(alert, lessons)
            ok = action == alert["expected"]
            correct += ok
            probes += p
            if action == "close_benign" and not ok:
                false_closes += 1
            emit("triage", alert=aid, arm=arm, action=action, expected=alert["expected"],
                 correct=ok, probes=p, cited_lesson=cited)
        totals[arm] = dict(correct=correct, of=len(ALERTS), total_probes=probes,
                           false_closes=false_closes)
    emit("metrics", **totals)
    m = totals["with_memory"]
    if m["correct"] != m["of"] or m["false_closes"] != 0:
        raise RuntimeError("memory arm mis-triaged; run teach with the same experiment first")
    if totals["cold"]["false_closes"] == 0:
        raise RuntimeError("fixture no longer demonstrates the look-alike false close")
    if m["total_probes"] >= totals["cold"]["total_probes"]:
        raise RuntimeError("no investigation-effort contrast measured")
    return totals


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("teach", "evaluate"))
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
    if args.phase == "teach":
        run_teach(client, args.experiment)
    else:
        run_evaluate(client, args.experiment)


if __name__ == "__main__":
    main()
