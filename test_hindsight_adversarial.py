#!/usr/bin/env python3
"""Adversarial tests for Hindsight-inspired features.

These tests are specifically designed to EXPOSE whether features actually
work vs whether the original tests passed due to semantic similarity
confounds. Each test isolates a single feature and constructs scenarios
where semantic search alone would FAIL.

Based on LongMemEval categories:
  - Information extraction
  - Multi-session reasoning
  - Temporal reasoning
  - Knowledge updates (staleness)
  - Abstention

Sources:
  - LongMemEval: https://github.com/xiaowu0162/LongMemEval
  - Hindsight paper: https://arxiv.org/html/2512.12818v1
"""

import json
import os
import sys
import time
import uuid

import requests as _requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "live", "scripts"))

from mubit import Client

ENDPOINT = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
API_KEY = os.environ.get("MUBIT_API_KEY", "mbt_local_admin_secret")
SESSION = f"adversarial-{uuid.uuid4().hex[:8]}"


def make_client(session_id: str = SESSION) -> Client:
    client = Client(endpoint=ENDPOINT, api_key=API_KEY, run_id=session_id)
    client.set_transport("http")
    return client


def raw_query(session_id: str, **kwargs) -> dict:
    """Direct HTTP query endpoint call, bypassing SDK limitations.
    Supports all proto fields including min_timestamp, max_timestamp, budget.
    """
    body = {"run_id": session_id, **kwargs}
    resp = _requests.post(
        f"{ENDPOINT}/v2/control/query",
        json=body,
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    resp.raise_for_status()
    return resp.json()


def pp(label, obj):
    print(f"\n  {label}:")
    s = json.dumps(obj, indent=2, default=str)
    print(f"  {s[:800]}")


passed = 0
failed = 0
warnings = 0


def check(condition, msg, warn_only=False):
    global passed, failed, warnings
    if condition:
        print(f"  [+] PASS: {msg}")
        passed += 1
    elif warn_only:
        print(f"  [~] WARN: {msg}")
        warnings += 1
    else:
        print(f"  [x] FAIL: {msg}")
        failed += 1
    return condition


def section(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


# ======================================================================
# ADVERSARIAL TEST 1: Temporal Index Independence
#
# The ORIGINAL test stored "Alice moved to Seattle in January 2025" and
# queried "What happened in January 2025?" — this passes via SEMANTIC
# SIMILARITY because "January 2025" appears in both query and content.
#
# This test stores a fact with occurrence_time but NO temporal words in
# content. If temporal index works, it's found. If only semantic search
# works, it's invisible.
# ======================================================================
def test_temporal_index_independence():
    section("ADVERSARIAL 1: Temporal Index Independence")
    session_id = f"adversarial-temporal-{uuid.uuid4().hex[:8]}"
    client = make_client(session_id)

    print("  Setup: Store facts with occurrence_time but NO temporal words in content")

    # Fact 1: occurrence_time = Jan 15 2025, content has NO date references
    client.remember(
        content="The infrastructure team deployed a new load balancer configuration.",
        intent="fact",
        metadata={"occurrence_time": 1736899200},  # 2025-01-15 UTC
    )
    time.sleep(0.3)

    # Fact 2: occurrence_time = Mar 1 2025, content has NO date references
    client.remember(
        content="Database migration completed for the user profiles service.",
        intent="fact",
        metadata={"occurrence_time": 1740787200},  # 2025-03-01 UTC
    )
    time.sleep(0.3)

    # Fact 3: NO occurrence_time (control — ingested now, about unrelated topic)
    client.remember(
        content="The marketing team launched a new campaign for Q2.",
        intent="fact",
    )
    time.sleep(1.5)

    # Query 1: Temporal query — "What happened in January 2025?"
    # If temporal index works: should find fact 1 (load balancer)
    # If only semantic search: NO content mentions January, so it would
    # return marketing/database based on generic similarity
    print("\n  Query: 'What happened in January 2025?'")
    results = client.recall(query="What happened in January 2025?", limit=5)
    evidence = results.get("evidence", [])

    load_balancer_found = any(
        "load balancer" in e.get("content", "").lower() for e in evidence
    )
    check(
        load_balancer_found,
        "Temporal index found 'load balancer' fact via occurrence_time (no temporal words in content)",
    )

    # Query 2: Time-bounded recall (use raw HTTP to pass min/max_timestamp)
    print("  Query: recall with min/max timestamp for Jan 2025")
    results2 = raw_query(
        session_id,
        query="What technical changes were made?",
        limit=5,
        min_timestamp=1735689600,   # 2025-01-01
        max_timestamp=1738367999,   # 2025-01-31
    )
    evidence2 = results2.get("evidence", [])
    lb_in_bounded = any(
        "load balancer" in e.get("content", "").lower() for e in evidence2
    )
    db_not_in_bounded = not any(
        "database migration" in e.get("content", "").lower() for e in evidence2
    )
    check(
        lb_in_bounded,
        "Time-bounded recall found January fact (load balancer)",
    )
    check(
        db_not_in_bounded,
        "Time-bounded recall excluded March fact (database migration)",
        warn_only=True,  # Server may not yet filter on bounds
    )


# ======================================================================
# ADVERSARIAL TEST 2: Entity Canonicalization Isolation
#
# The ORIGINAL test stored facts about "Alice"/"Alice Chen"/"Dr. Chen"
# but all content ALSO contained the word "Chen" or "Alice". Semantic
# similarity would find them regardless of canonicalization.
#
# This test stores facts where entity aliases have ZERO lexical overlap
# with the query term. Only canonicalization can bridge the gap.
# ======================================================================
def test_entity_canonicalization_isolation():
    section("ADVERSARIAL 2: Entity Canonicalization Isolation")
    client = make_client()

    print("  Setup: Store facts with name variants that DON'T overlap with query")

    # Fact 1: Uses ONLY "Alice" (no surname)
    client.remember(
        content="Alice enjoys hiking in the mountains every weekend.",
        intent="fact",
        metadata={"speaker": "narrator"},
    )
    time.sleep(0.3)

    # Fact 2: Uses ONLY "Dr. Martinez" (completely different surface form)
    # But we'll also store that Alice's surname is Martinez
    client.remember(
        content="Alice Martinez received the outstanding researcher award.",
        intent="fact",
        metadata={"speaker": "HR"},
    )
    time.sleep(0.3)

    # Fact 3: Uses "Dr. Martinez" (no "Alice" anywhere)
    client.remember(
        content="Dr. Martinez published three papers on climate modeling.",
        intent="fact",
        metadata={"speaker": "academic"},
    )
    time.sleep(1.5)

    # Query using "Alice" — should find ALL three facts via canonicalization
    # Fact 1: "Alice" → direct match
    # Fact 2: "Alice Martinez" → links "Alice" to "Martinez"
    # Fact 3: "Dr. Martinez" → if canonicalized, "Martinez" maps to "Alice Martinez"
    print("\n  Query: 'Tell me about Alice'")
    results = client.recall(query="Tell me about Alice", limit=10)
    evidence = results.get("evidence", [])

    alice_hiking = any("hiking" in e.get("content", "").lower() for e in evidence)
    alice_award = any("award" in e.get("content", "").lower() for e in evidence)
    dr_martinez_papers = any(
        "papers" in e.get("content", "").lower()
        and "martinez" in e.get("content", "").lower()
        for e in evidence
    )

    check(alice_hiking, "Found 'Alice hiking' fact (direct name match)")
    check(alice_award, "Found 'Alice Martinez award' fact (full name match)")
    check(
        dr_martinez_papers,
        "Found 'Dr. Martinez papers' fact via entity alias (Alice → Martinez canonicalization)",
        warn_only=True,  # This is the hard case — requires alias table to link Dr. Martinez → Alice
    )

    # Reverse query: "Dr. Martinez" should find "Alice hiking"
    print("  Query: 'What does Dr. Martinez do in her free time?'")
    results2 = client.recall(query="What does Dr. Martinez do in her free time?", limit=10)
    evidence2 = results2.get("evidence", [])
    hiking_via_alias = any("hiking" in e.get("content", "").lower() for e in evidence2)
    check(
        hiking_via_alias,
        "Found 'Alice hiking' when querying 'Dr. Martinez' (reverse alias resolution)",
        warn_only=True,
    )


# ======================================================================
# ADVERSARIAL TEST 3: Graph Multi-Hop (No Direct Mention)
#
# The ORIGINAL test asked "What project is Alice working on?" and found
# "Alice designed the consensus protocol used in Project Phoenix" — but
# that fact DIRECTLY mentions both Alice and Phoenix. Semantic search
# alone finds it.
#
# This test constructs a chain where the answer requires 2+ hops:
# Entity A → Entity B → Target fact (A never appears with target)
# ======================================================================
def test_graph_multihop_no_direct():
    section("ADVERSARIAL 3: Graph Multi-Hop (No Direct Mention)")
    client = make_client()

    print("  Setup: Create entity chain requiring 2-hop traversal")

    # Fact 1: Bob works with Carol (Bob ↔ Carol link)
    client.remember(
        content="Bob Thompson and Carol Davis collaborate on the analytics platform daily.",
        intent="fact",
    )
    time.sleep(0.3)

    # Fact 2: Carol leads Project Zenith (Carol ↔ Zenith link)
    client.remember(
        content="Carol Davis is the technical lead for Project Zenith, a real-time streaming system.",
        intent="fact",
    )
    time.sleep(0.3)

    # Fact 3: Project Zenith detail (Zenith detail — no Bob, no Carol)
    client.remember(
        content="Project Zenith achieved 99.99% uptime in Q4 and processes 2 million events per second.",
        intent="fact",
    )
    time.sleep(0.3)

    # Distractor: Unrelated Bob fact
    client.remember(
        content="Bob Thompson completed his annual performance review with positive feedback.",
        intent="fact",
    )
    time.sleep(1.5)

    # Query: "What is Bob's team working on?"
    # 2-hop path: Bob → Carol (co-occurrence) → Project Zenith
    # Semantic search alone: "Bob" has NO direct connection to "Zenith" or "streaming"
    print("\n  Query: 'What project is Bob Thompson involved with?'")
    results = client.recall(query="What project is Bob Thompson involved with?", limit=10)
    evidence = results.get("evidence", [])

    zenith_found = any("zenith" in e.get("content", "").lower() for e in evidence)
    carol_found = any("carol" in e.get("content", "").lower() for e in evidence)

    check(carol_found, "Found Carol Davis fact (1-hop from Bob via co-occurrence)")
    check(
        zenith_found,
        "Found Project Zenith fact (2-hop: Bob → Carol → Zenith via graph traversal)",
        warn_only=True,  # Multi-hop graph may not reach the Zenith-only fact
    )

    # Print what we actually got
    for e in evidence[:5]:
        content = e.get("content", "")[:80]
        score = e.get("score", 0)
        print(f"    score={score:.3f} {content}")


# ======================================================================
# ADVERSARIAL TEST 4: Staleness / Knowledge Update
#
# The ORIGINAL test checked if both old and new facts exist. It did NOT
# verify that the old fact is actually deprioritized.
#
# This test stores contradicting facts and checks:
# 1. Does the stale metadata actually get set?
# 2. Is the newer fact ranked higher (via core search)?
# ======================================================================
def test_staleness_knowledge_update():
    section("ADVERSARIAL 4: Staleness / Knowledge Update")
    client = make_client(f"adversarial-staleness-{uuid.uuid4().hex[:8]}")

    print("  Setup: Store contradicting facts about same entity")

    # Old fact
    client.remember(
        content="The company headquarters is located in Boston, Massachusetts.",
        intent="fact",
        metadata={"speaker": "HR dept"},
    )
    time.sleep(1.5)  # Ensure ordering

    # New contradicting fact (same entity "company headquarters", different location)
    client.remember(
        content="The company headquarters relocated to Austin, Texas in early 2025.",
        intent="fact",
        metadata={"speaker": "HR dept"},
    )
    time.sleep(1.5)

    print("\n  Query: 'Where is the company headquarters?'")
    results = client.recall(query="Where is the company headquarters?", limit=5)
    evidence = results.get("evidence", [])

    austin_rank = None
    boston_rank = None
    for i, e in enumerate(evidence):
        c = e.get("content", "").lower()
        if "austin" in c and austin_rank is None:
            austin_rank = i
        if "boston" in c and boston_rank is None:
            boston_rank = i

    both_found = austin_rank is not None and boston_rank is not None
    check(both_found, f"Both facts found (austin=rank {austin_rank}, boston=rank {boston_rank})")

    if both_found:
        check(
            austin_rank < boston_rank,
            f"Newer fact (Austin, rank {austin_rank}) ranked above stale fact (Boston, rank {boston_rank})",
        )
    else:
        check(austin_rank is not None, "At least the newer fact (Austin) is found")

    # Check if stale metadata is propagated in evidence
    boston_entries = [e for e in evidence if "boston" in e.get("content", "").lower()]
    if boston_entries:
        meta_str = boston_entries[0].get("metadata_json", "")
        has_stale_meta = "is_stale" in meta_str
        check(
            has_stale_meta,
            "Boston fact has is_stale in metadata (staleness propagated to control service)",
            warn_only=True,
        )


# ======================================================================
# ADVERSARIAL TEST 5: Mental Model Priority (Not Just Existence)
#
# The ORIGINAL test checked if mental_model appears in get_context.
# But does it actually get PRIORITIZED over raw facts? If a fact and
# mental model both match, the mental model should come first.
#
# This test stores many competing facts + one mental model, then checks
# that mental model appears in position 1, not buried.
# ======================================================================
def test_mental_model_priority():
    section("ADVERSARIAL 5: Mental Model Priority Over Facts")
    client = make_client()

    print("  Setup: Store 8 facts and 1 mental model about same topic")

    # 8 individual facts
    facts = [
        "The ML team uses PyTorch for all training pipelines.",
        "Training runs are scheduled nightly on 8xA100 clusters.",
        "The current model achieves 94.2% accuracy on the internal benchmark.",
        "Data preprocessing takes approximately 3 hours per dataset.",
        "The team consists of 6 ML engineers and 2 data scientists.",
        "Model serving uses TensorRT for inference optimization.",
        "The latest model version was deployed on March 10th.",
        "GPU utilization averages 78% across all training jobs.",
    ]
    for f in facts:
        client.remember(content=f, intent="fact")
        time.sleep(0.2)

    # 1 mental model (comprehensive summary)
    client.remember(
        content=(
            "The ML team (6 engineers, 2 data scientists) runs nightly PyTorch training "
            "on 8xA100 clusters at 78% GPU utilization. Current model: 94.2% accuracy, "
            "served via TensorRT. Data prep: ~3h/dataset. Latest deploy: March 10th."
        ),
        intent="mental_model",
        importance="critical",
        metadata={"entity": "ml team", "consolidated": True},
    )
    time.sleep(1.5)

    print("\n  Query: get_context for 'ML team overview'")
    ctx = client.get_context(
        query="Give me an overview of the ML team and their infrastructure",
        limit=10,
        max_token_budget=400,  # Tight budget — forces prioritization
    )
    context_block = ctx.get("context_block", "") or ctx.get("context", "")

    # Mental model should appear BEFORE any raw facts
    mm_pos = context_block.find("Mental Model")
    facts_pos = context_block.find("Known Facts")
    if facts_pos == -1:
        facts_pos = context_block.find("Facts")

    has_mm_section = mm_pos >= 0
    mm_before_facts = mm_pos < facts_pos if (mm_pos >= 0 and facts_pos >= 0) else False

    check(has_mm_section, "Mental Models section exists in context")
    check(
        mm_before_facts,
        f"Mental Models section (pos {mm_pos}) appears BEFORE Facts section (pos {facts_pos})",
    )

    # Under tight budget, mental model should be included even if some facts are dropped
    budget_used = ctx.get("budget_used", 0)
    dropped = ctx.get("evidence_dropped_by_budget", 0)
    mental_model_in_block = "94.2% accuracy" in context_block and "TensorRT" in context_block
    check(
        mental_model_in_block,
        f"Mental model content present despite tight budget (used={budget_used}, dropped={dropped})",
    )


# ======================================================================
# ADVERSARIAL TEST 6: Occurrence Time vs Ingestion Time Disambiguation
#
# Hindsight explicitly tracks when events happened vs when the system
# learned about them. This test stores old events ingested NOW and
# new events with old occurrence_time, then queries to see which
# time dimension is used.
# ======================================================================
def test_dual_time_disambiguation():
    section("ADVERSARIAL 6: Occurrence Time vs Ingestion Time")
    client = make_client(f"adversarial-dualtime-{uuid.uuid4().hex[:8]}")

    now = int(time.time())

    print("  Setup: Store events with mismatched occurrence/ingestion times")

    # Event 1: Happened long ago, ingested now
    client.remember(
        content="Server migration to AWS completed with zero downtime.",
        intent="fact",
        metadata={"occurrence_time": now - 86400 * 180},  # 6 months ago
    )
    time.sleep(0.3)

    # Event 2: Happened recently, ingested now
    client.remember(
        content="New CI/CD pipeline reduced deployment time by 60%.",
        intent="fact",
        metadata={"occurrence_time": now - 86400 * 3},  # 3 days ago
    )
    time.sleep(0.3)

    # Event 3: No occurrence_time (ingestion time = now)
    client.remember(
        content="Documentation updated for the new API versioning scheme.",
        intent="fact",
    )
    time.sleep(1.5)

    # Query: "What happened recently?" should prefer event 2 (recent occurrence)
    # over event 1 (old occurrence) even though both were ingested at the same time
    print("\n  Query: 'What happened in the last week?'")
    results = client.recall(query="What happened in the last week?", limit=5)
    evidence = results.get("evidence", [])

    cicd_rank = None
    aws_rank = None
    for i, e in enumerate(evidence):
        c = e.get("content", "").lower()
        if "ci/cd" in c and cicd_rank is None:
            cicd_rank = i
        if "aws" in c and aws_rank is None:
            aws_rank = i

    check(
        cicd_rank is not None,
        f"Recent event (CI/CD, 3 days ago) found (rank={cicd_rank})",
    )
    if cicd_rank is not None and aws_rank is not None:
        check(
            cicd_rank < aws_rank,
            f"Recent occurrence (CI/CD, rank {cicd_rank}) ranked above old occurrence (AWS, rank {aws_rank})",
            warn_only=True,  # Dual time routing may not fully work yet
        )


# ======================================================================
# MAIN
# ======================================================================
def main():
    global passed, failed, warnings

    print(f"\nMubit Adversarial Feature Tests")
    print(f"  Purpose: Expose whether features ACTUALLY work vs semantic similarity confounds")
    print(f"  endpoint: {ENDPOINT}")
    print(f"  session:  {SESSION}")

    try:
        client = make_client()
        client.auth.health()
    except Exception as e:
        print(f"\n  ERROR: Cannot connect to Mubit at {ENDPOINT}: {e}")
        sys.exit(1)

    tests = [
        test_temporal_index_independence,
        test_entity_canonicalization_isolation,
        test_graph_multihop_no_direct,
        test_staleness_knowledge_update,
        test_mental_model_priority,
        test_dual_time_disambiguation,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            print(f"\n  EXCEPTION in {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    section("ADVERSARIAL RESULTS")
    total = passed + failed + warnings
    print(f"\n  {passed} PASS / {failed} FAIL / {warnings} WARN  (out of {total} checks)")
    print()
    if failed > 0:
        print("  Failures indicate features that are NOT yet working as intended.")
        print("  Warnings indicate features that work partially or may need deeper integration.")
    if warnings > 0:
        print(f"\n  {warnings} warnings represent known gaps between current implementation")
        print("  and the Hindsight architecture. These are tracked for future phases.")
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
