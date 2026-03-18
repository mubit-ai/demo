#!/usr/bin/env python3
"""Conference demo with real LLM calls (Gemini) + Mubit memory.

Each agent uses Gemini to reason. Mubit stores their memories and
assembles context for their prompts. The demo shows how Mubit context
changes an LLM's behavior between attempt 1 (failure) and attempt 2 (success).
"""

import json
import os
import sys

from google import genai

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from _common import ENDPOINT, SESSION, make_client, pp

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
llm = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def ask_llm(role: str, prompt: str) -> str:
    """Call Gemini and return the text response."""
    print(f"\n  [{role} thinking...]")
    response = llm.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    text = response.text.strip()
    print(f"  [{role}]: {text[:300]}{'...' if len(text) > 300 else ''}")
    return text


def pause(msg: str = ""):
    input(
        f"\n{'─' * 50}\n  Press Enter to continue{f' ({msg})' if msg else ''}...\n{'─' * 50}\n"
    )


def act1_setup():
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  ACT 1: SETUP                                   ║")
    print("╚══════════════════════════════════════════════════╝\n")

    client = make_client(SESSION)
    print(f"Connected to Mubit")
    print(f"  endpoint:  {ENDPOINT}")
    print(f"  session:   {SESSION}")
    print(f"  LLM:       {GEMINI_MODEL}")
    return client


def act2_coordination(client):
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  ACT 2: MULTI-AGENT COORDINATION                ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # Register agents
    print("Registering agents...")
    client.register_agent(
        session_id=SESSION,
        agent_id="planner",
        role="planner",
        read_scopes=["fact", "rule", "lesson", "trace", "handoff", "feedback"],
        write_scopes=["fact", "rule", "trace", "lesson"],
        shared_memory_lanes=["knowledge", "history"],
    )
    client.register_agent(
        session_id=SESSION,
        agent_id="developer",
        role="developer",
        read_scopes=["fact", "rule", "lesson", "trace", "handoff"],
        write_scopes=["trace", "lesson"],
    )
    client.register_agent(
        session_id=SESSION,
        agent_id="reviewer",
        role="reviewer",
        read_scopes=["fact", "rule", "lesson", "trace", "handoff", "feedback"],
        write_scopes=["feedback", "lesson"],
    )
    agents = client.list_agents(session_id=SESSION)
    print(f"  Registered {len(agents.get('agents', []))} agents:")
    for a in agents.get("agents", []):
        print(f"    - {a.get('agent_id')} ({a.get('role')})")

    # Planner uses LLM to break down the task
    planner_output = ask_llm(
        "planner",
        "You are a tech lead planning a sprint task. The task is: "
        "'Implement token rotation for the auth service.' "
        "List the key requirements and constraints in 2-3 bullet points. Be concise.",
    )

    # Store planner's analysis as a fact in Mubit
    client.remember(
        session_id=SESSION,
        agent_id="planner",
        content=f"Task SPRINT-42: {planner_output}",
        intent="fact",
        importance="high",
        metadata={"task": "SPRINT-42", "component": "auth-service"},
    )
    client.remember(
        session_id=SESSION,
        agent_id="planner",
        content="Rule: Always run integration tests after modifying the auth module.",
        intent="rule",
        importance="high",
    )
    print("\n  Stored planner's analysis as fact + 1 rule in Mubit")

    # Handoff planner -> developer
    handoff_to_dev = client.handoff(
        session_id=SESSION,
        task_id="SPRINT-42",
        from_agent_id="planner",
        to_agent_id="developer",
        content="Implement token rotation. Key constraint: invalidate Redis cache first.",
        requested_action="execute",
        metadata={"priority": "high"},
    )
    assert handoff_to_dev.get("success"), f"handoff failed: {handoff_to_dev}"
    print(f"\n  Handoff planner -> developer: {handoff_to_dev.get('handoff_id')}")

    # Developer asks LLM to implement — WITHOUT Mubit context (first attempt)
    print("\n--- Developer implements WITHOUT prior lessons ---")
    dev_attempt_1 = ask_llm(
        "developer",
        "You are a developer. Implement token rotation for an auth service that uses "
        "Redis for caching and RS256 JWT signing. "
        "Describe the steps you would take in order. Be concise (3-5 steps).",
    )

    # Store the developer's (potentially wrong) implementation trace
    client.remember(
        session_id=SESSION,
        agent_id="developer",
        content=f"Attempt 1 implementation plan: {dev_attempt_1}",
        intent="trace",
        metadata={"task": "SPRINT-42", "attempt": 1},
    )
    print("\n  Stored developer's attempt 1 trace in Mubit")

    # Developer hands off to reviewer
    handoff_to_review = client.handoff(
        session_id=SESSION,
        task_id="SPRINT-42",
        from_agent_id="developer",
        to_agent_id="reviewer",
        content=f"Token rotation implementation plan: {dev_attempt_1}",
        requested_action="review",
    )
    assert handoff_to_review.get("success"), f"handoff failed: {handoff_to_review}"
    print(f"\n  Handoff developer -> reviewer: {handoff_to_review.get('handoff_id')}")

    return handoff_to_review, dev_attempt_1


def act3_failure(client, handoff_to_review, dev_attempt_1):
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  ACT 3: THE FAILURE                             ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # Reviewer uses LLM to evaluate the implementation
    reviewer_verdict = ask_llm(
        "reviewer",
        f"You are a senior security reviewer. A developer proposed this implementation "
        f"for token rotation:\n\n{dev_attempt_1}\n\n"
        f"The CRITICAL requirement is: Redis cache MUST be invalidated BEFORE the "
        f"signing key is rotated, otherwise stale tokens will be served for up to 5 minutes.\n\n"
        f"Evaluate: did the developer get the ordering right? "
        f"If not, explain the exact problem. Be concise.",
    )

    # Store reviewer's feedback via Mubit
    feedback_reject = client.feedback(
        session_id=SESSION,
        handoff_id=handoff_to_review.get("handoff_id"),
        verdict="request_changes",
        comments=reviewer_verdict,
        from_agent_id="reviewer",
    )
    assert feedback_reject.get("success"), f"feedback failed: {feedback_reject}"
    print("\n  Reviewer verdict stored in Mubit: REQUEST_CHANGES")

    # Record failure lesson
    client.remember(
        session_id=SESSION,
        agent_id="developer",
        content=f"Token rotation attempt 1 failed. Reviewer feedback: {reviewer_verdict}",
        intent="lesson",
        lesson_type="failure",
        lesson_scope="session",
        lesson_importance="high",
        metadata={"task": "SPRINT-42", "attempt": 1},
    )
    print("  Failure lesson stored in long-term memory")

    # Checkpoint
    cp = client.checkpoint(
        session_id=SESSION,
        label="post-failure-attempt-1",
        context_snapshot=f"Developer's first attempt was rejected. Reviewer said: {reviewer_verdict[:200]}",
        agent_id="developer",
        metadata={"task": "SPRINT-42"},
    )
    assert cp.get("success"), f"checkpoint failed: {cp}"
    print(f"  Checkpoint: {cp.get('checkpoint_id')}")


def act4_learning(client):
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  ACT 4: LEARNING FROM FAILURE                   ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # Reflect — Mubit analyzes all evidence and extracts lessons
    print("Mubit reflects on the session...")
    reflection = client.reflect(session_id=SESSION)
    print("  Reflection result:")
    pp(reflection)

    # Surface strategies
    print("\nSurfacing strategies...")
    strategies = client.surface_strategies(
        session_id=SESSION,
        lesson_types=["success", "failure"],
        max_strategies=5,
    )
    assert isinstance(strategies.get("strategies"), list), (
        f"strategies malformed: {strategies}"
    )
    print(f"  Found {len(strategies.get('strategies', []))} strategies")

    # Get context — THE AHA MOMENT
    # Mubit assembles context including the failure lesson
    print("\n--- Developer asks Mubit for context before retry ---")
    context = client.get_context(
        session_id=SESSION,
        query="How should I implement token rotation for the auth service?",
        agent_id="developer",
        entry_types=["fact", "rule", "lesson", "feedback"],
        mode="sections",
        sections=["facts", "lessons", "feedback"],
        max_token_budget=800,
    )
    context_block = context.get("context_block", "")
    print("  Mubit context block:")
    print(f"  ┌{'─' * 60}")
    for line in context_block.split("\n"):
        print(f"  │ {line}")
    print(f"  └{'─' * 60}")
    print(f"  Token budget used: {context.get('budget_used')}/{context.get('budget_used', 0) + context.get('budget_remaining', 0)}")

    has_lesson = any(
        s.get("entry_type") == "lesson" for s in context.get("sources", [])
    )
    print(f"  Lesson surfaced in context: {has_lesson}")

    # Developer retries WITH Mubit context fed into the LLM prompt
    print("\n--- Developer retries WITH Mubit context in prompt ---")
    dev_attempt_2 = ask_llm(
        "developer",
        f"You are a developer retrying a failed task. Here is what you know from "
        f"your memory system:\n\n{context_block}\n\n"
        f"Based on this context (especially the lessons learned), describe the correct "
        f"steps to implement token rotation for the auth service. Be concise (3-5 steps).",
    )

    # Store success trace
    client.remember(
        session_id=SESSION,
        agent_id="developer",
        content=f"Attempt 2 implementation plan (informed by lessons): {dev_attempt_2}",
        intent="trace",
        metadata={"task": "SPRINT-42", "attempt": 2},
    )
    print("\n  Stored attempt 2 trace in Mubit")

    # Record outcome against the failure lesson
    lessons = client.control.lessons({"run_id": SESSION, "limit": 10})
    failure_lesson = next(
        (l for l in lessons.get("lessons", []) if l.get("lesson_type") == "failure"),
        None,
    )
    if failure_lesson:
        outcome = client.record_outcome(
            session_id=SESSION,
            reference_id=failure_lesson["id"],
            outcome="success",
            signal=0.9,
            rationale="Retry succeeded after Mubit surfaced the failure lesson in context.",
            agent_id="developer",
        )
        assert outcome.get("success"), f"record_outcome failed: {outcome}"
        print("  Outcome recorded — lesson confidence updated")

    # Reviewer evaluates attempt 2
    print("\n--- Reviewer evaluates attempt 2 ---")
    reviewer_verdict_2 = ask_llm(
        "reviewer",
        f"You are a senior security reviewer. A developer revised their implementation "
        f"for token rotation after receiving feedback:\n\n{dev_attempt_2}\n\n"
        f"The CRITICAL requirement is: Redis cache MUST be invalidated BEFORE the "
        f"signing key is rotated.\n\n"
        f"Does this revised plan get the ordering right? Approve or request changes.",
    )

    handoff_retry = client.handoff(
        session_id=SESSION,
        task_id="SPRINT-42",
        from_agent_id="developer",
        to_agent_id="reviewer",
        content=f"Revised implementation: {dev_attempt_2}",
        requested_action="review",
    )
    feedback_approve = client.feedback(
        session_id=SESSION,
        handoff_id=handoff_retry.get("handoff_id"),
        verdict="approve",
        comments=reviewer_verdict_2,
        from_agent_id="reviewer",
    )
    assert feedback_approve.get("success"), f"feedback failed: {feedback_approve}"
    print("\n  Reviewer verdict stored: APPROVED")


def act5_wrapup(client):
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  ACT 5: WRAP-UP                                 ║")
    print("╚══════════════════════════════════════════════════╝\n")

    print("Memory health check...")
    health = client.memory_health(session_id=SESSION, limit=100)
    pp(health)


def main():
    client = act1_setup()
    pause("Act 2: Multi-Agent Coordination")

    handoff_to_review, dev_attempt_1 = act2_coordination(client)
    pause("Act 3: The Failure")

    act3_failure(client, handoff_to_review, dev_attempt_1)
    pause("Act 4: Learning from Failure")

    act4_learning(client)
    pause("Act 5: Wrap-up")

    act5_wrapup(client)

    print("\n=== Demo complete! ===")


if __name__ == "__main__":
    main()
