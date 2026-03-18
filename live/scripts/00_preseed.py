#!/usr/bin/env python3
"""Pre-seed script — run 60 seconds before the talk starts.

Warms the embedding pipeline and ensures a clean demo session.
Does NOT pre-register agents or store demo data (those happen live).
"""

import sys
import time

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _common import API_KEY, ENDPOINT, SESSION, make_client


def health_check() -> bool:
    try:
        r = requests.get(f"{ENDPOINT}/metrics", timeout=5)
        return r.status_code == 200
    except Exception as e:
        print(f"Health check failed: {e}")
        return False


def main():
    # 1. Verify Docker is up
    print("1. Checking Mubit server health...")
    if not health_check():
        print("   FAIL: Server not reachable. Start Docker first:")
        print("   docker compose -f docker-compose.demo.yml up -d")
        sys.exit(1)
    print("   OK: Server is healthy.\n")

    # 2. Create client
    client = make_client(SESSION)

    # 3. Clean slate — delete any previous demo session
    print("2. Cleaning previous demo session...")
    try:
        client.control.delete_run({"run_id": SESSION})
        print("   Deleted previous session.")
    except Exception:
        print("   No previous session to delete (clean start).")

    # 4. Warm the embedding pipeline with throwaway data
    print("\n3. Warming embedding pipeline...")
    warmup_session = "warmup:throwaway"
    client.set_run_id(warmup_session)

    start = time.time()
    client.remember(
        session_id=warmup_session,
        content="Warmup item: this text exists only to pre-warm the encoding pipeline.",
        intent="fact",
    )
    elapsed_store = time.time() - start

    start = time.time()
    client.recall(
        session_id=warmup_session,
        query="warmup query",
        limit=1,
    )
    elapsed_query = time.time() - start

    # Clean up warmup data
    try:
        client.control.delete_run({"run_id": warmup_session})
    except Exception:
        pass

    print(f"   Store latency:  {elapsed_store:.2f}s")
    print(f"   Query latency:  {elapsed_query:.2f}s")
    print("   Pipeline warm.\n")

    # 5. Reset client to demo session
    client.set_run_id(SESSION)

    print("=== Pre-seed complete. Ready for demo! ===")
    print(f"   Endpoint:  {ENDPOINT}")
    print(f"   Session:   {SESSION}")
    print(f"   Transport: http")


if __name__ == "__main__":
    main()
