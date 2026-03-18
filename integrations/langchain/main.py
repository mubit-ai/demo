"""
MuBit + LangChain Example: Conversational Research Assistant

A multi-turn conversational assistant that uses MuBit memory to persist
context across sessions. Session 2 recalls facts learned in Session 1,
demonstrating cross-session semantic memory.

Requirements:
    pip install -r requirements.txt

Environment variables:
    OPENAI_API_KEY   - OpenAI API key (required)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key (default: empty for local dev)
"""

import os
import sys

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# Add the SDK and integrations to the path for local development
_REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for p in [os.path.join(_REPO, "sdk", "python", "mubit-sdk", "src"), os.path.join(_REPO, "integrations", "python")]:
    if p not in sys.path:
        sys.path.insert(0, p)

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
                "You are a knowledgeable research assistant. Answer questions "
                "thoroughly but concisely. When you have relevant context from "
                "previous conversations, reference it naturally."
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
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not openai_key:
        print("Error: OPENAI_API_KEY environment variable is required.")
        sys.exit(1)

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)

    # --- Session 1: Learn about the 2008 financial crisis ---
    memory_s1 = MubitChatMemory(
        endpoint=endpoint,
        api_key=api_key,
        session_id="research-financial-crisis-s1",
        agent_id="research-assistant",
        memory_key="history",
    )

    session1_questions = [
        "What were the main causes of the 2008 financial crisis?",
        "How did subprime mortgages contribute specifically?",
        "What regulatory changes were made afterward, such as Dodd-Frank?",
    ]

    run_session(llm, memory_s1, session1_questions, "Session 1: Learning about the 2008 Financial Crisis")

    # --- Session 2: New session, tests cross-session memory ---
    memory_s2 = MubitChatMemory(
        endpoint=endpoint,
        api_key=api_key,
        session_id="research-financial-crisis-s2",
        agent_id="research-assistant",
        memory_key="history",
    )

    session2_questions = [
        "What do we know about financial crisis prevention measures?",
        "How effective has Dodd-Frank been at preventing another crisis?",
    ]

    run_session(llm, memory_s2, session2_questions, "Session 2: Cross-Session Memory (new session, same MuBit instance)")

    print(f"\n{'='*60}")
    print("  Done! Session 2 responses should reference facts from Session 1.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
