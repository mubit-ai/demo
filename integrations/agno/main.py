"""
MuBit + Agno Example: Research & Writing Team

A two-agent Agno team that processes research tasks:
  - Researcher: investigates topics and stores findings in MuBit memory
  - Writer: drafts content using recalled research from MuBit

Run 1 processes an initial topic; Run 2 processes a related topic and
demonstrates how the Writer agent discovers research patterns from
Run 1 via MuBit memory.

Requirements:
    pip install -r requirements.txt

Environment variables:
    OPENAI_API_KEY   - OpenAI API key (required)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key (default: empty for local dev)
"""

import os
import time
import uuid

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.memory.v2.memory import Memory
from mubit_agno import MubitAgnoMemory, MubitToolkit


TOPIC_1 = """
Research the key benefits and challenges of using Retrieval-Augmented
Generation (RAG) in production systems. Focus on:
1. Performance characteristics
2. Common failure modes
3. Best practices for chunk sizing
"""

TOPIC_2 = """
Write a technical blog post section about building reliable RAG pipelines.
Use any prior research findings we have on RAG systems.
"""


def create_team(mubit: MubitAgnoMemory, session_id: str):
    """Create a research + writing team backed by MuBit."""

    model = OpenAIChat(id="gpt-4o")

    researcher = Agent(
        name="Researcher",
        role="Research specialist",
        model=model,
        memory=Memory(db=mubit.as_memory_db()),
        tools=[mubit.as_toolkit()],
        instructions=[
            "You are a thorough research agent.",
            "Use mubit_remember to store key findings as facts.",
            "Use mubit_recall to check for existing research before starting.",
            "Always cite sources and note confidence levels.",
        ],
    )

    writer = Agent(
        name="Writer",
        role="Technical writer",
        model=model,
        memory=Memory(db=mubit.as_memory_db()),
        tools=[mubit.as_toolkit()],
        instructions=[
            "You are a skilled technical writer.",
            "Use mubit_recall to find relevant research before writing.",
            "Use mubit_get_context to get assembled background context.",
            "Write clear, well-structured content based on evidence.",
        ],
    )

    return researcher, writer


def run_demo():
    endpoint = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
    api_key = os.environ.get("MUBIT_API_KEY", "")

    # ---------- Run 1: Research Phase ----------
    session_1 = f"agno-demo-{uuid.uuid4().hex[:8]}"
    print(f"=== Run 1: Research Phase (session: {session_1}) ===\n")

    mubit_1 = MubitAgnoMemory(
        endpoint=endpoint,
        api_key=api_key,
        session_id=session_1,
        user_id="demo-user",
    )

    # Register agents for MAS coordination
    mubit_1.register_agent(
        "researcher",
        role="researcher",
        read_scopes=["fact", "lesson", "trace"],
        write_scopes=["fact", "trace", "lesson"],
        shared_memory_lanes=["research"],
    )
    mubit_1.register_agent(
        "writer",
        role="writer",
        read_scopes=["fact", "lesson", "trace"],
        write_scopes=["trace"],
        shared_memory_lanes=["research"],
    )

    researcher, writer = create_team(mubit_1, session_1)

    # Researcher investigates
    print("Researcher is investigating...")
    response = researcher.run(TOPIC_1, session_id=session_1)
    print(f"Researcher:\n{response.content}\n")

    # Checkpoint after research
    mubit_1.checkpoint("Research complete", f"Finished RAG research: {TOPIC_1[:50]}...")
    mubit_1.record_outcome("research-rag", "success", rationale="Research completed with findings stored")

    # Handoff to writer
    mubit_1.handoff(
        "researcher", "writer",
        "Research on RAG systems is complete. Key findings stored in memory.",
        requested_action="Draft a blog post section using the research.",
    )

    time.sleep(1)  # Allow async ingestion to complete

    # ---------- Run 2: Writing Phase ----------
    session_2 = f"agno-demo-{uuid.uuid4().hex[:8]}"
    print(f"\n=== Run 2: Writing Phase (session: {session_2}) ===\n")

    mubit_2 = MubitAgnoMemory(
        endpoint=endpoint,
        api_key=api_key,
        session_id=session_2,
        user_id="demo-user",
    )

    _, writer = create_team(mubit_2, session_2)

    # Writer recalls research and drafts content
    print("Writer is drafting...")
    response = writer.run(TOPIC_2, session_id=session_2)
    print(f"Writer:\n{response.content}\n")

    mubit_2.record_outcome("writing-rag-blog", "success", rationale="Blog section drafted from memory")

    # Surface strategies from both runs
    strategies = mubit_2.surface_strategies(max_strategies=3)
    print(f"\nDiscovered strategies: {strategies}")

    # Reflect on lessons learned
    lessons = mubit_2.reflect()
    print(f"Extracted lessons: {lessons}")

    # Check memory health
    health = mubit_2.memory_health()
    print(f"\nMemory health: {health}")


if __name__ == "__main__":
    run_demo()
