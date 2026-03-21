"""
MuBit + LangGraph Example: Multi-Agent Code Review Pipeline

A StateGraph with 3 nodes reviews a code diff:
  - Planner: breaks the review into checklist items
  - Reviewer: evaluates each item (loops), stores findings in MuBit
  - Summarizer: assembles findings into a final review

MuBit store persists findings across steps and sessions.
Run 1 reviews a SQL-injection sample; Run 2 reviews a secrets-in-code
sample and benefits from findings stored during Run 1.

Requirements:
    pip install -r requirements.txt

Environment variables:
    GOOGLE_API_KEY   - Google AI API key (falls back to GEMINI_API_KEY)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key (default: empty for local dev)
"""

import json
import logging
import os
import time
import uuid
from typing import Annotated, TypedDict

logger = logging.getLogger(__name__)

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.store.base import PutOp, SearchOp

from mubit_langgraph import MubitStore


# -- Sample code diffs to review --

SAMPLE_DIFF = '''
def get_user_profile(user_id, db_connection):
    """Fetch user profile from database."""
    query = f"SELECT * FROM users WHERE id = {user_id}"
    result = db_connection.execute(query)
    row = result.fetchone()
    return {
        "id": row[0],
        "name": row[1],
        "email": row[2],
        "role": row[3],
        "password_hash": row[4],
    }


def update_user_email(user_id, new_email, db):
    query = f"UPDATE users SET email = '{new_email}' WHERE id = {user_id}"
    db.execute(query)
    db.commit()
    return True


def delete_user(user_id, db):
    db.execute(f"DELETE FROM users WHERE id = {user_id}")
    db.commit()
'''

SAMPLE_DIFF_2 = '''
import os

DB_PASSWORD = "super_secret_123"
API_SECRET = "sk-prod-abc123xyz"

def authenticate(request):
    token = request.headers.get("Authorization")
    if token == API_SECRET:
        return True
    return False

def get_config():
    return {
        "db_host": "prod-db.internal",
        "db_password": DB_PASSWORD,
        "debug": True,
    }
'''


# -- State schema --

class ReviewState(TypedDict):
    code_diff: str
    checklist: list[str]
    current_idx: int
    findings: list[str]
    final_review: str


# -- Globals set per-run --
NAMESPACE = None
llm = None
mubit_store = None


def planner_node(state: ReviewState) -> dict:
    """Analyze the code diff and produce a review checklist."""
    store = mubit_store

    # Search for past review patterns
    past = store.batch([SearchOp(
        namespace_prefix=NAMESPACE,
        query="code review checklist security issues",
        limit=3,
    )])[0]

    past_context = ""
    if past:
        past_context = "\n\nPast review findings to consider:\n" + "\n".join(
            f"- {item.value.get('text', '')}" for item in past
        )

    response = llm.invoke([
        {"role": "system", "content": (
            "You are a senior code reviewer. Analyze the code diff and produce a "
            "checklist of specific items to review. Each item should be a single, "
            "concrete concern. Return a JSON array of strings, nothing else."
            f"{past_context}"
        )},
        {"role": "user", "content": f"Code diff to review:\n```python\n{state['code_diff']}\n```"},
    ])

    try:
        checklist = json.loads(response.content)
    except json.JSONDecodeError:
        # Extract JSON array from response if wrapped in markdown
        content = response.content
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        checklist = json.loads(content.strip())

    store.checkpoint(NAMESPACE, snapshot=f"Checklist created with {len(checklist)} items")

    print(f"\n[Planner] Created {len(checklist)} review items:")
    for i, item in enumerate(checklist):
        print(f"  {i+1}. {item}")

    # Record planner -> reviewer handoff
    try:
        store.handoff(
            NAMESPACE,
            from_agent_id="planner",
            to_agent_id="reviewer",
            content=f"Created {len(checklist)} review items.",
            requested_action="review",
        )
        print("[Planner] Handoff to reviewer recorded.")
    except Exception as e:
        logger.debug("handoff note: %s", e)

    return {"checklist": checklist, "current_idx": 0, "findings": []}


def reviewer_node(state: ReviewState) -> dict:
    """Evaluate one checklist item, store finding in MuBit."""
    store = mubit_store
    idx = state["current_idx"]
    item = state["checklist"][idx]

    response = llm.invoke([
        {"role": "system", "content": (
            "You are a meticulous code reviewer. Evaluate the code against the "
            "specific review item. Provide a concise finding: what's wrong (if anything), "
            "severity (critical/warning/info), and a suggested fix. Be specific with line references."
        )},
        {"role": "user", "content": (
            f"Code:\n```python\n{state['code_diff']}\n```\n\n"
            f"Review item: {item}"
        )},
    ])

    finding = response.content

    # Store finding in MuBit
    finding_key = f"finding-{idx}-{uuid.uuid4().hex[:6]}"
    store.batch([PutOp(
        namespace=NAMESPACE,
        key=finding_key,
        value={
            "text": finding,
            "intent": "lesson",
            "metadata": {"review_item": item, "index": idx},
            "occurrence_time": int(time.time()),
        },
    )])

    print(f"\n[Reviewer] Item {idx+1}/{len(state['checklist'])}: {item[:60]}...")
    print(f"  Finding: {finding[:100]}...")

    new_findings = list(state["findings"]) + [finding]
    return {"findings": new_findings, "current_idx": idx + 1}


def summarizer_node(state: ReviewState) -> dict:
    """Assemble all findings into a final review."""
    store = mubit_store

    # Record reviewer -> summarizer handoff
    try:
        store.handoff(
            NAMESPACE,
            from_agent_id="reviewer",
            to_agent_id="summarizer",
            content=f"Completed {len(state['findings'])} findings.",
            requested_action="execute",
        )
        print("[Summarizer] Handoff from reviewer recorded.")
    except Exception as e:
        logger.debug("handoff note: %s", e)

    # Get assembled context from MuBit (including mental_model entries)
    context = store.get_context(
        NAMESPACE,
        query="code review findings security vulnerabilities",
        max_token_budget=4096,
        entry_types=["mental_model", "fact", "lesson", "rule", "feedback"],
    )
    mubit_context = context.get("context_block", "")

    findings_text = "\n\n".join(
        f"### Finding {i+1}\n{f}" for i, f in enumerate(state["findings"])
    )

    response = llm.invoke([
        {"role": "system", "content": (
            "You are a senior engineering lead writing a final code review summary. "
            "Synthesize all findings into a clear, actionable review with:\n"
            "1. Executive summary (1-2 sentences)\n"
            "2. Critical issues (must fix before merge)\n"
            "3. Warnings (should fix)\n"
            "4. Suggestions (nice to have)\n"
            "5. Overall recommendation: approve / request changes / reject\n\n"
            f"Additional context from past reviews:\n{mubit_context}" if mubit_context else ""
        )},
        {"role": "user", "content": f"Individual findings:\n\n{findings_text}"},
    ])

    # Record outcome
    try:
        store.record_outcome(
            NAMESPACE,
            reference_id=f"review-{uuid.uuid4().hex[:8]}",
            outcome="success",
            rationale="Code review completed with all checklist items evaluated.",
        )
    except Exception as e:
        logger.debug("record_outcome note: %s", e)

    # Store a mental model summarizing key review patterns
    try:
        store.batch([PutOp(
            namespace=NAMESPACE,
            key=f"mental-model-{uuid.uuid4().hex[:6]}",
            value={
                "text": f"Code review summary: {response.content[:500]}",
                "intent": "mental_model",
                "importance": "critical",
                "metadata": {"consolidated": True},
            },
        )])
    except Exception as e:
        logger.debug("mental model store note: %s", e)

    print(f"\n[Summarizer] Final review assembled.")

    return {"final_review": response.content}


def should_continue(state: ReviewState) -> str:
    """Route to reviewer if more items remain, otherwise to summarizer."""
    if state["current_idx"] < len(state["checklist"]):
        return "reviewer"
    return "summarizer"


def run_review(store: MubitStore, code_diff: str, label: str) -> None:
    """Run the full review pipeline for a given code diff."""
    global NAMESPACE

    # Session-specific namespace so each run has its own memory lane,
    # but SearchOp with namespace_prefix still finds cross-run data.
    session_id = uuid.uuid4().hex[:8]
    NAMESPACE = ("memories", "code-reviewer", f"review-{session_id}")

    # Register agents for this session
    for agent_id, role in [
        ("planner", "review-planner"),
        ("reviewer", "item-reviewer"),
        ("summarizer", "review-summarizer"),
    ]:
        store.register_agent(NAMESPACE, agent_id=agent_id, role=role)

    # Build the graph
    graph = StateGraph(ReviewState)
    graph.add_node("planner", planner_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("summarizer", summarizer_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "reviewer")
    graph.add_conditional_edges("reviewer", should_continue, {
        "reviewer": "reviewer",
        "summarizer": "summarizer",
    })
    graph.add_edge("summarizer", END)

    compiled = graph.compile()

    # Run the review
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    result = compiled.invoke(
        {"code_diff": code_diff.strip(), "checklist": [], "current_idx": 0, "findings": [], "final_review": ""},
    )

    # Print final review
    print(f"\n{'='*60}")
    print(f"  Final Code Review  ({label})")
    print(f"{'='*60}\n")
    print(result["final_review"])
    print()


def main():
    global llm, mubit_store

    endpoint = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
    api_key = os.environ.get("MUBIT_API_KEY") or os.environ.get("MUBIT_BOOTSTRAP_ADMIN_API_KEY", "")
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    if not google_key:
        print("Error: GOOGLE_API_KEY (or GEMINI_API_KEY) environment variable is required.")
        sys.exit(1)

    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0.2, google_api_key=google_key)

    # Set up shared MuBit store
    store = MubitStore(endpoint=endpoint, api_key=api_key)
    mubit_store = store

    # --- Run 1: SQL injection sample ---
    run_review(store, SAMPLE_DIFF, "Run 1 — SQL Injection Review")

    # Wait for MuBit ingestion so Run 2's SearchOp can find Run 1 findings
    print(f"\n{'~'*60}")
    print("  Waiting 8 seconds for MuBit ingestion before Run 2...")
    print(f"{'~'*60}")
    time.sleep(8)

    # --- Run 2: Secrets-in-code sample ---
    run_review(store, SAMPLE_DIFF_2, "Run 2 — Secrets in Code Review")


if __name__ == "__main__":
    main()
