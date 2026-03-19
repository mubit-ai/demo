"""Pipeline state machine — working memory variables + goals."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .memory import Memory


@dataclass
class Phase:
    key: str
    display_name: str
    agent_cls: type
    category: str  # "research", "analysis", "synthesis"


# Import agents here to avoid circular imports at module level
def _get_phases() -> list[Phase]:
    from .agents import (
        MarketAnalyst, TechAssessor, CompetitiveIntel,
        RiskAnalyst, FinancialModeler, ReportWriter,
    )
    return [
        Phase("market_analysis", "Market Analysis", MarketAnalyst, "research"),
        Phase("tech_assessment", "Technical Assessment", TechAssessor, "research"),
        Phase("competitive_landscape", "Competitive Landscape", CompetitiveIntel, "research"),
        Phase("risk_analysis", "Risk Analysis", RiskAnalyst, "analysis"),
        Phase("financial_modeling", "Financial Modeling", FinancialModeler, "analysis"),
        Phase("executive_report", "Executive Report", ReportWriter, "synthesis"),
    ]


# Module-level lazy accessor
_PHASES: list[Phase] | None = None


def get_phases() -> list[Phase]:
    global _PHASES
    if _PHASES is None:
        _PHASES = _get_phases()
    return _PHASES


PHASE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "phase_key": {"type": "string"},
        "agent_name": {"type": "string"},
        "output_summary": {"type": "string"},
        "tokens_used": {"type": "integer"},
    },
}

CRASH_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "crash_phase": {"type": "string"},
        "error_message": {"type": "string"},
        "completed_phases": {"type": "array", "items": {"type": "string"}},
        "timestamp": {"type": "string"},
    },
}


class PipelineState:
    """Manages pipeline state via Mubit working memory variables and goals."""

    def __init__(self, memory: Memory):
        self.memory = memory

    def initialize(self, target_company: str):
        """Set initial state variables."""
        self.memory.set_variable("target_company", target_company)
        self.memory.set_variable("pipeline_status", "initializing")
        self.memory.set_variable("current_phase", "none")
        self.memory.set_variable("completed_phases", [])
        self.memory.set_variable("total_phases", len(get_phases()))
        self.memory.set_variable("phase_outputs", {})

    def define_concepts(self):
        """Define typed schemas for phase outputs and crash reports."""
        self.memory.define_concept("phase_output", PHASE_OUTPUT_SCHEMA)
        self.memory.define_concept("crash_report", CRASH_REPORT_SCHEMA)

    def create_goals(self) -> dict[str, str]:
        """Create a goal tree: root + 6 phase goals. Returns {phase_key: goal_id}."""
        root_id = self.memory.add_goal(
            "Complete technology due diligence for DataPulse Analytics acquisition",
            priority="critical",
        )
        goal_map = {"_root": root_id}
        for phase in get_phases():
            goal_id = self.memory.add_goal(
                f"Phase: {phase.display_name} ({phase.category})",
                priority="high",
                parent_goal_id=root_id,
            )
            goal_map[phase.key] = goal_id
        return goal_map

    def begin_phase(self, phase_key: str):
        """Mark phase as in-progress."""
        self.memory.set_variable("current_phase", phase_key)
        self.memory.set_variable("pipeline_status", "running")

    def complete_phase(self, phase_key: str, goal_id: str | None, output_summary: str = ""):
        """Mark phase as complete, update state."""
        # Update completed phases list
        completed = self._get_completed()
        if phase_key not in completed:
            completed.append(phase_key)
        self.memory.set_variable("completed_phases", completed)
        self.memory.set_variable("current_phase", f"completed:{phase_key}")

        # Update goal
        if goal_id:
            self.memory.update_goal(goal_id, "achieved")

        # Track output summary
        outputs = self._get_outputs()
        outputs[phase_key] = output_summary[:200]
        self.memory.set_variable("phase_outputs", outputs)

    def record_crash(self, phase_key: str, error: str, goal_id: str | None):
        """Record crash state."""
        self.memory.set_variable("pipeline_status", "crashed")
        self.memory.set_variable("crash_error", str(error))
        self.memory.set_variable("crash_phase", phase_key)
        self.memory.set_variable("crash_timestamp",
                                 datetime.now(timezone.utc).isoformat())
        if goal_id:
            self.memory.update_goal(goal_id, "failed")

    def get_completed_phases(self) -> list[str]:
        """Read completed phases from state."""
        return self._get_completed()

    def _get_completed(self) -> list[str]:
        try:
            val = self.memory.get_variable("completed_phases")
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                return json.loads(val)
        except Exception:
            pass
        return []

    def _get_outputs(self) -> dict:
        try:
            val = self.memory.get_variable("phase_outputs")
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                return json.loads(val)
        except Exception:
            pass
        return {}
