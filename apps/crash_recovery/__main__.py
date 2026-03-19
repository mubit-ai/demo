"""Entry point: python -m crash_recovery

Demonstrates crash recovery and mid-run resumption via Mubit memory.
Run 1 crashes after Phase 3 → Run 2 detects, restores, and resumes from Phase 4.
"""

import sys
import time
import uuid

from . import config
from .memory import Memory
from .pipeline import DueDiligencePipeline
from .recovery import RecoveryManager
from .state import get_phases


TARGET_COMPANY = (
    "DataPulse Analytics — a B2B SaaS company providing real-time customer "
    "analytics and behavioral segmentation for mid-market e-commerce companies. "
    "Founded 2021, ~60 employees, $4.5M ARR, 180 enterprise customers. "
    "Tech stack: React/TypeScript frontend, Python/FastAPI backend, PostgreSQL "
    "and ClickHouse for data, deployed on AWS EKS. Asking valuation: $35M."
)

PHASES = get_phases()


def _check_env():
    if not config.GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable is required.")
        sys.exit(1)
    if not config.MUBIT_API_KEY:
        print("Warning: MUBIT_API_KEY not set — memory calls may fail.")


def _safe(fn, label=""):
    try:
        return fn()
    except Exception as e:
        if label:
            msg = str(e)
            # Truncate verbose connection errors for clean output
            if "Max retries exceeded" in msg or "Connection refused" in msg:
                msg = "Mubit API temporarily unavailable"
            elif len(msg) > 120:
                msg = msg[:117] + "..."
            print(f"  {label}: {msg}")
        return None


def main():
    _check_env()

    crash_session = f"recovery-crash-{uuid.uuid4().hex[:8]}"
    resume_session = f"recovery-resume-{uuid.uuid4().hex[:8]}"
    crash_after = config.CRASH_AFTER_PHASE

    print(f"{'='*70}")
    print(f"  Technology Due Diligence — Crash Recovery Demo")
    print(f"  Endpoint:     {config.MUBIT_ENDPOINT}")
    print(f"  Model:        {config.MODEL}")
    print(f"  Crash after:  Phase {crash_after + 1} ({PHASES[crash_after].display_name})")
    print(f"{'='*70}\n")

    # ================================================================
    # RUN 1: Execute until crash
    # ================================================================
    print(f"{'='*70}")
    print(f"  RUN 1: Due Diligence (will crash after Phase {crash_after + 1})")
    print(f"  Session: {crash_session}")
    print(f"  Target:  {TARGET_COMPANY[:80]}...")
    print(f"{'='*70}")

    memory1 = Memory(
        endpoint=config.MUBIT_ENDPOINT,
        api_key=config.MUBIT_API_KEY,
        session_id=crash_session,
    )
    pipeline1 = DueDiligencePipeline(memory1, crash_after_phase=crash_after)

    # Register agents
    try:
        memory1.register_agents(pipeline1.all_agents())
        agents = memory1.list_agents()
        print(f"  Agents registered: {[a.get('agent_id') for a in agents]}")
    except Exception as e:
        print(f"  register_agents(): {e}")

    # Execute (will crash)
    try:
        pipeline1.run(TARGET_COMPANY)
    except RuntimeError as e:
        print(f"\n  {'!'*60}")
        print(f"  PIPELINE CRASHED: {e}")
        print(f"  {'!'*60}")

    # Post-crash: memory health
    print(f"\n{'='*70}")
    print(f"  POST-CRASH: Memory Health (Run 1)")
    print(f"{'='*70}\n")
    _safe(lambda: memory1.print_health(), "memory_health")

    # ================================================================
    # BETWEEN RUNS: Wait for ingestion
    # ================================================================
    print(f"\n  Waiting 12 seconds for Mubit ingestion + embedding...")
    time.sleep(12)

    # ================================================================
    # RECOVERY: Detect crash and build resume plan
    # ================================================================
    print(f"\n{'='*70}")
    print(f"  RECOVERY: Detecting crash and building resume plan")
    print(f"  Inspecting session: {crash_session}")
    print(f"{'='*70}\n")

    memory2 = Memory(
        endpoint=config.MUBIT_ENDPOINT,
        api_key=config.MUBIT_API_KEY,
        session_id=resume_session,
    )
    recovery = RecoveryManager(memory2)

    # Detect crash
    print("  === Crash Detection (9 Mubit APIs) ===\n")
    crash_report = recovery.detect_crash(crash_session)

    print(f"\n  {'─'*60}")
    print(f"  Crash Detection Summary:")
    print(f"    Crashed:          {crash_report.crashed}")
    print(f"    Crash phase:      {crash_report.crash_phase}")
    print(f"    Crash error:      {crash_report.crash_error[:80]}...")
    print(f"    Completed phases: {crash_report.completed_phases}")
    print(f"    Completed goals:  {len(crash_report.completed_goals)}")
    print(f"    Pending goals:    {len(crash_report.pending_goals)}")
    print(f"    Activities:       {crash_report.activity_count}")
    print(f"  {'─'*60}")

    # Build resume plan
    print(f"\n  === Building Resume Plan ===\n")
    resume_plan = recovery.build_resume_plan(
        crash_report, crash_session, TARGET_COMPANY,
    )
    print(f"\n  Resume Plan:")
    print(f"    Resume from:   Phase {resume_plan.resume_from_phase + 1} "
          f"({PHASES[resume_plan.resume_from_phase].display_name})")
    print(f"    Skip phases:   {resume_plan.skip_phases}")
    print(f"    Recovered:     {list(resume_plan.prior_outputs.keys())}")

    # Link runs and restore context
    print(f"\n  === Linking Runs & Restoring Context ===\n")
    linked_context = _safe(
        lambda: recovery.restore_context(crash_session, resume_session),
        "restore_context",
    )
    if linked_context:
        print(f"  Restored context: {len(linked_context)} chars")
        for line in linked_context.split("\n")[:5]:
            print(f"    {line[:100]}")
        if linked_context.count("\n") > 5:
            print(f"    ... ({linked_context.count(chr(10)) - 5} more lines)")

    # ================================================================
    # RUN 2: Resume from crash point
    # ================================================================
    print(f"\n{'='*70}")
    print(f"  RUN 2: Resuming Due Diligence from Phase "
          f"{resume_plan.resume_from_phase + 1} "
          f"({PHASES[resume_plan.resume_from_phase].display_name})")
    print(f"  Session: {resume_session}")
    print(f"{'='*70}")

    pipeline2 = DueDiligencePipeline(memory2, crash_after_phase=None)

    # Register agents for new run
    try:
        memory2.register_agents(pipeline2.all_agents())
    except Exception as e:
        print(f"  register_agents(): {e}")

    # Execute from resume point
    result = pipeline2.run(
        TARGET_COMPANY,
        start_from_phase=resume_plan.resume_from_phase,
        prior_outputs=resume_plan.prior_outputs,
    )

    print(f"\n{'='*70}")
    print(f"  EXECUTIVE DUE DILIGENCE REPORT")
    print(f"{'='*70}\n")
    print(result)

    # ================================================================
    # POST-RUN 2: Analysis
    # ================================================================
    print(f"\n  Waiting 8 seconds for indexing...")
    time.sleep(8)

    print(f"\n{'='*70}")
    print(f"  POST-RUN: Reflection & Analysis")
    print(f"{'='*70}\n")

    # Reflect (with step outcomes)
    print("  reflect() — extracting lessons from resumed run...")
    reflection = _safe(lambda: memory2.reflect(include_step_outcomes=True), "reflect")
    if reflection:
        memory2.print_reflection(reflection)

    # Surface strategies
    print("\n  surface_strategies() — clustering patterns...")
    strategies = _safe(lambda: memory2.strategies(), "surface_strategies")
    if strategies:
        memory2.print_strategies(strategies)

    # Memory health comparison
    print(f"\n  memory_health() — Run 2 (resumed):")
    _safe(lambda: memory2.print_health(), "memory_health")

    # Record final success
    _safe(lambda: memory2.record_success(
        "Due diligence completed successfully after crash recovery. "
        "Pipeline resumed from Phase 4 using Mubit state restoration. "
        "All 6 phases completed across 2 runs."
    ), "record_success")

    # Archive the final report
    ref = _safe(lambda: memory2.archive_report(result), "archive")
    if ref:
        print(f"\n  archive() — reference: {ref}")
        exact = _safe(lambda: memory2.verify_archive(ref))
        if exact and exact.get("found"):
            print(f"  dereference() — verified ({len(exact.get('content', ''))} chars)")

    # Export full activity timeline
    print("\n  export_activity() — full timeline:")
    export = _safe(lambda: memory2.export_activity(), "export_activity")
    if export:
        entries = export.get("entries", export.get("jsonl", []))
        count = len(entries) if isinstance(entries, list) else 0
        print(f"    Exported {count} activity entries")

    # ================================================================
    # SUMMARY
    # ================================================================
    print(f"\n{'='*70}")
    print(f"  MUBIT APIs USED (31 unique)")
    print(f"{'='*70}")
    print("""
  From discovery app (15):
    remember()             — stored phase outputs as facts/lessons
    recall()               — recovered completed phase outputs on resume
    get_context()          — injected prior findings into agent prompts
    checkpoint()           — snapshot after each phase + crash state
    archive()              — archived executive report
    dereference()          — verified archived report
    register_agent()       — registered 6 specialist agents
    list_agents()          — listed agent roster
    handoff()              — inter-phase agent handoffs
    feedback()             — verdict on handoff quality
    record_outcome()       — final success reinforcement signal
    reflect()              — extracted lessons from run
    surface_strategies()   — clustered patterns
    memory_health()        — pre/post crash health comparison
    control.lessons()      — listed lessons for outcome recording

  NEW APIs (16):
    control.set_variable()         — tracked phase progress and crash state
    control.get_variable()         — read state variables for recovery
    control.list_variables()       — enumerated all state during crash detection
    control.add_goal()             — created hierarchical goal tree (root + 6 phases)
    control.update_goal()          — marked goals achieved/failed
    control.list_goals()           — checked completion status during recovery
    control.get_goal_tree()        — visualized goal hierarchy
    control.record_step_outcome()  — per-phase process rewards (success/failure)
    control.agent_heartbeat()      — agent liveness before/after each phase
    control.link_run()             — linked crash run to resume run
    control.append_activity()      — structured activity records per phase
    control.list_activity()        — browsed crash timeline during recovery
    control.export_activity()      — exported full activity timeline
    control.submit_action()        — logged pipeline decisions
    control.get_action_log()       — reviewed action history during recovery
    control.context_snapshot()     — full run state snapshot for recovery
    control.run_cycle()            — decision cycle before each phase
    control.get_cycle_history()    — reviewed cycle history during recovery
    control.define_concept()       — defined phase_output and crash_report schemas
    control.list_concepts()        — listed defined concepts during recovery
    diagnose()                     — analyzed crash error against failure patterns
    """)
    print(f"{'='*70}")
    print(f"  Sessions: crash={crash_session}  resume={resume_session}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
