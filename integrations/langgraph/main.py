"""
MuBit + LangGraph Example: Multi-Agent Code Review Pipeline

A StateGraph with 3 nodes reviews a code diff:
  - Planner: breaks the review into checklist items
  - Reviewer: evaluates each item (loops), stores findings in MuBit
  - Summarizer: assembles findings into a final review

MuBit store persists findings across steps and sessions.

Requirements:
    pip install -r requirements.txt

Environment variables:
    OPENAI_API_KEY   - OpenAI API key (required)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key (default: empty for local dev)
"""

import json
import logging
import os
import sys
import uuid
from typing import Annotated, TypedDict

logger = logging.getLogger(__name__)

# Add the SDK and integrations to the path for local development
_REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for p in [os.path.join(_REPO, "sdk", "python", "mubit-sdk", "src"), os.path.join(_REPO, "integrations", "python")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.store.base import PutOp, SearchOp

from mubit_langgraph import MubitStore


# -- Sample code diff to review --

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


# -- State schema --

class ReviewState(TypedDict):
    code_diff: str
    checklist: list[str]
    current_idx: int
    findings: list[str]
    final_review: str


# -- Namespace for MuBit store --
NAMESPACE = ("memories", "code-reviewer", "review-session")

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
        },
    )])

    print(f"\n[Reviewer] Item {idx+1}/{len(state['checklist'])}: {item[:60]}...")
    print(f"  Finding: {finding[:100]}...")

    new_findings = list(state["findings"]) + [finding]
    return {"findings": new_findings, "current_idx": idx + 1}


def summarizer_node(state: ReviewState) -> dict:
    """Assemble all findings into a final review."""
    store = mubit_store

    # Get assembled context from MuBit
    context = store.get_context(
        NAMESPACE,
        query="code review findings security vulnerabilities",
        max_token_budget=4096,
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

    print(f"\n[Summarizer] Final review assembled.")

    return {"final_review": response.content}


def should_continue(state: ReviewState) -> str:
    """Route to reviewer if more items remain, otherwise to summarizer."""
    if state["current_idx"] < len(state["checklist"]):
        return "reviewer"
    return "summarizer"


def main():
    global llm

    endpoint = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
    api_key = os.environ.get("MUBIT_API_KEY") or os.environ.get("MUBIT_BOOTSTRAP_ADMIN_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not openai_key:
        print("Error: OPENAI_API_KEY environment variable is required.")
        sys.exit(1)

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

    global mubit_store

    # --- Set up MuBit store ---
    store = MubitStore(endpoint=endpoint, api_key=api_key)
    mubit_store = store

    # Register agents
    for agent_id, role in [
        ("planner", "review-planner"),
        ("reviewer", "item-reviewer"),
        ("summarizer", "review-summarizer"),
    ]:
        store.register_agent(NAMESPACE, agent_id=agent_id, role=role)

    # --- Build the graph ---
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

    # --- Run the review ---
    print(f"{'='*60}")
    print("  Running Code Review Pipeline")
    print(f"{'='*60}")

    result = compiled.invoke(
        {"code_diff": SAMPLE_DIFF.strip(), "checklist": [], "current_idx": 0, "findings": [], "final_review": ""},
    )

    # Record handoffs
    try:
        store.handoff(
            NAMESPACE,
            from_agent_id="planner",
            to_agent_id="reviewer",
            content=f"Created {len(result['checklist'])} review items.",
            requested_action="review",
        )
        store.handoff(
            NAMESPACE,
            from_agent_id="reviewer",
            to_agent_id="summarizer",
            content=f"Completed {len(result['findings'])} findings.",
            requested_action="execute",
        )
        print("Handoffs recorded.")
    except Exception as e:
        print(f"Handoff note: {e}")

    # --- Print final review ---
    print(f"\n{'='*60}")
    print("  Final Code Review")
    print(f"{'='*60}\n")
    print(result["final_review"])
    print()


if __name__ == "__main__":
    main()
