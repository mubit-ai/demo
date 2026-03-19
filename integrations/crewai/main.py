"""
MuBit + CrewAI Example: Support Ticket Triage Crew

A three-agent crew that processes customer support tickets:
  - Classifier: categorizes severity and type
  - Researcher: queries MuBit memory for similar past tickets
  - Responder: drafts a customer-facing reply

Run 1 processes an initial ticket; Run 2 processes a second ticket and
demonstrates how the Researcher agent discovers resolution patterns from
Run 1 via MuBit memory.

Requirements:
    pip install -r requirements.txt

Environment variables:
    GEMINI_API_KEY   - Google Gemini API key (required)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key (default: empty for local dev)
"""

import os
import time
import uuid

from crewai import Agent, Task, Crew, Process, LLM
from mubit_crewai import MubitCrewMemory


SAMPLE_TICKET_1 = """
Subject: Duplicate charge on Pro subscription - URGENT

I've been charged twice for my Pro subscription this month. Order #38291.
My credit card shows two identical charges of $49.99 on March 10th.
I need a refund for the duplicate charge immediately.

This is the third time this has happened in the past 6 months. Last time
(ticket #29104) I was told it was a payment gateway issue and it took
2 weeks to get my refund. This is unacceptable for a paying customer.

I'm considering canceling my subscription if this keeps happening.

- Sarah M., Pro Plan, Account #A-8842
"""

SAMPLE_TICKET_2 = """
Subject: Charged twice for Team Plan upgrade

I upgraded from Basic to Team Plan yesterday. My account shows the Team
Plan is active but I was charged $99.99 twice (order #41058 and #41059).
I only authorized one payment.

Can you confirm which charge is the duplicate and process a refund?

- James K., Team Plan, Account #A-11204
"""


LLM_MODEL = LLM(
    model="gemini/gemini-2.0-flash",
    api_key=os.environ.get("GEMINI_API_KEY"),
)


def _make_agents():
    """Create the three triage agents."""
    classifier = Agent(
        role="Support Ticket Classifier",
        goal="Accurately classify support tickets by severity and category",
        backstory=(
            "You are an experienced support team lead who has processed thousands "
            "of tickets. You can quickly identify the severity (critical, high, "
            "medium, low) and category (billing, technical, account, feature-request) "
            "of any support ticket. You also flag escalation triggers like repeated "
            "issues or cancellation threats."
        ),
        verbose=True,
        llm=LLM_MODEL,
    )

    researcher = Agent(
        role="Solution Researcher",
        goal="Find relevant past solutions and knowledge to resolve the ticket",
        backstory=(
            "You are a support knowledge specialist who maintains the team's "
            "solution database. You search for similar past tickets, known issues, "
            "and resolution patterns. You provide the response drafter with "
            "actionable context and recommended approaches based on what has "
            "worked before."
        ),
        verbose=True,
        llm=LLM_MODEL,
    )

    responder = Agent(
        role="Customer Response Drafter",
        goal="Draft empathetic, actionable customer replies that resolve issues",
        backstory=(
            "You are a senior customer success agent known for turning frustrated "
            "customers into advocates. You write clear, empathetic responses that "
            "acknowledge the issue, provide a concrete resolution path, and include "
            "a goodwill gesture when appropriate. You reference the classification "
            "and research findings to craft the best possible response."
        ),
        verbose=True,
        llm=LLM_MODEL,
    )

    return classifier, researcher, responder


def _make_tasks(classifier, researcher, responder):
    """Create the sequential triage tasks."""
    classify_task = Task(
        description=(
            "Classify the following support ticket:\n\n"
            "{ticket}\n\n"
            "Provide:\n"
            "1. Severity: critical / high / medium / low\n"
            "2. Category: billing / technical / account / feature-request\n"
            "3. Key issues identified (bullet points)\n"
            "4. Escalation flags (repeated issues, cancellation risk, etc.)\n"
            "5. Recommended priority for the response team"
        ),
        expected_output=(
            "A structured classification with severity, category, key issues, "
            "escalation flags, and priority recommendation."
        ),
        agent=classifier,
    )

    research_task = Task(
        description=(
            "Based on the ticket classification, research solutions:\n\n"
            "Ticket: {ticket}\n\n"
            "Search for:\n"
            "1. Similar past tickets and how they were resolved\n"
            "2. Known issues matching the symptoms described\n"
            "3. Standard procedures for this type of issue\n"
            "4. Any patterns that suggest a systemic problem\n\n"
            "Provide actionable research findings the responder can use."
        ),
        expected_output=(
            "Research findings including past similar cases, known solutions, "
            "standard procedures, and systemic pattern observations."
        ),
        agent=researcher,
    )

    respond_task = Task(
        description=(
            "Draft a customer response for this ticket:\n\n"
            "Ticket: {ticket}\n\n"
            "Use the classification and research findings to craft a response that:\n"
            "1. Acknowledges the customer's frustration empathetically\n"
            "2. Takes ownership of the problem\n"
            "3. Provides a concrete resolution timeline\n"
            "4. Addresses the repeated nature of the issue\n"
            "5. Includes a goodwill gesture if appropriate\n"
            "6. Ends with clear next steps"
        ),
        expected_output=(
            "A professional, empathetic customer response email that addresses "
            "all issues raised and provides a clear resolution path."
        ),
        agent=responder,
    )

    return classify_task, research_task, respond_task


def run_triage(ticket: str, ticket_label: str, memory: MubitCrewMemory):
    """Run the triage crew on a single ticket and record post-run memory ops."""

    classifier, researcher, responder = _make_agents()
    classify_task, research_task, respond_task = _make_tasks(
        classifier, researcher, responder,
    )

    crew = Crew(
        agents=[classifier, researcher, responder],
        tasks=[classify_task, research_task, respond_task],
        process=Process.sequential,
        memory=memory.as_crew_memory(),
        verbose=True,
    )

    print(f"\n{'='*60}")
    print(f"  {ticket_label}: Running Support Ticket Triage Crew")
    print(f"{'='*60}\n")

    result = crew.kickoff(inputs={"ticket": ticket.strip()})

    # --- Post-run MuBit operations ---
    print(f"\n{'='*60}")
    print(f"  {ticket_label}: Post-Run MuBit Memory Operations")
    print(f"{'='*60}\n")

    # Record handoffs
    memory.handoff(
        from_agent_id="classifier",
        to_agent_id="researcher",
        content="Classification complete. Ticket is high-severity billing issue with escalation risk.",
        requested_action="continue",
    )
    memory.handoff(
        from_agent_id="researcher",
        to_agent_id="responder",
        content="Research complete. Found similar past tickets and resolution patterns.",
        requested_action="execute",
    )
    print("Handoffs recorded.")

    # Checkpoint
    memory.checkpoint(
        snapshot=f"Triage complete for {ticket_label}. Final response drafted.",
        label=f"triage-complete-{ticket_label.lower().replace(' ', '-')}",
    )
    print("Checkpoint created.")

    # Record outcome
    try:
        memory.record_outcome(
            reference_id=f"{memory._session_id}-{ticket_label.lower().replace(' ', '-')}",
            outcome="success",
            rationale=f"{ticket_label}: Ticket classified, researched, and response drafted successfully.",
        )
        print("Outcome recorded.")
    except Exception as e:
        print(f"Outcome recording note: {e}")

    # Surface strategies from accumulated lessons
    try:
        strategies = memory.surface_strategies()
        print(f"Strategies surfaced: {strategies}")
    except Exception as e:
        print(f"Strategy surfacing note: {e}")

    # --- Print final result ---
    print(f"\n{'='*60}")
    print(f"  {ticket_label}: Final Customer Response")
    print(f"{'='*60}\n")
    print(result.raw if hasattr(result, "raw") else str(result))
    print()

    return result


def main():
    endpoint = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
    api_key = os.environ.get("MUBIT_API_KEY") or os.environ.get("MUBIT_BOOTSTRAP_ADMIN_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY")

    if not gemini_key:
        print("Error: GEMINI_API_KEY environment variable is required.")
        sys.exit(1)

    session_id = f"triage-{uuid.uuid4().hex[:8]}"
    print(f"Session ID: {session_id}")

    # --- Set up MuBit memory (shared across both runs) ---
    memory = MubitCrewMemory(
        endpoint=endpoint,
        api_key=api_key,
        session_id=session_id,
        agent_id="crewai-triage",
    )

    # Register agents with MuBit for MAS coordination
    for agent_def in [
        {"agent_id": "classifier", "role": "ticket-classifier"},
        {"agent_id": "researcher", "role": "solution-researcher"},
        {"agent_id": "responder", "role": "response-drafter"},
    ]:
        memory.register_agent(**agent_def)

    # ── Run 1 ────────────────────────────────────────────────
    run_triage(SAMPLE_TICKET_1, "Run 1", memory)

    # Wait for MuBit ingestion so Run 2 can discover Run 1 patterns
    print(f"\n{'='*60}")
    print("  Waiting 8 seconds for MuBit ingestion before Run 2 ...")
    print(f"{'='*60}\n")
    time.sleep(8)

    # ── Run 2 ────────────────────────────────────────────────
    run_triage(SAMPLE_TICKET_2, "Run 2", memory)


if __name__ == "__main__":
    main()
