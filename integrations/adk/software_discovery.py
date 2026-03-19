"""
Software Discovery Agent — Multi-Agent Web Search + Mubit Memory

A multi-agent system that researches and recommends software tools for a
business using live web search and persistent memory.

Pipeline: Coordinator → Parallel Researchers (with live web search) → Evaluator → Recommender

Each researcher uses Gemini's built-in Google Search grounding to find real
tools, pricing, and features. Mubit stores findings across runs — Run 2
recalls research from Run 1 to produce better recommendations.

Mubit integration via mubit-adk (MubitMemoryService) for:
  - ADK Runner memory hooks (automatic session ingestion + search)
  - register_agent, handoff, feedback, checkpoint, record_outcome
  - archive, dereference, surface_strategies, get_context, memory_health

Plus mubit-sdk (Client) for APIs not yet on the ADK adapter:
  - remember, recall, reflect, control.lessons

Environment variables:
    GOOGLE_API_KEY   - Google/Gemini API key (required)
    MUBIT_ENDPOINT   - MuBit server URL (default: http://127.0.0.1:3000)
    MUBIT_API_KEY    - MuBit API key
"""

import asyncio
import json
import os
import sys
import time

from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from google.genai import types as genai_types

import mubit
from mubit_adk import MubitMemoryService


APP_NAME = "software-discovery"
USER_ID = "demo-user"
MODEL = "gemini-2.0-flash"


# ── Agent Definitions ─────────────────────────────────────────────────────

coordinator = LlmAgent(
    name="coordinator",
    model=MODEL,
    description="Solutions architect that creates a research plan",
    instruction=(
        "You are a senior solutions architect at a software advisory firm. "
        "Given a business context, analyze the company's needs and create a "
        "concise research plan. Identify the 3 most critical software categories "
        "they need. For each category, specify what to look for: key features, "
        "budget constraints, and integration requirements.\n\n"
        "Output a clear, structured plan that downstream researchers can follow."
    ),
    output_key="research_plan",
)

payments_researcher = LlmAgent(
    name="payments_researcher",
    model=MODEL,
    description="Researches payment processing tools",
    instruction=(
        "You are a payments infrastructure specialist. Search the web for the "
        "top 3-5 payment processing tools suitable for the business described "
        "in the research plan. For each tool, find and report:\n"
        "- Product name and company\n"
        "- Pricing model (transaction fees, monthly costs)\n"
        "- Key features relevant to the business\n"
        "- Integration complexity (SDKs, APIs, time to integrate)\n"
        "- Notable customers or case studies\n\n"
        "Use real, current data from the web. Be specific with pricing numbers."
    ),
    tools=[google_search],
    output_key="research_payments",
)

billing_researcher = LlmAgent(
    name="billing_researcher",
    model=MODEL,
    description="Researches subscription billing and revenue tools",
    instruction=(
        "You are a billing and revenue operations specialist. Search the web "
        "for the top 3-5 subscription billing and revenue recognition tools "
        "suitable for the business described in the research plan. For each tool:\n"
        "- Product name and company\n"
        "- Pricing (per-subscription, flat fee, percentage-based)\n"
        "- Key features: recurring billing, usage-based billing, dunning, "
        "revenue recognition, tax handling\n"
        "- Integration with payment processors (especially Stripe, Adyen)\n"
        "- Notable customers\n\n"
        "Use real, current data from the web. Be specific."
    ),
    tools=[google_search],
    output_key="research_billing",
)

fraud_researcher = LlmAgent(
    name="fraud_researcher",
    model=MODEL,
    description="Researches fraud detection and prevention tools",
    instruction=(
        "You are a fraud prevention specialist. Search the web for the top "
        "3-5 fraud detection and prevention tools suitable for the business "
        "described in the research plan. For each tool:\n"
        "- Product name and company\n"
        "- Pricing model\n"
        "- Detection capabilities: payment fraud, account takeover, "
        "identity verification, chargeback prevention\n"
        "- Integration approach (API, SDK, built-in to payment processor)\n"
        "- False positive rates or accuracy metrics if available\n\n"
        "Use real, current data from the web. Be specific."
    ),
    tools=[google_search],
    output_key="research_fraud",
)

research_team = ParallelAgent(
    name="research_team",
    description="Runs all category researchers in parallel",
    sub_agents=[payments_researcher, billing_researcher, fraud_researcher],
)

evaluator = LlmAgent(
    name="evaluator",
    model=MODEL,
    description="Scores and ranks discovered tools against requirements",
    instruction=(
        "You are a technology evaluation analyst. Given the research findings "
        "from the parallel researchers, create a structured evaluation matrix.\n\n"
        "For each tool discovered, score it on a 1-10 scale across:\n"
        "- Pricing fit (does it match the company's budget/stage?)\n"
        "- Feature completeness (does it cover the company's needs?)\n"
        "- Integration ease (how hard is it to integrate?)\n"
        "- Ecosystem/community (documentation, support, community size)\n\n"
        "Present the results as a comparison table. If you have context from "
        "previous evaluations in your memory, reference those findings to "
        "provide more confident scoring.\n\n"
        "Identify the top pick and runner-up in each category."
    ),
    output_key="evaluation",
)

recommender = LlmAgent(
    name="recommender",
    model=MODEL,
    description="Synthesizes evaluation into a final stack recommendation",
    instruction=(
        "You are a chief technology advisor presenting a final recommendation. "
        "Based on the evaluation matrix, produce a complete tech stack "
        "recommendation report:\n\n"
        "1. **Recommended Stack** — one tool per category with reasoning\n"
        "2. **Alternative Options** — runner-up for each category\n"
        "3. **Integration Architecture** — how the tools connect\n"
        "4. **Estimated Costs** — monthly/annual costs at the company's scale\n"
        "5. **Implementation Roadmap** — suggested order of adoption\n\n"
        "If you have context from past recommendations for similar companies, "
        "reference those to strengthen your advice. Be decisive and opinionated."
    ),
    output_key="final_recommendation",
)

discovery_pipeline = SequentialAgent(
    name="discovery_pipeline",
    description="Full software discovery pipeline",
    sub_agents=[coordinator, research_team, evaluator, recommender],
)


# ── Runner ────────────────────────────────────────────────────────────────


async def run_discovery(runner, session_service, mm, sdk, query, label):
    """Run a single software discovery pipeline.

    Args:
        mm: MubitMemoryService (mubit-adk adapter)
        sdk: mubit.Client (SDK client for remember/recall/reflect)
    """
    session = await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID,
    )
    sid = f"discovery-{session.id}"
    sdk.set_run_id(sid)

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Session: {sid}")
    print(f"{'='*70}\n")
    print(f"  Query: {query}\n")

    # ── recall() [sdk] — check for prior research ────────────────────────
    print(f"  {'─'*60}")
    print(f"  recall() — searching for prior research")
    try:
        prior = sdk.recall(
            session_id=sid,
            query="B2B SaaS payments billing subscription tools",
            entry_types=["fact", "lesson"],
            limit=5,
        )
        evidence = prior.get("evidence", [])
        if evidence:
            print(f"  Found {len(evidence)} relevant entries from past runs:")
            for e in evidence[:3]:
                print(f"    [{e.get('entry_type')}] {e.get('content', '')[:100]}...")
        else:
            print(f"  No prior research found — starting fresh.")
    except Exception as ex:
        print(f"  recall(): {ex}")
    print()

    # ── Run the ADK pipeline ──────────────────────────────────────────────
    user_content = genai_types.Content(
        parts=[genai_types.Part(text=query)], role="user",
    )

    agent_outputs = {}
    agent_order = []
    final_text = ""
    async for event in runner.run_async(
        session_id=session.id, user_id=USER_ID, new_message=user_content,
    ):
        if hasattr(event, "author") and hasattr(event, "content"):
            author = getattr(event, "author", "unknown")
            text = ""
            if hasattr(event.content, "parts"):
                text = " ".join(
                    p.text for p in event.content.parts
                    if hasattr(p, "text") and p.text
                )
            if text:
                preview = text[:300].replace("\n", " ")
                print(f"  [{author}] {preview}{'...' if len(text) > 300 else ''}")
                agent_outputs[author] = text
                if author not in agent_order:
                    agent_order.append(author)
                final_text = text

    # ── Post-pipeline Mubit operations ────────────────────────────────────
    print(f"\n  {'─'*60}")
    print(f"  {label}: Mubit Memory Operations\n")

    # ── remember() [sdk] — store each agent's output ─────────────────────
    stored = 0
    for agent_name, output in agent_outputs.items():
        if "researcher" in agent_name:
            intent, importance = "fact", "high"
        elif agent_name == "evaluator":
            intent, importance = "lesson", "high"
        elif agent_name == "recommender":
            intent, importance = "lesson", "critical"
        else:
            intent, importance = "trace", "medium"
        try:
            sdk.remember(
                session_id=sid, agent_id=agent_name,
                content=output[:2000], intent=intent, importance=importance,
                metadata={"label": label, "agent": agent_name, "query": query[:100]},
            )
            stored += 1
        except Exception as e:
            print(f"    Note storing {agent_name}: {e}")
    print(f"  remember()  — stored {stored} agent outputs as structured memory")

    # ── archive() [mubit-adk] — store recommendation as exact-reference ──
    archive_ref = None
    if "recommender" in agent_outputs:
        try:
            archived = await mm.archive(
                app_name=APP_NAME, user_id=USER_ID, session_id=session.id,
                agent_id="recommender", origin_agent_id="recommender",
                artifact_kind="recommendation_report",
                content=agent_outputs["recommender"][:3000],
                source_attempt_id=sid, source_tool="discovery_pipeline",
                family="software-discovery",
                labels=["recommendation", "tech-stack"],
                metadata={"label": label, "query": query[:100]},
            )
            archive_ref = archived.get("reference_id")
            print(f"  archive()   — exact-reference: {archive_ref}")
        except Exception as e:
            print(f"  archive(): {e}")

    # ── handoff() [mubit-adk] — record agent-to-agent handoffs ───────────
    handoff_pairs = []
    for i in range(len(agent_order) - 1):
        f, t = agent_order[i], agent_order[i + 1]
        if "researcher" in f and "researcher" in t:
            continue
        handoff_pairs.append((f, t))

    for from_a, to_a in handoff_pairs:
        try:
            await mm.handoff(
                user_id=USER_ID, session_id=session.id,
                from_agent_id=from_a, to_agent_id=to_a,
                content=f"{from_a} completed, handing off to {to_a}",
                requested_action="execute" if to_a != "recommender" else "review",
            )
        except Exception:
            pass
    print(f"  handoff()   — recorded {len(handoff_pairs)} agent handoffs")

    # ── feedback() [mubit-adk] — recommender approves evaluator ──────────
    try:
        last_ho = await mm.handoff(
            user_id=USER_ID, session_id=session.id,
            from_agent_id="evaluator", to_agent_id="recommender",
            content="Evaluation matrix ready for final recommendation",
            requested_action="review",
        )
        await mm.feedback(
            user_id=USER_ID, session_id=session.id,
            handoff_id=last_ho.get("handoff_id"),
            verdict="approve",
            comments="Evaluation matrix is comprehensive. Proceeding with recommendation.",
        )
        print(f"  feedback()  — recommender approved evaluator's matrix")
    except Exception as e:
        print(f"  feedback(): {e}")

    # ── checkpoint() [mubit-adk] — snapshot pipeline state ────────────────
    try:
        cp = await mm.checkpoint(
            app_name=APP_NAME, user_id=USER_ID, session_id=session.id,
            snapshot=f"Pipeline complete. {stored} findings. Query: {query[:80]}",
            label=f"pipeline-complete-{label[:30]}",
        )
        print(f"  checkpoint() — {cp.get('checkpoint_id')}")
    except Exception as e:
        print(f"  checkpoint(): {e}")

    # ── record_outcome() [mubit-adk] — record success ────────────────────
    try:
        sdk.remember(
            session_id=sid, agent_id="recommender",
            content=f"Pipeline completed successfully for: {query[:100]}",
            intent="lesson", lesson_type="success",
            lesson_scope="global", lesson_importance="high",
        )
        lessons = sdk.control.lessons({"run_id": sid, "limit": 5})
        lesson_list = lessons.get("lessons", [])
        if lesson_list:
            await mm.record_outcome(
                app_name=APP_NAME, user_id=USER_ID, session_id=session.id,
                reference_id=lesson_list[0]["id"],
                outcome="success", signal=0.85,
                rationale=f"Full pipeline completed with {stored} findings for: {label}",
            )
            print(f"  record_outcome() — success recorded against lesson")
    except Exception as e:
        print(f"  record_outcome(): {e}")

    # ── dereference() [mubit-adk] — verify archive is retrievable ─────────
    if archive_ref:
        try:
            exact = await mm.dereference(
                user_id=USER_ID, session_id=session.id,
                reference_id=archive_ref, agent_id="recommender",
            )
            if exact.get("found"):
                print(f"  dereference() — verified ({len(exact.get('content', ''))} chars)")
            else:
                print(f"  dereference() — not yet indexed")
        except Exception as e:
            print(f"  dereference(): {e}")

    # Print final recommendation
    print(f"\n{'='*70}")
    print(f"  {label}: FINAL RECOMMENDATION")
    print(f"{'='*70}\n")
    print(final_text or "(see agent output above)")
    print()

    return session


async def main():
    endpoint = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
    api_key = os.environ.get("MUBIT_API_KEY") or os.environ.get("MUBIT_BOOTSTRAP_ADMIN_API_KEY", "")
    google_key = os.environ.get("GOOGLE_API_KEY")

    if not google_key:
        print("Error: GOOGLE_API_KEY environment variable is required.")
        sys.exit(1)

    # mubit-adk: plugs into ADK Runner + exposes MAS helpers
    mm = MubitMemoryService(endpoint=endpoint, api_key=api_key)

    # mubit-sdk: for remember/recall/reflect (not yet on ADK adapter)
    sdk = mubit.Client(endpoint=endpoint, api_key=api_key, run_id="discovery")
    sdk.set_transport("http")

    session_service = InMemorySessionService()
    runner = Runner(
        agent=discovery_pipeline,
        app_name=APP_NAME,
        session_service=session_service,
        memory_service=mm,
    )

    # ── register_agent() [mubit-adk] ─────────────────────────────────────
    reg_session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    for agent_id, role in [
        ("coordinator", "solutions-architect"),
        ("payments_researcher", "payments-research"),
        ("billing_researcher", "billing-research"),
        ("fraud_researcher", "fraud-research"),
        ("evaluator", "tool-evaluation"),
        ("recommender", "stack-recommendation"),
    ]:
        await mm.register_agent(
            user_id=USER_ID, session_id=reg_session.id,
            agent_id=agent_id, role=role,
        )
    print("Agents registered.\n")

    # ── Run 1 ─────────────────────────────────────────────────────────────
    s1 = await run_discovery(
        runner, session_service, mm, sdk,
        query=(
            "I'm a Series A B2B SaaS company with 50 employees. We process "
            "about $2M ARR through a mix of monthly and annual subscriptions. "
            "I need a complete payments and billing stack: payment processing, "
            "subscription billing, revenue recognition, and fraud detection. "
            "We currently use a basic Stripe integration but need to scale."
        ),
        label="Run 1: Series A SaaS — Full Payments Stack",
    )

    print(f"{'='*70}")
    print("  Waiting 12 seconds for Mubit ingestion + embedding...")
    print(f"{'='*70}\n")
    await asyncio.sleep(12)

    # ── reflect() [sdk] — extract lessons from Run 1 ─────────────────────
    sid1 = f"discovery-{s1.id}"
    print(f"{'='*70}")
    print("  MUBIT: REFLECT + ANALYZE RUN 1")
    print(f"{'='*70}\n")

    print("  reflect() — extracting lessons from Run 1 findings...")
    try:
        reflection = sdk.reflect(session_id=sid1)
        print(f"  Confidence: {reflection.get('confidence')}")
        print(f"  Lessons extracted: {reflection.get('lessons_stored', 0)}")
        for lesson in reflection.get("lessons", []):
            print(f"    [{lesson.get('lesson_type'):10s}] {lesson.get('content', '')[:120]}...")
        summary = reflection.get("summary", "")
        if summary:
            print(f"  Summary: {summary[:200]}...")
    except Exception as e:
        print(f"  reflect(): {e}")

    # ── surface_strategies() [mubit-adk] ──────────────────────────────────
    print()
    print("  surface_strategies() — clustering patterns from Run 1...")
    try:
        strategies = await mm.surface_strategies(session_id=s1.id)
        strat_list = strategies.get("strategies", [])
        print(f"  Found {len(strat_list)} strategies:")
        for s in strat_list[:3]:
            print(f"    [{s.get('supporting_lesson_count')} lessons] {s.get('description', '')[:120]}...")
    except Exception as e:
        print(f"  surface_strategies(): {e}")

    # ── memory_health() [mubit-adk] ───────────────────────────────────────
    print()
    print("  memory_health() — Run 1:")
    try:
        health = await mm.memory_health(user_id=USER_ID, session_id=s1.id)
        counts = health.get("entry_counts", {})
        print(f"  Entry counts: {json.dumps(counts, indent=4)}")
        print(f"  Total: {sum(counts.values()) if counts else 0}")
    except Exception as e:
        print(f"  memory_health(): {e}")

    # ── get_context() [mubit-adk] — preview what Run 2 would see ──────────
    print(f"\n  get_context() — what Run 2 would see:")
    try:
        context = await mm.get_context(
            app_name=APP_NAME, user_id=USER_ID,
            query="B2B SaaS payments billing subscription tools recommendation",
            session_id=s1.id,
        )
        block = context.get("context_block", "")
        if block:
            print(f"  ┌{'─'*66}")
            for line in block.split("\n")[:20]:
                print(f"  │ {line}")
            if block.count("\n") > 20:
                print(f"  │ ... ({block.count(chr(10)) - 20} more lines)")
            print(f"  └{'─'*66}")
            src_counts = context.get("source_counts_by_entry_type", {})
            print(f"  Sources by type: {json.dumps(src_counts)}")
        else:
            print("  (entries may still be indexing)")
    except Exception as e:
        print(f"  get_context(): {e}")

    print()

    # ── Run 2 ─────────────────────────────────────────────────────────────
    s2 = await run_discovery(
        runner, session_service, mm, sdk,
        query=(
            "I'm a seed-stage B2B SaaS with 10 employees. We're just starting "
            "to monetize our product — need payment processing and subscription "
            "billing. Budget is tight. We want something that can grow with us. "
            "What's the best stack to start with?"
        ),
        label="Run 2: Seed Stage SaaS — Budget Stack (cross-run memory)",
    )

    # ── Final analysis ────────────────────────────────────────────────────
    await asyncio.sleep(8)
    sid2 = f"discovery-{s2.id}"

    print(f"\n{'='*70}")
    print("  MUBIT: FINAL ANALYSIS AFTER BOTH RUNS")
    print(f"{'='*70}\n")

    print("  reflect() on Run 2...")
    try:
        r2 = sdk.reflect(session_id=sid2)
        print(f"  Lessons extracted: {r2.get('lessons_stored', 0)}")
        for lesson in r2.get("lessons", []):
            print(f"    [{lesson.get('lesson_type'):10s}] {lesson.get('content', '')[:120]}...")
    except Exception as e:
        print(f"  reflect(): {e}")

    print()
    print("  surface_strategies() — patterns across BOTH runs...")
    try:
        strats = await mm.surface_strategies(session_id=s2.id)
        sl = strats.get("strategies", [])
        print(f"  Found {len(sl)} strategies:")
        for s in sl[:3]:
            print(f"    [{s.get('supporting_lesson_count')} lessons] {s.get('description', '')[:120]}...")
    except Exception as e:
        print(f"  surface_strategies(): {e}")

    print()
    print("  memory_health() — Run 2:")
    try:
        h2 = await mm.memory_health(user_id=USER_ID, session_id=s2.id)
        counts2 = h2.get("entry_counts", {})
        print(f"  Entry counts: {json.dumps(counts2, indent=4)}")
        sh = h2.get("section_health", [])
        if sh:
            print(f"\n  Section health:")
            for s in sh:
                print(f"    {s.get('section_name', '?'):20s}  count={s.get('count', 0):3d}  confidence={s.get('avg_confidence', 0):.2f}")
    except Exception as e:
        print(f"  memory_health(): {e}")

    print(f"\n{'='*70}")
    print("  MUBIT APIS USED")
    print(f"{'='*70}")
    print("""
  Via mubit-adk (MubitMemoryService):
    Runner memory_service  — automatic session ingestion + search_memory
    register_agent()       — registered 6 agents with roles
    handoff()              — agent-to-agent handoffs
    feedback()             — recommender approved evaluator
    checkpoint()           — pipeline state snapshots
    record_outcome()       — success with reinforcement signal
    archive()              — exact-reference artifacts
    dereference()          — verified archive retrieval
    surface_strategies()   — clustered patterns across runs
    get_context()          — token-budgeted context assembly
    memory_health()        — entry counts + section confidence

  Via mubit-sdk (Client):
    remember()             — stored agent outputs as facts/lessons/traces
    recall()               — searched for prior research before each run
    reflect()              — extracted higher-order lessons from findings
    control.lessons()      — listed lessons for outcome recording
    """)
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
