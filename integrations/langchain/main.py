"""
MuBit + LangChain Example: Incident Response Assistant

An on-call engineer's assistant that uses MuBit memory to persist
context across sessions. Session 2 recalls resolutions from Session 1,
demonstrating cross-session semantic memory for incident response.

Requirements:
    pip install -r requirements.txt

Environment variables:
    GOOGLE_API_KEY   - Google / Gemini API key (required, falls back to GEMINI_API_KEY)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key (default: empty for local dev)
"""

import os
import time

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage



from mubit_langchain import MubitChatMemory


def run_session(llm, memory, questions, session_label):
    """Run a conversation session with the given questions."""
    print(f"\n{'='*60}")
    print(f"  {session_label}")
    print(f"{'='*60}")

    for question in questions:
        # Load relevant context from MuBit
        context = memory.load_memory_variables({"input": question})
        history = context.get("history", [])

        # Build message list
        messages = [
            SystemMessage(content=(
                "You are an experienced SRE / incident response assistant. "
                "Help on-call engineers diagnose and resolve production incidents. "
                "When you have relevant context from previous incidents, "
                "reference those resolutions to speed up diagnosis."
            )),
        ]
        if history:
            messages.extend(history)
        messages.append(HumanMessage(content=question))

        # Call the LLM
        response = llm.invoke(messages)

        # Save the interaction to MuBit
        memory.save_context({"input": question}, {"output": response.content})

        # Display
        print(f"\nQ: {question}")
        print(f"A: {response.content}")
        print("-" * 40)


def main():
    endpoint = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
    api_key = os.environ.get("MUBIT_API_KEY") or os.environ.get("MUBIT_BOOTSTRAP_ADMIN_API_KEY", "")
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    if not google_key:
        print("Error: GOOGLE_API_KEY environment variable is required (falls back to GEMINI_API_KEY).")
        sys.exit(1)

    os.environ["GOOGLE_API_KEY"] = google_key

    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0.3)

    # --- Session 1: Redis connection timeout incident ---
    memory_s1 = MubitChatMemory(
        endpoint=endpoint,
        api_key=api_key,
        session_id="incident-redis-timeout-s1",
        agent_id="incident-response-assistant",
        memory_key="history",
    )

    session1_questions = [
        (
            "We're seeing Redis connection timeouts spiking. P95 latency is at "
            "12 seconds and error rate is 15%. What should we check first?"
        ),
        (
            "We checked and the connection pool was at max capacity — only 128 "
            "connections configured but we have 200 worker threads. We increased "
            "maxclients to 512 and the issue resolved."
        ),
    ]

    run_session(llm, memory_s1, session1_questions, "Session 1: Redis Connection Timeout Incident")

    # Wait for MuBit to ingest the session before starting the next one
    print("\nWaiting 8 seconds for MuBit ingestion...")
    time.sleep(8)

    # --- Session 2: Similar incident, new session, tests cross-session memory ---
    memory_s2 = MubitChatMemory(
        endpoint=endpoint,
        api_key=api_key,
        session_id="incident-redis-pool-s2",
        agent_id="incident-response-assistant",
        memory_key="history",
    )

    session2_questions = [
        (
            "Redis clients are failing to acquire connections from the pool. "
            "Connection pool exhaustion errors in the logs. What's the likely cause?"
        ),
        (
            "Based on your suggestion we checked and pool size was indeed the issue. "
            "Increasing it from 64 to 256 fixed it."
        ),
    ]

    run_session(llm, memory_s2, session2_questions, "Session 2: Cross-Session Memory (new session, same MuBit instance)")

    print(f"\n{'='*60}")
    print("  Done! Session 2's first response should reference the")
    print("  connection pool resolution from Session 1.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
