"""Entry point: python -m discovery"""

import sys
import time
import uuid

from . import config
from .memory import Memory
from .pipeline import DiscoveryPipeline


def _check_env():
    if not config.GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable is required.")
        sys.exit(1)
    if not config.MUBIT_API_KEY:
        print("Warning: MUBIT_API_KEY not set — memory calls may fail.")


def main():
    _check_env()

    session_1 = f"discovery-{uuid.uuid4().hex[:8]}"

    print(f"{'='*70}")
    print(f"  Software Discovery — Multi-Agent Pipeline")
    print(f"  Endpoint: {config.MUBIT_ENDPOINT}")
    print(f"  Model:    {config.MODEL}")
    print(f"{'='*70}\n")

    # ── Setup ──────────────────────────────────────────────────────────
    memory = Memory(
        endpoint=config.MUBIT_ENDPOINT,
        api_key=config.MUBIT_API_KEY,
        session_id=session_1,
    )
    pipeline = DiscoveryPipeline(memory)
    try:
        memory.register_agents(pipeline.all_agents())
        print("  Agents registered.\n")
    except Exception as e:
        print(f"  register_agents(): {e}\n")

    # ── Run 1 ──────────────────────────────────────────────────────────
    print(f"{'='*70}")
    print(f"  RUN 1: Series A SaaS — Full Payments Stack")
    print(f"  Session: {session_1}")
    print(f"{'='*70}")

    result1 = pipeline.run(
        "I'm a Series A B2B SaaS company with 50 employees. We process "
        "about $2M ARR through a mix of monthly and annual subscriptions. "
        "I need a complete payments and billing stack: payment processing, "
        "subscription billing, revenue recognition, and fraud detection. "
        "We currently use a basic Stripe integration but need to scale."
    )

    print(f"\n{'='*70}")
    print(f"  RUN 1: FINAL RECOMMENDATION")
    print(f"{'='*70}\n")
    print(result1)

    # ── Post Run 1: memory operations ──────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  memory_health() — Run 1")
    print(f"{'='*70}\n")
    try:
        memory.print_health()
    except Exception as e:
        print(f"  memory_health(): {e}")

    print(f"\n  Waiting 12 seconds for Mubit ingestion + embedding...")
    time.sleep(12)

    # ── Reflect on Run 1 ───────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  MUBIT: REFLECT + ANALYZE RUN 1")
    print(f"{'='*70}\n")

    print("  reflect() — extracting lessons from Run 1...")
    try:
        reflection = memory.reflect()
        memory.print_reflection(reflection)
    except Exception as e:
        print(f"  reflect(): {e}")

    print()
    print("  surface_strategies() — clustering patterns...")
    try:
        strategies = memory.strategies()
        memory.print_strategies(strategies)
    except Exception as e:
        print(f"  surface_strategies(): {e}")

    print()
    print("  get_context() — what Run 2 would see:")
    try:
        ctx = memory.get_context(
            "B2B SaaS payments billing subscription tools recommendation",
            max_tokens=1200,
        )
        memory.print_context(ctx)
    except Exception as e:
        print(f"  get_context(): {e}")

    # ── Run 2 ──────────────────────────────────────────────────────────
    session_2 = f"discovery-{uuid.uuid4().hex[:8]}"
    memory2 = Memory(
        endpoint=config.MUBIT_ENDPOINT,
        api_key=config.MUBIT_API_KEY,
        session_id=session_2,
    )
    pipeline2 = DiscoveryPipeline(memory2)
    try:
        memory2.register_agents(pipeline2.all_agents())
    except Exception as e:
        print(f"  register_agents(): {e}")

    print(f"\n{'='*70}")
    print(f"  RUN 2: Seed Stage SaaS — Budget Stack (cross-run memory)")
    print(f"  Session: {session_2}")
    print(f"{'='*70}")

    result2 = pipeline2.run(
        "I'm a seed-stage B2B SaaS with 10 employees. We're just starting "
        "to monetize our product — need payment processing and subscription "
        "billing. Budget is tight. We want something that can grow with us. "
        "What's the best stack to start with?"
    )

    print(f"\n{'='*70}")
    print(f"  RUN 2: FINAL RECOMMENDATION")
    print(f"{'='*70}\n")
    print(result2)

    # ── Final analysis ─────────────────────────────────────────────────
    time.sleep(8)

    print(f"\n{'='*70}")
    print(f"  MUBIT: FINAL ANALYSIS AFTER BOTH RUNS")
    print(f"{'='*70}\n")

    print("  reflect() on Run 2...")
    try:
        r2 = memory2.reflect()
        memory2.print_reflection(r2)
    except Exception as e:
        print(f"  reflect(): {e}")

    print()
    print("  surface_strategies() — patterns across BOTH runs...")
    try:
        strats = memory2.strategies()
        memory2.print_strategies(strats)
    except Exception as e:
        print(f"  surface_strategies(): {e}")

    print()
    print("  memory_health() — Run 2:")
    try:
        memory2.print_health()
    except Exception as e:
        print(f"  memory_health(): {e}")

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  MUBIT APIs USED")
    print(f"{'='*70}")
    print("""
  Via mubit-sdk (Client):
    remember()             — stored agent outputs as facts/lessons/traces
    recall()               — searched for prior research before each run
    reflect()              — extracted higher-order lessons from findings
    get_context()          — token-budgeted context assembly
    register_agent()       — registered 6 agents with roles
    handoff()              — agent-to-agent task handoffs
    feedback()             — recommender approved evaluator's matrix
    checkpoint()           — pipeline state snapshots
    record_outcome()       — success with reinforcement signal
    archive()              — exact-reference artifacts
    dereference()          — verified archive retrieval
    surface_strategies()   — clustered patterns across runs
    memory_health()        — entry counts + section confidence
    control.lessons()      — listed lessons for outcome recording
    """)
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
