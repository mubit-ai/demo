"""Memory hygiene: sleep-time consolidation and archive reversibility.

Deterministic (no LLM calls needed by this app; the server's deterministic
duplicate merge needs no LLM either). Implements three documented patterns:

- sleep-time-consolidation: near-duplicate guidance written six different
  ways merges into one canonical entry while the run is idle; scattered
  entity facts distill forward. The canonical answer must survive the merge.
- write-time-reconciliation: the six restatements share one upsert slot
  family, so re-writes update instead of appending.
- utility-weighted-forgetting: cold evidence moves to the archive tier and
  stays reachable by ID — archive + dereference proves reversibility.

Phases:
  seed        write the messy store; snapshot counts + health
  consolidate poll until the sweep merges duplicates; verify recall quality
  archive     archive cold artifacts, dereference them back

NOTE: this demo needs a fast consolidation server:
  MUBIT_CL_CONSOLIDATE_IDLE_SECS=5 MUBIT_CL_CONSOLIDATE_INTERVAL_SECS=5
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

VERSION = "librarian-v1"
ENTITY = "vendor_nexon"

DUPLICATES = [
    "Nexon ships restock orders within five business days of a purchase order.",
    "Restock orders from Nexon arrive in 5 business days after the PO.",
    "After a purchase order, Nexon restocks take five business days.",
    "Nexon lead time: restock in 5 business days from PO date.",
    "Purchase orders to Nexon are fulfilled within five business days.",
    "Five business days is the Nexon restock window once the PO is placed.",
]
ENTITY_FACTS = [
    "Nexon's tier-2 SLA credits 2% per late day.",
    "Nexon account contact is Dana Reyes (dana@nexon.example).",
    "Nexon offers a 12% volume discount above 500 units.",
    "Nexon ships from the Reno warehouse only.",
]
ARCHIVE_ITEMS = [
    dict(kind="export", content="2026-08 raw usage export CSV for account 77342 (7.2 MB)"),
    dict(kind="export", content="2026-07 raw usage export CSV for account 77342 (6.8 MB)"),
]


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def lesson_count(client, run_id):
    r = client.lessons(session_id=run_id, limit=100)
    lessons = (r or {}).get("lessons") or []
    return len(lessons)


def run_seed(client, experiment):
    run_id = f"{VERSION}-{experiment}"
    for i, text in enumerate(DUPLICATES):
        stored = client.remember(content=text, intent="lesson", lesson_type="observation",
                                 lesson_scope="run", agent_id="librarian",
                                 upsert_key=f"{VERSION}:{ENTITY}:leadtime:v{i}",
                                 item_id=f"dup-{i}", wait=True, timeout_ms=60000)
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError(f"seed {i} failed")
    for i, text in enumerate(ENTITY_FACTS):
        stored = client.remember(content=text, intent="fact", agent_id="librarian",
                                 item_id=f"fact-{i}", wait=True, timeout_ms=60000)
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError(f"entity fact {i} failed")
    before = lesson_count(client, run_id)
    health = client.memory_health() or {}
    emit("seed_done", lessons_before=before, stale_entries=health.get("stale_entries", 0))


def run_consolidate(client, experiment):
    run_id = f"{VERSION}-{experiment}"
    before = lesson_count(client, run_id)
    deadline = time.monotonic() + 150
    after = before
    while time.monotonic() < deadline:
        after = lesson_count(client, run_id)
        if after > before:
            break
        time.sleep(5)
    emit("consolidated", entries_before=before, entries_after=after,
         distilled=after - before)
    if after <= before:
        raise RuntimeError("consolidation produced no distilled entries within the timeout; "
                           "run the server with MUBIT_CL_CONSOLIDATE_IDLE_SECS=5 "
                           "MUBIT_CL_CONSOLIDATE_INTERVAL_SECS=5")
    # Recall quality must survive the reorganization: the canonical answer is
    # retrievable and the distilled observations are present.
    r = client.recall(query="How long do Nexon restock orders take after a purchase order?",
                      entry_types=["lesson"], limit=5, evidence_only=True,
                      include_working_memory=False, include_linked_runs=False,
                      prefer_current_run=True)
    evidence = [e for e in (r.get("evidence") or []) if e.get("id") and not e.get("is_stale")]
    ok = any("business day" in (e.get("content") or "").lower() for e in evidence)
    distilled = client.lessons(session_id=run_id, limit=100)
    names = [(l.get("content") or "") for l in (distilled.get("lessons") or [])]
    distilled_samples = [c for c in names if c not in DUPLICATES][:3]
    emit("recall_quality", hits=len(evidence), canonical_answer_survives=ok,
         distilled_samples=distilled_samples)
    if not ok:
        raise RuntimeError("the canonical answer was lost in consolidation")


def run_archive(client, experiment):
    run_id = f"{VERSION}-{experiment}"
    refs = []
    for item in ARCHIVE_ITEMS:
        r = client.archive(content=item["content"], artifact_kind=item["kind"],
                           session_id=run_id, agent_id="librarian")
        if r.get("error"):
            raise RuntimeError(f"archive failed: {r['error']}")
        ref = r.get("reference_id") or r.get("id")
        refs.append(str(ref))
        emit("archived", kind=item["kind"], reference=ref)
    for ref in refs:
        d = client.dereference(reference_id=ref, session_id=run_id)
        content = ((d or {}).get("evidence") or {}).get("content") or ""
        emit("dereferenced", reference=ref, recovered=bool(content))
        if not content:
            raise RuntimeError(f"dereference returned nothing for {ref}")


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("seed", "consolidate", "archive"))
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
    elif args.phase == "consolidate":
        run_consolidate(client, args.experiment)
    else:
        run_archive(client, args.experiment)


if __name__ == "__main__":
    main()
