"""Phase-based pipeline with checkpoint/resume/crash simulation."""

import json

from .agents import ReportWriter
from .memory import Memory
from .state import PipelineState, get_phases


def _safe(fn, label=""):
    """Run fn(), swallow exceptions so the pipeline keeps going."""
    try:
        return fn()
    except Exception as e:
        if label:
            msg = str(e)
            if "Max retries exceeded" in msg or "Connection refused" in msg:
                msg = "Mubit API temporarily unavailable"
            elif len(msg) > 120:
                msg = msg[:117] + "..."
            print(f"  {label}: {msg}")
        return None


class DueDiligencePipeline:
    """6-phase due diligence pipeline with full Mubit instrumentation."""

    def __init__(self, memory: Memory, crash_after_phase: int | None = None):
        self.memory = memory
        self.crash_after_phase = crash_after_phase
        self.state = PipelineState(memory)
        self.phases = get_phases()
        self._agents = {p.key: p.agent_cls() for p in self.phases}

    def all_agents(self) -> list:
        return list(self._agents.values())

    def run(self, target_company: str, start_from_phase: int = 0,
            prior_outputs: dict[str, str] | None = None) -> str:
        """Execute phases. If start_from_phase > 0, skip completed phases."""
        prior_outputs = prior_outputs or {}
        phase_outputs: dict[str, str] = dict(prior_outputs)
        goal_map: dict[str, str] = {}
        is_fresh = start_from_phase == 0

        # Initialize state (only if starting fresh)
        if is_fresh:
            print("\n  Initializing pipeline state...")
            _safe(lambda: self.state.initialize(target_company), "state.initialize")
            _safe(lambda: self.state.define_concepts(), "state.define_concepts")
            print("  Creating goal tree...")
            goal_map = _safe(lambda: self.state.create_goals(), "state.create_goals") or {}
            if goal_map:
                print(f"  Goals created: {list(goal_map.keys())}")
            _safe(lambda: self.memory.submit_action(
                "pipeline", "pipeline_start",
                {"target": target_company[:100], "total_phases": len(self.phases)},
            ), "submit_action")
        else:
            print(f"\n  Resuming from Phase {start_from_phase + 1}...")
            print(f"  Prior outputs available: {list(prior_outputs.keys())}")
            _safe(lambda: self.state.initialize(target_company), "state.initialize")
            goal_map = _safe(lambda: self.state.create_goals(), "state.create_goals") or {}
            # Mark skipped phases as complete in goals
            for i in range(start_from_phase):
                phase = self.phases[i]
                gid = goal_map.get(phase.key)
                if gid:
                    _safe(lambda g=gid: self.memory.update_goal(g, "achieved"))
                _safe(lambda p=phase: self.state.complete_phase(
                    p.key, None, f"Recovered from crash run"
                ))
            _safe(lambda: self.memory.submit_action(
                "pipeline", "pipeline_resume",
                {"resume_from": self.phases[start_from_phase].key,
                 "skipped_phases": [p.key for p in self.phases[:start_from_phase]]},
            ), "submit_action")

        # Execute phases
        for i, phase in enumerate(self.phases):
            if i < start_from_phase:
                continue

            # Check for crash simulation
            if self.crash_after_phase is not None and i > self.crash_after_phase:
                # Record crash state
                goal_id = goal_map.get(phase.key)
                self._handle_crash(phase, goal_id, target_company)
                raise RuntimeError(
                    f"Simulated crash: external API timeout during "
                    f"Phase {i + 1} ({phase.display_name}). "
                    f"Process terminated unexpectedly."
                )

            # Build prior findings from completed phases
            prior_findings = self._build_prior_findings(phase_outputs, i)
            goal_id = goal_map.get(phase.key)

            # Execute phase
            output = self._execute_phase(
                phase, i, target_company, prior_findings, goal_id,
            )
            phase_outputs[phase.key] = output

        return phase_outputs.get("executive_report", "(no report generated)")

    def _execute_phase(self, phase, phase_idx: int, target_company: str,
                       prior_findings: str, goal_id: str | None) -> str:
        """Execute a single phase with full Mubit instrumentation."""
        agent = self._agents[phase.key]
        phase_num = phase_idx + 1

        print(f"\n  {'─'*60}")
        print(f"  Phase {phase_num}/6: {phase.display_name} [{agent.name}]")
        print(f"  {'─'*60}")

        # 1. Agent heartbeat (active)
        _safe(lambda: self.memory.agent_heartbeat(agent.name, "active"),
              "agent_heartbeat")

        # 2. Begin phase state
        _safe(lambda: self.state.begin_phase(phase.key), "state.begin_phase")

        # 3. Decision cycle
        _safe(lambda: self.memory.run_cycle(agent.name), "run_cycle")

        # 4. Get context from memory
        mubit_context = _safe(
            lambda: self.memory.get_context(
                f"{phase.display_name} due diligence analysis"
            )
        ) or ""

        # 5. Agent LLM call
        print(f"  [{agent.name}] Running analysis...")
        if phase.key == "executive_report":
            output = agent.run(prior_findings, prior_context=mubit_context)
        else:
            output = agent.run(target_company, prior_findings=prior_findings,
                               prior_context=mubit_context)
        preview = output[:300].replace("\n", " ")
        print(f"  [{agent.name}] {preview}{'...' if len(output) > 300 else ''}")

        # 6. Store finding
        intent = "lesson" if phase.category == "synthesis" else "fact"
        importance = "critical" if phase.key == "executive_report" else "high"
        _safe(lambda: self.memory.store_finding(
            agent.name, output, intent=intent, importance=importance,
        ), "store_finding")

        # 7. Record step outcome
        _safe(lambda: self.memory.record_step_outcome(
            step_id=f"phase-{phase_num}-{phase.key}",
            step_name=phase.display_name,
            outcome="success",
            signal=0.85,
            rationale=f"Phase {phase_num} ({phase.display_name}) completed successfully",
            agent_id=agent.name,
        ), "record_step_outcome")

        # 8. Checkpoint
        _safe(lambda: self.memory.checkpoint(
            f"phase-{phase_num}-complete",
            f"Phase {phase_num} ({phase.display_name}) complete. "
            f"Output: {output[:200]}",
        ), "checkpoint")

        # 9. Append activity
        _safe(lambda: self.memory.append_activity(
            agent.name, "phase_complete",
            {"phase": phase.key, "phase_num": phase_num,
             "output_length": len(output)},
        ), "append_activity")

        # 10. Submit action
        _safe(lambda: self.memory.submit_action(
            agent.name, "phase_complete",
            {"phase": phase.key, "phase_num": phase_num},
        ), "submit_action")

        # 11. Complete phase state
        _safe(lambda: self.state.complete_phase(
            phase.key, goal_id, output[:200],
        ), "state.complete_phase")

        # 12. Handoff to next phase + feedback
        if phase_idx < len(self.phases) - 1:
            next_phase = self.phases[phase_idx + 1]
            next_agent = self._agents[next_phase.key]
            handoff_id = _safe(lambda: self.memory.store_handoff(
                agent.name, next_agent.name,
                f"Phase {phase_num} ({phase.display_name}) complete. "
                f"Handing off to Phase {phase_num + 1} ({next_phase.display_name}).",
            ), "handoff")
            if handoff_id:
                _safe(lambda: self.memory.store_feedback(
                    handoff_id, "approve",
                    f"{next_agent.name} accepts handoff from {agent.name}. "
                    f"Prior phase output is comprehensive.",
                ), "feedback")

        # 13. Agent heartbeat (idle)
        _safe(lambda: self.memory.agent_heartbeat(agent.name, "idle"),
              "agent_heartbeat")

        print(f"  Phase {phase_num} complete.")
        return output

    def _handle_crash(self, phase, goal_id: str | None, target_company: str):
        """Record crash state in Mubit before raising."""
        print(f"\n  !!! CRASH during Phase: {phase.display_name} !!!")

        error_msg = (
            f"Simulated crash: external API timeout during {phase.display_name}. "
            f"Process terminated unexpectedly."
        )

        # Record crash in state variables
        _safe(lambda: self.state.record_crash(phase.key, error_msg, goal_id),
              "state.record_crash")

        # Store failure lesson
        _safe(lambda: self.memory.store_finding(
            "pipeline", error_msg,
            intent="lesson", importance="critical",
        ), "store_finding(crash)")

        # Record step outcome (failure)
        phase_idx = next(i for i, p in enumerate(self.phases) if p.key == phase.key)
        _safe(lambda: self.memory.record_step_outcome(
            step_id=f"phase-{phase_idx + 1}-{phase.key}",
            step_name=phase.display_name,
            outcome="failure",
            signal=-0.8,
            rationale=error_msg,
            agent_id=self._agents[phase.key].name,
        ), "record_step_outcome(crash)")

        # Append crash activity
        _safe(lambda: self.memory.append_activity(
            "pipeline", "pipeline_crash",
            {"phase": phase.key, "error": error_msg},
        ), "append_activity(crash)")

        # Checkpoint crash state
        _safe(lambda: self.memory.checkpoint(
            "crash-state",
            f"Pipeline crashed during {phase.display_name}. Error: {error_msg}",
        ), "checkpoint(crash)")

    def _build_prior_findings(self, phase_outputs: dict[str, str],
                              current_idx: int) -> str:
        """Build accumulated findings string from completed phases."""
        parts = []
        for i, phase in enumerate(self.phases):
            if i >= current_idx:
                break
            output = phase_outputs.get(phase.key, "")
            if output:
                parts.append(f"=== {phase.display_name} ===\n{output}\n")
        return "\n".join(parts)
