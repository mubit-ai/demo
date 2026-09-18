"""Crash detection and resume plan builder."""

import json
from dataclasses import dataclass, field

from .memory import Memory
from .state import get_phases


@dataclass
class CrashReport:
    crashed: bool = False
    crash_error: str = ""
    crash_phase: str = ""
    completed_phases: list[str] = field(default_factory=list)
    pending_goals: list = field(default_factory=list)
    completed_goals: list = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    activity_count: int = 0
    diagnosis: dict = field(default_factory=dict)


@dataclass
class ResumePlan:
    resume_from_phase: int = 0
    skip_phases: list[str] = field(default_factory=list)
    prior_outputs: dict[str, str] = field(default_factory=dict)
    target_company: str = ""


def _safe(fn, label=""):
    try:
        return fn()
    except Exception as e:
        if label:
            msg = str(e)
            if "Max retries exceeded" in msg or "Connection refused" in msg:
                msg = "Mubit API temporarily unavailable"
            elif len(msg) > 120:
                msg = msg[:117] + "..."
            print(f"    {label}: {msg}")
        return None


class RecoveryManager:
    """Detects crashes and builds resume plans using Mubit APIs."""

    def __init__(self, memory: Memory):
        self.memory = memory

    def detect_crash(self, crash_session_id: str) -> CrashReport:
        """9-step crash detection using Mubit control plane APIs."""
        report = CrashReport()

        # Temporarily switch to crash session for inspection
        original_session = self.memory.session_id
        self.memory.set_session(crash_session_id)

        try:
            # 1. list_variables() — find crash indicators
            print("  1. list_variables() — checking state variables...")
            variables = _safe(lambda: self.memory.list_variables(),
                              "list_variables") or {}
            if variables:
                self.memory.print_variables(variables)
                report.crash_error = variables.get("crash_error", "")
                report.crash_phase = variables.get("crash_phase", "")
                status = variables.get("pipeline_status", "")
                report.crashed = status == "crashed"
                completed = variables.get("completed_phases", [])
                if isinstance(completed, str):
                    try:
                        completed = json.loads(completed)
                    except (json.JSONDecodeError, TypeError):
                        completed = []
                report.completed_phases = completed

            # 2. list_goals() — completion status
            print("\n  2. list_goals() — checking goal status...")
            all_goals = _safe(lambda: self.memory.list_goals(), "list_goals") or []
            if all_goals:
                for g in all_goals:
                    status = g.get("status", "").lower()
                    if status in ("achieved", "completed"):
                        report.completed_goals.append(g)
                    else:
                        report.pending_goals.append(g)
                self.memory.print_goals(all_goals)
                print(f"    Achieved: {len(report.completed_goals)}, "
                      f"Pending/Failed: {len(report.pending_goals)}")

            # 3. get_run_snapshot() — full state
            print("\n  3. get_run_snapshot() — fetching run snapshot...")
            report.snapshot = _safe(lambda: self.memory.get_run_snapshot(
                timeline_limit=30,
            ), "get_run_snapshot") or {}
            if report.snapshot:
                print(f"    Snapshot keys: {list(report.snapshot.keys())}")

            # 4. list_activity() — browse timeline
            print("\n  4. list_activity() — browsing crash timeline...")
            activities = _safe(lambda: self.memory.list_activity(limit=20),
                               "list_activity") or []
            report.activity_count = len(activities)
            for a in activities[:5]:
                # Handle various API response field names
                agent = (a.get("agent_id") or a.get("agent")
                         or a.get("source") or "?")
                atype = (a.get("entry_type") or a.get("type")
                         or a.get("activity_type") or a.get("intent") or "?")
                content = (a.get("content") or a.get("payload")
                           or a.get("context_snapshot") or "")
                preview = content[:60].replace("\n", " ") if content else ""
                print(f"    [{agent}] {atype}: {preview}")
            if len(activities) > 5:
                print(f"    ... ({len(activities) - 5} more entries)")

            # 5. diagnose() — analyze crash error
            if report.crash_error:
                print(f"\n  5. diagnose() — analyzing: {report.crash_error[:60]}...")
                report.diagnosis = _safe(
                    lambda: self.memory.diagnose_error(
                        report.crash_error, error_type="runtime_crash",
                    ), "diagnose"
                ) or {}
                if report.diagnosis:
                    evidence = report.diagnosis.get("evidence",
                                                    report.diagnosis.get("matches", []))
                    print(f"    Found {len(evidence)} related patterns")
                    for m in evidence[:3]:
                        content = m.get("content", "")[:100]
                        print(f"    Match: {content}...")
            else:
                print("\n  5. diagnose() — no crash error to diagnose")

            # 6. get_action_log() — review pipeline decisions
            print("\n  6. get_action_log() — reviewing decisions...")
            actions = _safe(lambda: self.memory.get_action_log(limit=10),
                            "get_action_log") or []
            for a in actions[:5]:
                atype = a.get("action_type", "?")
                agent = a.get("agent_id", "?")
                print(f"    [{agent}] {atype}")

            # 7. get_goal_tree() — hierarchical goal view
            print("\n  7. get_goal_tree() — goal hierarchy...")
            if all_goals:
                root_id = None
                for g in all_goals:
                    if not g.get("parent_goal_id"):
                        root_id = g.get("goal_id", g.get("id"))
                        break
                if root_id:
                    tree = _safe(lambda: self.memory.get_goal_tree(
                        root_goal_id=root_id,
                    ), "get_goal_tree") or {}
                    if tree:
                        self.memory.print_goal_tree(tree)

            # 8. get_cycle_history() — decision cycle history
            print("\n  8. get_cycle_history() — decision cycles...")
            cycles = _safe(lambda: self.memory.get_cycle_history(limit=5),
                           "get_cycle_history") or []
            for c in cycles[:3]:
                print(f"    Cycle: {json.dumps(c, default=str)[:100]}")
            if not cycles:
                print("    (no cycles recorded)")

            # 9. list_concepts() — defined schemas
            print("\n  9. list_concepts() — defined schemas...")
            concepts = _safe(lambda: self.memory.list_concepts(),
                             "list_concepts") or []
            for c in concepts:
                print(f"    {c.get('name', '?')}")
            if not concepts:
                print("    (no concepts defined)")

        finally:
            # Restore original session
            self.memory.set_session(original_session)

        return report

    def build_resume_plan(self, crash_report: CrashReport,
                          crash_session_id: str,
                          target_company: str) -> ResumePlan:
        """Determine which phases to skip and which to run."""
        phases = get_phases()
        completed = set(crash_report.completed_phases)
        skip_phases = list(completed)

        # Find first incomplete phase
        resume_from = 0
        for i, phase in enumerate(phases):
            if phase.key not in completed:
                resume_from = i
                break

        # Recover prior outputs via get_context() per phase (higher fidelity than recall)
        prior_outputs = {}
        original_session = self.memory.session_id
        self.memory.set_session(crash_session_id)
        try:
            for phase_key in completed:
                phase = next((p for p in phases if p.key == phase_key), None)
                if not phase:
                    continue
                print(f"  Recovering output: {phase.display_name}...")
                # Use get_context with high token budget for fuller recovery
                result = _safe(
                    lambda p=phase: self.memory.get_context(
                        f"{p.display_name} analysis findings",
                        max_tokens=2000,
                    ), f"get_context({phase_key})"
                )
                if not result:
                    # Fallback to recall
                    result = _safe(
                        lambda p=phase: self.memory.recall_prior(
                            f"{p.display_name} findings analysis output",
                            entry_types=["fact"],
                            limit=3,
                        ), f"recall({phase_key})"
                    )
                if result:
                    prior_outputs[phase_key] = result
                    print(f"    Recovered {len(result)} chars")
                else:
                    print(f"    No output found")
        finally:
            self.memory.set_session(original_session)

        return ResumePlan(
            resume_from_phase=resume_from,
            skip_phases=skip_phases,
            prior_outputs=prior_outputs,
            target_company=target_company,
        )

    def restore_context(self, crash_session_id: str,
                        resume_session_id: str) -> str:
        """Link runs and get cross-run context."""
        # link_run() to correlate crash and resume runs
        self.memory.link_run(crash_session_id)
        print(f"  Linked {resume_session_id} -> {crash_session_id}")

        # Get context with linked runs
        context = self.memory.recall_prior(
            "due diligence findings analysis market technical competitive",
            entry_types=["fact", "lesson", "step_outcome"],
            limit=10,
            include_linked_runs=True,
        )
        return context
