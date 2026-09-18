"""Entry point: python -m orchestrator

Demonstrates an autonomous agent that uses Mubit memory as tools.
The LLM decides when to store, recall, checkpoint, and reflect — no hardcoded pipeline.

Session 1: Research fintech tech stack
Session 2: Build on Session 1's knowledge with a follow-up query
"""

import sys
import time
import uuid

from . import config
from .memory import Memory
from .agent import OrchestratorAgent


QUERY_1 = (
    "I'm building a fintech startup focused on cross-border B2B payments for "
    "SMBs in emerging markets. We need to handle multi-currency transactions, "
    "compliance with local regulations (KYC/AML), and real-time FX rates. "
    "What tech stack should I use? Consider payment processing, databases, "
    "backend framework, infrastructure, and compliance tooling. "
    "Budget is limited — we're pre-seed with $500K."
)

QUERY_2 = (
    "Following up on our previous fintech tech stack research — we've now "
    "raised a seed round ($3M) and need to add fraud detection and "
    "transaction monitoring to our cross-border payments platform. "
    "What tools and services should we integrate? Consider our existing "
    "stack decisions and budget constraints."
)


def _check_env():
    if not config.GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable is required.")
        sys.exit(1)
    if not config.MUBIT_API_KEY:
        print("Warning: MUBIT_API_KEY not set — memory tools will fail.")


def main():
    _check_env()

    session_1 = f"orch-{uuid.uuid4().hex[:8]}"
    session_2 = f"orch-{uuid.uuid4().hex[:8]}"

    print(f"{'='*70}")
    print(f"  Autonomous Orchestrator Agent — Mubit as Tools")
    print(f"  Endpoint: {config.MUBIT_ENDPOINT}")
    print(f"  Model:    {config.MODEL}")
    print(f"{'='*70}")

    # ================================================================
    # SESSION 1: Research fintech tech stack
    # ================================================================
    print(f"\n{'='*70}")
    print(f"  SESSION 1: Fintech Tech Stack Research")
    print(f"  Session: {session_1}")
    print(f"{'='*70}")

    memory1 = Memory(
        endpoint=config.MUBIT_ENDPOINT,
        api_key=config.MUBIT_API_KEY,
        session_id=session_1,
    )

    try:
        memory1.register_agent()
        print("  Agent registered.")
    except Exception as e:
        print(f"  register_agent(): {e}")

    agent1 = OrchestratorAgent(memory1)
    result1 = agent1.run(QUERY_1)

    print(f"\n{'='*70}")
    print(f"  SESSION 1: RECOMMENDATION")
    print(f"{'='*70}\n")
    print(result1)

    # ================================================================
    # BETWEEN SESSIONS: Wait for ingestion
    # ================================================================
    print(f"\n  Waiting 12 seconds for Mubit ingestion + embedding...")
    time.sleep(12)

    # ================================================================
    # SESSION 2: Follow-up query (cross-session memory)
    # ================================================================
    print(f"\n{'='*70}")
    print(f"  SESSION 2: Fraud Detection Follow-up (cross-session memory)")
    print(f"  Session: {session_2}")
    print(f"{'='*70}")

    memory2 = Memory(
        endpoint=config.MUBIT_ENDPOINT,
        api_key=config.MUBIT_API_KEY,
        session_id=session_2,
    )

    try:
        memory2.register_agent()
    except Exception as e:
        print(f"  register_agent(): {e}")

    agent2 = OrchestratorAgent(memory2)
    result2 = agent2.run(QUERY_2)

    print(f"\n{'='*70}")
    print(f"  SESSION 2: RECOMMENDATION")
    print(f"{'='*70}\n")
    print(result2)

    # ================================================================
    # SUMMARY
    # ================================================================
    print(f"\n{'='*70}")
    print(f"  HOW THIS WORKS")
    print(f"{'='*70}")
    print("""
  The orchestrator agent has Mubit APIs exposed as Gemini function-calling
  tools. Unlike the other demos, there is NO hardcoded pipeline — the LLM
  autonomously decides when to:

    recall_memory()          — check prior knowledge before researching
    store_memory()           — save findings as it discovers them
    get_assembled_context()  — get full context before making decisions
    set_goal()               — define what it's trying to achieve
    update_goal()            — mark goals complete
    create_checkpoint()      — save progress at milestones
    reflect_on_session()     — extract lessons after completing research
    archive_artifact()       — store final recommendations
    surface_strategies()     — find patterns across lessons
    check_memory_health()    — verify memory state

  Session 2 demonstrates cross-session memory: the agent recalls Session 1's
  fintech research and builds on it for the fraud detection follow-up.
    """)
    print(f"{'='*70}")
    print(f"  Sessions: {session_1}  {session_2}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
