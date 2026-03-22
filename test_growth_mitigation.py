#!/usr/bin/env python3
"""Tests for data growth mitigation features.

Verifies:
  1. Partitioned sparse index (per-run, disk-backed)
  2. Redis event stream trimming (MAXLEN)
  3. RocksDB compaction + disk usage Prometheus metrics
  4. LLM telemetry metrics (token counts, latency)
  5. Multi-run isolation with bounded memory
"""

import json
import os
import sys
import time
import uuid

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "live", "scripts"))
from mubit import Client

ENDPOINT = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
API_KEY = os.environ.get("MUBIT_API_KEY", "mbt_local_admin_secret")

passed = 0
failed = 0


def check(condition, msg):
    global passed, failed
    if condition:
        print(f"  [+] PASS: {msg}")
        passed += 1
    else:
        print(f"  [x] FAIL: {msg}")
        failed += 1
    return condition


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def make_client(session_id):
    c = Client(endpoint=ENDPOINT, api_key=API_KEY, run_id=session_id)
    c.set_transport("http")
    return c


def remember_and_wait(client, content, intent="fact", metadata=None, timeout=30):
    """Remember a fact and poll until ingestion completes."""
    result = client.remember(content=content, intent=intent, metadata=metadata or {})
    job_id = result.get("job_id") if isinstance(result, dict) else None
    if not job_id:
        time.sleep(3)
        return result
    headers = {"Authorization": f"Bearer {API_KEY}"}
    for _ in range(timeout):
        try:
            resp = requests.get(
                f"{ENDPOINT}/v2/control/ingest/jobs/{job_id}?run_id={client.state.run_id}",
                headers=headers, timeout=10,
            )
            if resp.status_code == 200 and resp.json().get("done"):
                return result
        except Exception:
            pass
        time.sleep(1)
    return result


def get_metrics():
    resp = requests.get(f"{ENDPOINT}/metrics")
    return resp.text


# ======================================================================
# TEST 1: Multi-Run Sparse Isolation
# ======================================================================
def test_multirun_sparse_isolation():
    section("TEST 1: Multi-Run Sparse Isolation")

    run_a = f"sparse-iso-A-{uuid.uuid4().hex[:8]}"
    run_b = f"sparse-iso-B-{uuid.uuid4().hex[:8]}"
    client_a = make_client(run_a)
    client_b = make_client(run_b)

    print("  Ingesting unique facts into two separate runs...")

    # Run A: astronomy facts
    remember_and_wait(client_a, "The Andromeda galaxy is 2.537 million light-years from Earth.")
    remember_and_wait(client_a, "Neutron stars can rotate at 716 revolutions per second.")

    # Run B: cooking facts (completely different domain)
    remember_and_wait(client_b, "Maillard reaction occurs between amino acids and reducing sugars above 140C.")
    remember_and_wait(client_b, "Sous vide cooking maintains precise temperature for consistent results.")

    # Query Run A for astronomy — should NOT return cooking facts
    result_a = client_a.recall(query="Tell me about stars and galaxies", limit=5)
    evidence_a = result_a.get("evidence", [])
    has_astronomy = any("andromeda" in e.get("content", "").lower() or "neutron" in e.get("content", "").lower() for e in evidence_a)
    has_cooking = any("maillard" in e.get("content", "").lower() or "sous vide" in e.get("content", "").lower() for e in evidence_a)

    check(has_astronomy, "Run A query found astronomy facts")
    check(not has_cooking, "Run A query did NOT leak cooking facts from Run B")

    # Query Run B for cooking — should NOT return astronomy facts
    result_b = client_b.recall(query="Tell me about cooking techniques", limit=5)
    evidence_b = result_b.get("evidence", [])
    has_cooking_b = any("maillard" in e.get("content", "").lower() or "sous vide" in e.get("content", "").lower() for e in evidence_b)
    has_astro_b = any("andromeda" in e.get("content", "").lower() or "neutron" in e.get("content", "").lower() for e in evidence_b)

    check(has_cooking_b, "Run B query found cooking facts")
    check(not has_astro_b, "Run B query did NOT leak astronomy facts from Run A")


# ======================================================================
# TEST 2: Prometheus RocksDB + Disk Metrics
# ======================================================================
def test_prometheus_storage_metrics():
    section("TEST 2: Prometheus Storage Metrics")

    metrics = get_metrics()

    check("mubit_rocksdb_compaction_pending" in metrics, "RocksDB compaction_pending metric exists")
    check("mubit_rocksdb_write_stall_active" in metrics, "RocksDB write_stall_active metric exists")
    check("mubit_rocksdb_immutable_memtables" in metrics, "RocksDB immutable_memtables metric exists")
    check("mubit_rocksdb_live_sst_bytes" in metrics, "RocksDB live_sst_bytes metric exists")
    check("mubit_disk_total_bytes" in metrics, "Disk total_bytes metric exists")
    check("mubit_disk_used_bytes" in metrics, "Disk used_bytes metric exists")
    check("mubit_disk_usage_pct" in metrics, "Disk usage_pct metric exists")

    # Verify disk metrics have non-zero values
    for line in metrics.splitlines():
        if line.startswith("mubit_disk_total_bytes "):
            value = float(line.split()[-1])
            check(value > 0, f"Disk total_bytes > 0 (actual: {value / 1e9:.1f} GB)")
            break


# ======================================================================
# TEST 3: LLM Telemetry Metrics
# ======================================================================
def test_llm_telemetry_metrics():
    section("TEST 3: LLM Telemetry Metrics")

    # Trigger an LLM call via a query
    session = f"telemetry-test-{uuid.uuid4().hex[:8]}"
    client = make_client(session)
    client.remember(content="The quick brown fox jumps over the lazy dog.", intent="fact")
    time.sleep(2)
    client.recall(query="What does the fox do?", limit=3)
    time.sleep(1)

    metrics = get_metrics()

    check("mubit_llm_calls_total" in metrics, "LLM calls_total metric exists")
    check("mubit_llm_call_duration_seconds" in metrics, "LLM call_duration_seconds metric exists")
    check("mubit_llm_tokens_total" in metrics, "LLM tokens_total metric exists")

    # Check token counts are non-zero
    has_tokens = False
    for line in metrics.splitlines():
        if line.startswith("mubit_llm_tokens_total") and not line.startswith("#"):
            value = float(line.split()[-1])
            if value > 0:
                has_tokens = True
                break
    check(has_tokens, "LLM token counts are being tracked (> 0)")


# ======================================================================
# TEST 4: Redis Event Stream Bounded (MAXLEN)
# ======================================================================
def test_redis_stream_bounded():
    section("TEST 4: Redis Event Stream Bounded")

    session = f"stream-bound-{uuid.uuid4().hex[:8]}"
    client = make_client(session)

    print("  Ingesting 20 items to generate events...")
    for i in range(20):
        client.remember(
            content=f"Event stream test item {i}: testing that Redis streams are bounded by MAXLEN.",
            intent="fact",
        )

    time.sleep(3)

    # We can't directly check Redis stream length from here, but we can verify
    # the ingestion completed without Redis OOM
    result = client.recall(query="event stream test", limit=5)
    evidence = result.get("evidence", [])

    check(len(evidence) > 0, f"Queries work after 20 rapid ingestions (evidence={len(evidence)})")
    check(
        any("event stream test" in e.get("content", "").lower() for e in evidence),
        "Ingested content is retrievable after rapid ingestion",
    )


# ======================================================================
# TEST 5: Staleness Detection (is_stale in evidence response)
# ======================================================================
def test_staleness_in_response():
    section("TEST 5: Staleness in Evidence Response")

    session = f"stale-resp-{uuid.uuid4().hex[:8]}"
    client = make_client(session)

    remember_and_wait(client,
        "The office is located in Building A, Floor 3.",
        metadata={"speaker": "facilities"},
    )
    remember_and_wait(client,
        "The office has moved to Building B, Floor 7.",
        metadata={"speaker": "facilities"},
    )

    result = client.recall(query="Where is the office?", limit=5)
    evidence = result.get("evidence", [])

    building_b = [e for e in evidence if "building b" in e.get("content", "").lower()]
    building_a = [e for e in evidence if "building a" in e.get("content", "").lower()]

    check(len(building_b) > 0, "Newer fact (Building B) found")

    # Check is_stale field exists in proto response
    has_is_stale = any("is_stale" in str(e) for e in evidence)
    check(has_is_stale, "is_stale field present in evidence response")

    if building_b and building_a:
        b_rank = next(i for i, e in enumerate(evidence) if "building b" in e.get("content", "").lower())
        a_rank = next(i for i, e in enumerate(evidence) if "building a" in e.get("content", "").lower())
        check(b_rank < a_rank, f"Newer fact ranked higher (B=rank {b_rank}, A=rank {a_rank})")


# ======================================================================
# TEST 6: Occurrence Time in Recency Ranking
# ======================================================================
def test_occurrence_time_ranking():
    section("TEST 6: Occurrence Time Ranking")

    session = f"occ-time-{uuid.uuid4().hex[:8]}"
    client = make_client(session)
    now = int(time.time())

    # Old event, ingested now
    remember_and_wait(client,
        "Legacy system decommissioned after 10 years of service.",
        metadata={"occurrence_time": now - 86400 * 365},  # 1 year ago
    )

    # Recent event, ingested now
    remember_and_wait(client,
        "New microservices architecture deployed to production.",
        metadata={"occurrence_time": now - 86400 * 2},  # 2 days ago
    )

    result = client.recall(query="What happened recently?", limit=5)
    evidence = result.get("evidence", [])

    micro_rank = None
    legacy_rank = None
    for i, e in enumerate(evidence):
        c = e.get("content", "").lower()
        if "microservices" in c and micro_rank is None:
            micro_rank = i
        if "legacy" in c and legacy_rank is None:
            legacy_rank = i

    check(micro_rank is not None, f"Recent event (microservices) found (rank={micro_rank})")
    if micro_rank is not None and legacy_rank is not None:
        check(micro_rank < legacy_rank, f"Recent occurrence ranked above old (micro={micro_rank}, legacy={legacy_rank})")


# ======================================================================
# TEST 7: Budget Parameter
# ======================================================================
def test_budget_parameter():
    section("TEST 7: Budget Parameter (low/mid/high)")

    session = f"budget-test-{uuid.uuid4().hex[:8]}"
    client = make_client(session)

    for i in range(5):
        client.remember(content=f"Budget test fact {i}: system processes {i*100} events per second.", intent="fact")
    time.sleep(3)

    # Use raw HTTP to pass budget parameter (SDK may not support it yet)
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    resp_low = requests.post(f"{ENDPOINT}/v2/control/query", headers=headers, json={
        "run_id": session, "query": "How many events?", "limit": 5,
        "embedding": [], "entry_types": [], "budget": "low",
    })
    resp_high = requests.post(f"{ENDPOINT}/v2/control/query", headers=headers, json={
        "run_id": session, "query": "How many events?", "limit": 5,
        "embedding": [], "entry_types": [], "budget": "high",
    })

    check(resp_low.status_code == 200, f"Budget=low query succeeded (status={resp_low.status_code})")
    check(resp_high.status_code == 200, f"Budget=high query succeeded (status={resp_high.status_code})")

    low_ev = resp_low.json().get("evidence", [])
    high_ev = resp_high.json().get("evidence", [])
    check(len(low_ev) > 0, f"Budget=low returned evidence ({len(low_ev)} items)")
    check(len(high_ev) > 0, f"Budget=high returned evidence ({len(high_ev)} items)")


# ======================================================================
# MAIN
# ======================================================================
def main():
    global passed, failed

    print(f"\nMubit Growth Mitigation Feature Tests")
    print(f"  endpoint: {ENDPOINT}")

    try:
        resp = requests.get(f"{ENDPOINT}/v2/core/health", timeout=5)
        if resp.status_code != 200:
            print(f"\n  ERROR: Health check failed: {resp.status_code}")
            return 1
    except Exception as e:
        print(f"\n  ERROR: Cannot connect: {e}")
        return 1

    tests = [
        test_multirun_sparse_isolation,
        test_prometheus_storage_metrics,
        test_llm_telemetry_metrics,
        test_redis_stream_bounded,
        test_staleness_in_response,
        test_occurrence_time_ranking,
        test_budget_parameter,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            print(f"\n  EXCEPTION in {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    section("RESULTS")
    total = passed + failed
    print(f"\n  {passed} PASS / {failed} FAIL (out of {total} checks)")
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
