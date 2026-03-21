#!/usr/bin/env python3
"""Concurrent agent load test for mubit scaling verification.

Simulates N agents (default 200) each performing remember + recall cycles
against a single mubit instance. Measures throughput, latency percentiles,
error rates, and Redis connection stability.

Usage:
    # Quick smoke (20 agents, 3 cycles)
    python3 demo/test_concurrent_load.py --agents 20 --cycles 3

    # Full 200-agent test
    python3 demo/test_concurrent_load.py --agents 200 --cycles 5

    # Custom endpoint
    MUBIT_ENDPOINT=http://host:3000 python3 demo/test_concurrent_load.py
"""

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
import uuid

import httpx

ENDPOINT = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
API_KEY = os.environ.get("MUBIT_API_KEY", "mbt_local_admin_secret")


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = min(math.ceil(rank), len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def latency_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
    return {
        "count": len(values),
        "avg": round(statistics.mean(values)),
        "p50": round(percentile(values, 0.50)),
        "p95": round(percentile(values, 0.95)),
        "p99": round(percentile(values, 0.99)),
        "max": round(max(values)),
    }


class AgentResult:
    def __init__(self, agent_id: int):
        self.agent_id = agent_id
        self.ingest_latencies: list[float] = []
        self.query_latencies: list[float] = []
        self.ingest_errors: int = 0
        self.query_errors: int = 0


async def run_agent(
    client: httpx.AsyncClient,
    agent_id: int,
    run_id: str,
    cycles: int,
    use_shared_run: bool,
) -> AgentResult:
    """Simulate one agent doing remember + recall cycles."""
    result = AgentResult(agent_id)
    agent_run = run_id if use_shared_run else f"{run_id}-agent-{agent_id}"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    for cycle in range(cycles):
        # --- Ingest (remember) ---
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{ENDPOINT}/v2/control/ingest",
                headers=headers,
                json={
                    "run_id": agent_run,
                    "agent_id": f"load-agent-{agent_id}",
                    "parallel": True,
                    "items": [
                        {
                            "item_id": f"load-{agent_id}-{cycle}-{i}",
                            "content_type": "text",
                            "text": f"Agent {agent_id} cycle {cycle} fact {i}: "
                            f"The system processed {agent_id * 100 + i} requests "
                            f"with a latency of {cycle * 10 + i}ms on node {agent_id % 8}.",
                            "payload_json": "",
                            "hints_json": "",
                            "metadata_json": json.dumps(
                                {
                                    "agent_id": f"load-agent-{agent_id}",
                                    "cycle": cycle,
                                    "entry_type": "fact",
                                }
                            ),
                            "intent": "fact",
                        }
                        for i in range(3)
                    ],
                },
                timeout=30.0,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code == 200:
                # Poll for job completion
                body = resp.json()
                job_id = body.get("job_id", "")
                if job_id:
                    for _ in range(60):  # up to 30s
                        poll = await client.get(
                            f"{ENDPOINT}/v2/control/ingest/jobs/{job_id}?run_id={agent_run}",
                            headers=headers,
                            timeout=10.0,
                        )
                        if poll.status_code == 200:
                            job = poll.json()
                            if job.get("done"):
                                break
                        await asyncio.sleep(0.5)
                latency_ms = (time.monotonic() - t0) * 1000
                result.ingest_latencies.append(latency_ms)
            else:
                result.ingest_errors += 1
        except Exception:
            result.ingest_errors += 1

        # Brief pause to let ingestion settle
        await asyncio.sleep(0.1)

        # --- Query (recall) ---
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{ENDPOINT}/v2/control/query",
                headers=headers,
                json={
                    "run_id": agent_run,
                    "query": f"What is the latency on node {agent_id % 8}?",
                    "schema": "",
                    "mode": "agent_routed",
                    "limit": 5,
                    "embedding": [],
                    "entry_types": [],
                    "include_working_memory": False,
                },
                timeout=30.0,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code == 200:
                result.query_latencies.append(latency_ms)
            else:
                result.query_errors += 1
        except Exception:
            result.query_errors += 1

    return result


async def get_redis_connections() -> int | None:
    """Try to get Redis connected_clients count."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "mubit-local-redis",
            "redis-cli",
            "INFO",
            "clients",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        for line in stdout.decode().splitlines():
            if line.startswith("connected_clients:"):
                return int(line.split(":")[1].strip())
    except Exception:
        pass
    return None


async def get_prometheus_metric(client: httpx.AsyncClient, name: str) -> str | None:
    """Fetch a specific metric from Prometheus endpoint."""
    try:
        resp = await client.get(f"{ENDPOINT}/metrics", timeout=5.0)
        for line in resp.text.splitlines():
            if line.startswith(name) and not line.startswith("#"):
                return line
    except Exception:
        pass
    return None


async def main():
    parser = argparse.ArgumentParser(description="Concurrent agent load test")
    parser.add_argument("--agents", type=int, default=200, help="Number of concurrent agents")
    parser.add_argument("--cycles", type=int, default=5, help="Remember+recall cycles per agent")
    parser.add_argument(
        "--shared-run",
        action="store_true",
        help="All agents share one run_id (worst case for write contention)",
    )
    parser.add_argument("--batch-size", type=int, default=50, help="Agent launch batch size")
    args = parser.parse_args()

    run_id = f"load-test-{uuid.uuid4().hex[:8]}"
    print(f"\nMubit Concurrent Agent Load Test")
    print(f"  endpoint:    {ENDPOINT}")
    print(f"  agents:      {args.agents}")
    print(f"  cycles:      {args.cycles}")
    print(f"  shared_run:  {args.shared_run}")
    print(f"  run_id:      {run_id}")
    print(f"  total ops:   {args.agents * args.cycles} ingests + {args.agents * args.cycles} queries")

    # Health check
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{ENDPOINT}/v2/core/health", timeout=5.0)
            if resp.status_code != 200:
                print(f"\n  ERROR: Health check failed: {resp.status_code}")
                return 1
        except Exception as e:
            print(f"\n  ERROR: Cannot connect to {ENDPOINT}: {e}")
            return 1

    redis_before = await get_redis_connections()
    print(f"\n  Redis connections (before): {redis_before or 'unknown'}")

    # Run the load test
    print(f"\n  Starting {args.agents} agents...")
    wall_start = time.monotonic()

    # Use connection pooling in httpx
    limits = httpx.Limits(
        max_connections=min(args.agents, 100),
        max_keepalive_connections=min(args.agents, 50),
    )
    async with httpx.AsyncClient(limits=limits) as client:
        # Launch agents in batches to avoid overwhelming the connection pool
        all_results: list[AgentResult] = []
        for batch_start in range(0, args.agents, args.batch_size):
            batch_end = min(batch_start + args.batch_size, args.agents)
            batch_size = batch_end - batch_start
            tasks = [
                run_agent(client, i, run_id, args.cycles, args.shared_run)
                for i in range(batch_start, batch_end)
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in batch_results:
                if isinstance(r, AgentResult):
                    all_results.append(r)
                else:
                    print(f"  Agent exception: {r}")

            done = len(all_results)
            print(f"  Batch {batch_start // args.batch_size + 1}: "
                  f"{done}/{args.agents} agents completed")

        # Check Redis mid-test
        redis_during = await get_redis_connections()

    wall_elapsed = time.monotonic() - wall_start
    redis_after = await get_redis_connections()

    # Aggregate results
    all_ingest = []
    all_query = []
    total_ingest_errors = 0
    total_query_errors = 0

    for r in all_results:
        all_ingest.extend(r.ingest_latencies)
        all_query.extend(r.query_latencies)
        total_ingest_errors += r.ingest_errors
        total_query_errors += r.query_errors

    ingest_summary = latency_summary(all_ingest)
    query_summary = latency_summary(all_query)

    total_ops = len(all_ingest) + len(all_query)
    ops_per_sec = total_ops / wall_elapsed if wall_elapsed > 0 else 0

    # Print report
    print(f"\n{'=' * 60}")
    print(f"  LOAD TEST RESULTS")
    print(f"{'=' * 60}")
    print(f"\n  Wall time:     {wall_elapsed:.1f}s")
    print(f"  Agents:        {args.agents}")
    print(f"  Total ops:     {total_ops} ({len(all_ingest)} ingests + {len(all_query)} queries)")
    print(f"  Throughput:    {ops_per_sec:.1f} ops/sec")
    print(f"  Errors:        {total_ingest_errors} ingest + {total_query_errors} query")

    print(f"\n  INGEST LATENCY (ms):")
    print(f"    count={ingest_summary['count']}  avg={ingest_summary['avg']}  "
          f"p50={ingest_summary['p50']}  p95={ingest_summary['p95']}  "
          f"p99={ingest_summary['p99']}  max={ingest_summary['max']}")

    print(f"\n  QUERY LATENCY (ms):")
    print(f"    count={query_summary['count']}  avg={query_summary['avg']}  "
          f"p50={query_summary['p50']}  p95={query_summary['p95']}  "
          f"p99={query_summary['p99']}  max={query_summary['max']}")

    print(f"\n  REDIS CONNECTIONS:")
    print(f"    before={redis_before or '?'}  during={redis_during or '?'}  after={redis_after or '?'}")

    # Fetch Prometheus metrics
    async with httpx.AsyncClient() as client:
        llm_calls = await get_prometheus_metric(client, "mubit_llm_calls_total")
        llm_retries = await get_prometheus_metric(client, "mubit_llm_retries_total")
        degraded = await get_prometheus_metric(client, "mubit_agent_degraded_total")

    if llm_calls or llm_retries or degraded:
        print(f"\n  TELEMETRY:")
        if llm_calls:
            print(f"    {llm_calls}")
        if llm_retries:
            print(f"    {llm_retries}")
        if degraded:
            print(f"    {degraded}")

    # Pass/fail thresholds
    print(f"\n{'=' * 60}")
    print(f"  CHECKS")
    print(f"{'=' * 60}")

    checks_passed = 0
    checks_total = 0

    def check(condition: bool, msg: str):
        nonlocal checks_passed, checks_total
        checks_total += 1
        if condition:
            checks_passed += 1
            print(f"  [+] PASS: {msg}")
        else:
            print(f"  [x] FAIL: {msg}")

    check(
        total_ingest_errors == 0,
        f"Zero ingest errors ({total_ingest_errors} errors out of {len(all_ingest) + total_ingest_errors} attempts)",
    )
    check(
        total_query_errors == 0,
        f"Zero query errors ({total_query_errors} errors out of {len(all_query) + total_query_errors} attempts)",
    )
    check(
        ingest_summary["p99"] < 30_000,
        f"Ingest p99 < 30s (actual: {ingest_summary['p99']}ms)",
    )
    check(
        query_summary["p99"] < 15_000,
        f"Query p99 < 15s (actual: {query_summary['p99']}ms)",
    )
    if redis_before and redis_after:
        check(
            redis_after < redis_before + 10,
            f"Redis connections stable (before={redis_before}, after={redis_after}, delta={redis_after - redis_before})",
        )
    check(
        ops_per_sec > 1.0,
        f"Throughput > 1 ops/sec (actual: {ops_per_sec:.1f})",
    )

    print(f"\n  {checks_passed}/{checks_total} checks passed")
    print()

    return 0 if checks_passed == checks_total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
