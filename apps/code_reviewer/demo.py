"""Code review agent: team conventions and incident-linked risk in memory.

Deterministic (no LLM). PRs are structured change records; the "CI" fixture
holds ground-truth violations. The cold reviewer flags only the obvious PII
leak; the memory-driven reviewer applies the team's conventions — including
the incident-linked rule from INC-1042 — and cites the lesson behind each
finding.

Implements: guardrail-style convention rules (always inject), lessons from
reviewer feedback, and incident-linked conditional lessons.

Phase: review (two arms in one process; no writes — conventions are seeded).
"""
import argparse
import json
import os
import re
from pathlib import Path

VERSION = "reviewer-v1"

CONVENTIONS = [
    "Convention: database migrations must include a rollback section.",
    "Convention: changes touching payments code must include a load-test tag. "
    "Linked incident INC-1042: an untested payments change dropped queue depth to zero.",
    "Convention: never log PII (email, phone, address) — block and request redaction.",
]

# PR fixture: attributes + ground-truth violations.
PRS = {
    "PR-1": dict(files=["db/migrations/add_index.sql"], migration=True, rollback=False,
                 payments=False, pii=None, violations=["missing rollback section"]),
    "PR-2": dict(files=["payments/worker.py"], migration=False, rollback=None,
                 payments=True, load_test=False, pii=None,
                 violations=["payments change without load-test tag (INC-1042)"]),
    "PR-3": dict(files=["api/user_search.py"], pii="email in debug log",
                 violations=["PII in logs"]),
    "PR-4": dict(files=["docs/readme.md"], violations=[]),
    "PR-5": dict(files=["payments/refund.py"], payments=True, load_test=True, pii=None,
                 violations=[]),
}


def emit(event, **fields):
    print(json.dumps(dict(event=flags, **fields))) if False else print(
        json.dumps(dict(event=event, **fields)), flush=True)


def review(pr, lessons):
    """Deterministic review. Returns (findings, cited, false_blockers).
    Cold: only the visible PII leak. Memory: every recalled convention is
    checked against the PR; findings cite the convention that fired."""
    findings, cited, false_blockers = [], [], 0
    for lid, content in lessons:
        c = content.lower()
        if "rollback" in c and pr.get("migration") and not pr.get("rollback"):
            findings.append(("missing rollback section", lid))
        elif "load-test" in c and pr.get("payments") and not pr.get("load_test"):
            findings.append(("payments change without load-test tag", lid))
        elif "pii" in c and pr.get("pii"):
            findings.append(("PII in logs", lid))
    if not findings and pr.get("pii"):
        # the cold reviewer still catches the loud, obvious leak
        findings.append(("PII in logs", None))
    if not findings and pr.get("migration") and lessons:
        # over-cautious memory could block a clean migration that HAS rollback
        pass
    if pr["id"] == "PR-4" and findings:
        false_blockers += len(findings)
    return findings, [f[1] for f in findings if f[1]], false_blockers


def run_review(client, experiment):
    memory_markers = ("convention", "inc-1042")
    # Seed conventions into memory (idempotent upserts) so both runs of the
    # demo start from the same store.
    for i, conv in enumerate(CONVENTIONS):
        client.remember(content=f"[rev-v1] {conv}", intent="lesson", lesson_scope="global",
                        lesson_importance="high", agent_id="tech-lead",
                        upsert_key=f"{VERSION}:conv:{i}", wait=True, timeout_ms=60000)
    totals = {}
    for arm in ("cold", "with_memory"):
        if arm == "with_memory":
            r = client.recall(query="review conventions migrations payments load-test pii incident",
                              limit=10, evidence_only=True, include_working_memory=False,
                              include_linked_runs=False)
            if r.get("error"):
                raise RuntimeError("recall failed")
            lessons = [(e["id"], e.get("content") or "")
                       for e in (r.get("evidence") or [])
                       if e.get("id") and "[rev-v1]" in (e.get("content") or "")
                       and not e.get("is_stale")]
        else:
            lessons = []
        caught, expected_total, cited_count, false_blockers = 0, 0, 0, 0
        for pid, pr in PRS.items():
            pr = dict(pr, id=pid)
            findings, cited, false_b = review(pr, lessons)
            caught += sum(1 for f in findings if any(
                v.split(" (")[0].lower() in f[0].lower() or
                f[0].lower().startswith(v.split(" ")[0].lower())
                for v in pr["violations"]))
            expected_total += len(pr["violations"])
            cited_count += len(cited)
            false_blockers += false_b
            emit("review", pr=pid, arm=arm,
                 findings=[f[0] for f in findings],
                 expected=pr["violations"], cited=[c[:8] if c else None for c in cited])
        totals[arm] = dict(violations_caught=caught, violations_total=expected_total,
                           findings_cited=cited_count, false_blockers=false_blockers)
    emit("metrics", **totals)
    m = totals["with_memory"]
    if m["violations_caught"] != m["violations_total"] or m["false_blockers"]:
        raise RuntimeError("memory reviewer missed violations or over-blocked")
    if totals["cold"]["violations_caught"] >= m["violations_caught"]:
        raise RuntimeError("no convention-memory contrast measured")
    return totals


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("review",))
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
    run_review(client, args.experiment)


if __name__ == "__main__":
    main()
