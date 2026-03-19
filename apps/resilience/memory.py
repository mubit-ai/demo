"""Extended Mubit wrapper — typed methods for ALL control plane APIs."""

import json

import mubit


class Memory:
    """Full Mubit wrapper covering 30+ APIs including control plane."""

    def __init__(self, endpoint: str, api_key: str, session_id: str):
        self.client = mubit.Client(endpoint=endpoint, api_key=api_key, run_id=session_id)
        self.client.set_transport("http")
        self.session_id = session_id

    def set_session(self, session_id: str):
        self.session_id = session_id
        self.client.set_run_id(session_id)

    # ── Core memory ──────────────────────────────────────────────────────

    def store_finding(self, agent_name: str, content: str, intent: str = "fact",
                      importance: str = "medium"):
        """remember() — store an agent's output."""
        self.client.remember(
            session_id=self.session_id, agent_id=agent_name,
            content=content[:3000], intent=intent, importance=importance,
            metadata={"agent": agent_name, "session": self.session_id},
        )

    def recall_prior(self, query: str, entry_types: list[str] | None = None,
                     limit: int = 5, include_linked_runs: bool = False) -> str:
        """recall() — search for prior findings. Returns formatted context."""
        result = self.client.recall(
            session_id=self.session_id, query=query,
            entry_types=entry_types or ["fact", "lesson"],
            limit=limit, include_linked_runs=include_linked_runs,
        )
        evidence = result.get("evidence", [])
        if not evidence:
            return ""
        lines = []
        for e in evidence:
            lines.append(f"[{e.get('entry_type')}] {e.get('content', '')[:300]}")
        return "\n".join(lines)

    def get_context(self, query: str, max_tokens: int = 1200) -> str:
        """get_context() — assembled context block."""
        result = self.client.get_context(
            session_id=self.session_id, query=query,
            entry_types=["fact", "lesson", "rule", "feedback", "step_outcome"],
            max_token_budget=max_tokens,
        )
        return result.get("context_block", "")

    # ── Handoffs & feedback ──────────────────────────────────────────────

    def store_handoff(self, from_agent: str, to_agent: str, content: str) -> str | None:
        """handoff()"""
        result = self.client.handoff(
            session_id=self.session_id, task_id="due-diligence",
            from_agent_id=from_agent, to_agent_id=to_agent,
            content=content, requested_action="execute",
        )
        return result.get("handoff_id")

    def store_feedback(self, handoff_id: str, verdict: str, comments: str = ""):
        """feedback()"""
        self.client.feedback(
            session_id=self.session_id, handoff_id=handoff_id,
            verdict=verdict, comments=comments,
        )

    # ── Checkpoints & archives ───────────────────────────────────────────

    def checkpoint(self, label: str, snapshot: str):
        """checkpoint()"""
        self.client.checkpoint(
            session_id=self.session_id, label=label,
            context_snapshot=snapshot,
        )

    def archive_report(self, content: str) -> str | None:
        """archive()"""
        result = self.client.archive(
            session_id=self.session_id, content=content[:5000],
            artifact_kind="due_diligence_report",
            agent_id="report_writer", origin_agent_id="report_writer",
            family="tech-due-diligence",
            labels=["due-diligence", "executive-report", "acquisition"],
        )
        return result.get("reference_id")

    def verify_archive(self, reference_id: str) -> dict:
        """dereference()"""
        return self.client.dereference(
            session_id=self.session_id, reference_id=reference_id,
        )

    # ── Agent management ─────────────────────────────────────────────────

    def register_agents(self, agents):
        """register_agent() for each agent."""
        for agent in agents:
            self.client.register_agent(
                session_id=self.session_id, agent_id=agent.name,
                role=agent.role, capabilities=[agent.role],
                read_scopes=["fact", "lesson", "rule", "trace", "handoff",
                             "feedback", "step_outcome", "checkpoint"],
                write_scopes=["fact", "lesson", "trace", "feedback", "archive_block"],
            )

    def list_agents(self) -> list:
        """list_agents()"""
        result = self.client.list_agents(session_id=self.session_id)
        return result.get("agents", [])

    # ── Learning & reflection ────────────────────────────────────────────

    def record_success(self, rationale: str):
        """Store a success lesson and record outcome."""
        self.client.remember(
            session_id=self.session_id, agent_id="pipeline",
            content=rationale, intent="lesson",
            lesson_type="success", lesson_scope="global",
            lesson_importance="high",
        )
        try:
            lessons = self.client.control.lessons({"run_id": self.session_id, "limit": 5})
            lesson_list = lessons.get("lessons", [])
            if lesson_list:
                self.client.record_outcome(
                    session_id=self.session_id,
                    reference_id=lesson_list[0]["id"],
                    outcome="success", signal=0.9,
                    rationale=rationale,
                )
        except Exception:
            pass

    def reflect(self, include_step_outcomes: bool = False) -> dict:
        """reflect()"""
        payload = {"run_id": self.session_id, "include_step_outcomes": include_step_outcomes}
        return self.client.control.reflect(payload)

    def strategies(self) -> dict:
        """surface_strategies()"""
        return self.client.surface_strategies(session_id=self.session_id)

    def health(self) -> dict:
        """memory_health()"""
        return self.client.memory_health(session_id=self.session_id)

    # ── NEW: Working memory variables ────────────────────────────────────

    def set_variable(self, name: str, value, source: str = "pipeline"):
        """control.set_variable() — store a state variable."""
        self.client.control.set_variable({
            "run_id": self.session_id,
            "name": name,
            "value_json": json.dumps(value),
            "source": source,
        })

    def get_variable(self, name: str):
        """control.get_variable() — read a state variable."""
        result = self.client.control.get_variable({
            "run_id": self.session_id,
            "name": name,
        })
        value_json = result.get("value_json", result.get("value", "null"))
        if isinstance(value_json, str):
            try:
                return json.loads(value_json)
            except (json.JSONDecodeError, TypeError):
                return value_json
        return value_json

    def list_variables(self) -> dict:
        """control.list_variables() — enumerate all state."""
        result = self.client.control.list_variables({"run_id": self.session_id})
        variables = {}
        for v in result.get("variables", []):
            name = v.get("name", "")
            val = v.get("value_json", v.get("value", "null"))
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
            variables[name] = val
        return variables

    def delete_variable(self, name: str):
        """control.delete_variable()"""
        self.client.control.delete_variable({
            "run_id": self.session_id,
            "name": name,
        })

    # ── NEW: Goals ───────────────────────────────────────────────────────

    def add_goal(self, description: str, priority: str = "high",
                 parent_goal_id: str | None = None) -> str | None:
        """control.add_goal() — create a goal, returns goal_id."""
        payload = {
            "run_id": self.session_id,
            "description": description,
            "priority": priority,
        }
        if parent_goal_id:
            payload["parent_goal_id"] = parent_goal_id
        result = self.client.control.add_goal(payload)
        return result.get("goal_id", result.get("id"))

    def update_goal(self, goal_id: str, status: str):
        """control.update_goal() — mark goal achieved/failed/pending."""
        self.client.control.update_goal({
            "run_id": self.session_id,
            "goal_id": goal_id,
            "status": status,
        })

    def list_goals(self, status_filter: str | None = None) -> list:
        """control.list_goals()"""
        payload = {"run_id": self.session_id}
        if status_filter:
            payload["status_filter"] = status_filter
        result = self.client.control.list_goals(payload)
        return result.get("goals", [])

    def get_goal_tree(self, root_goal_id: str | None = None) -> dict:
        """control.get_goal_tree()"""
        payload = {"run_id": self.session_id}
        if root_goal_id:
            payload["root_goal_id"] = root_goal_id
        return self.client.control.get_goal_tree(payload)

    # ── NEW: Actions & decisions ─────────────────────────────────────────

    def submit_action(self, agent_id: str, action_type: str, action_data: dict):
        """control.submit_action() — log a pipeline decision."""
        self.client.control.submit_action({
            "run_id": self.session_id,
            "agent_id": agent_id,
            "action_type": action_type,
            "action_json": json.dumps(action_data),
        })

    def get_action_log(self, limit: int = 10) -> list:
        """control.get_action_log()"""
        result = self.client.control.get_action_log({
            "run_id": self.session_id,
            "limit": limit,
        })
        return result.get("actions", [])

    def run_cycle(self, agent_id: str) -> dict:
        """control.run_cycle() — execute a decision cycle."""
        return self.client.control.run_cycle({
            "run_id": self.session_id,
            "agent_id": agent_id,
        })

    def get_cycle_history(self, limit: int = 5) -> list:
        """control.get_cycle_history()"""
        result = self.client.control.get_cycle_history({
            "run_id": self.session_id,
            "limit": limit,
        })
        return result.get("cycles", [])

    # ── NEW: Step outcomes ───────────────────────────────────────────────

    def record_step_outcome(self, step_id: str, step_name: str, outcome: str,
                            signal: float, rationale: str, agent_id: str = "pipeline"):
        """control.record_step_outcome() — per-phase process reward."""
        self.client.control.record_step_outcome({
            "run_id": self.session_id,
            "step_id": step_id,
            "step_name": step_name,
            "outcome": outcome,
            "signal": signal,
            "rationale": rationale,
            "agent_id": agent_id,
        })

    # ── NEW: Agent lifecycle ─────────────────────────────────────────────

    def agent_heartbeat(self, agent_id: str, status: str = "active"):
        """control.agent_heartbeat() — agent liveness signal."""
        self.client.control.agent_heartbeat({
            "run_id": self.session_id,
            "agent_id": agent_id,
            "status": status,
        })

    # ── NEW: Activity timeline ───────────────────────────────────────────

    def append_activity(self, agent_id: str, activity_type: str, payload: dict):
        """control.append_activity() — structured activity record."""
        self.client.control.append_activity({
            "run_id": self.session_id,
            "agent_id": agent_id,
            "activity": {
                "type": activity_type,
                "payload": json.dumps(payload),
                "agent_id": agent_id,
            },
        })

    def list_activity(self, limit: int = 20) -> list:
        """control.list_activity()"""
        result = self.client.control.list_activity({
            "run_id": self.session_id,
            "limit": limit,
            "sort": "desc",
        })
        return result.get("entries", result.get("activities", []))

    def export_activity(self) -> dict:
        """control.export_activity()"""
        return self.client.control.export_activity({
            "run_id": self.session_id,
        })

    # ── NEW: Run management ──────────────────────────────────────────────

    def link_run(self, linked_run_id: str):
        """control.link_run() — link crash run to resume run."""
        self.client.control.link_run({
            "run_id": self.session_id,
            "linked_run_id": linked_run_id,
        })

    def get_run_snapshot(self, timeline_limit: int = 20) -> dict:
        """control.context_snapshot() — full run state snapshot."""
        return self.client.control.context_snapshot({
            "run_id": self.session_id,
            "timeline_limit": timeline_limit,
        })

    # ── NEW: Concepts ────────────────────────────────────────────────────

    def define_concept(self, name: str, schema: dict):
        """control.define_concept() — define a typed schema."""
        self.client.control.define_concept({
            "run_id": self.session_id,
            "name": name,
            "schema_json": json.dumps(schema),
        })

    def list_concepts(self) -> list:
        """control.list_concepts()"""
        result = self.client.control.list_concepts({"run_id": self.session_id})
        return result.get("concepts", [])

    # ── NEW: Diagnostics ─────────────────────────────────────────────────

    def diagnose_error(self, error_text: str, error_type: str | None = None) -> dict:
        """diagnose() — analyze error against past failure patterns."""
        return self.client.diagnose(
            session_id=self.session_id,
            error_text=error_text,
            error_type=error_type,
            limit=10,
        )

    # ── Display helpers ──────────────────────────────────────────────────

    def print_health(self):
        h = self.health()
        counts = h.get("entry_counts", {})
        print(f"  Entry counts: {json.dumps(counts, indent=4)}")
        print(f"  Total: {sum(counts.values()) if counts else 0}")
        sections = h.get("section_health", [])
        if sections:
            print(f"  Section health:")
            for s in sections:
                print(f"    {s.get('section_name', '?'):20s}  "
                      f"count={s.get('count', 0):3d}  "
                      f"confidence={s.get('avg_confidence', 0):.2f}")

    def print_reflection(self, result: dict):
        print(f"  Confidence: {result.get('confidence')}")
        print(f"  Lessons extracted: {result.get('lessons_stored', 0)}")
        for lesson in result.get("lessons", []):
            ltype = lesson.get("lesson_type", "?")
            content = lesson.get("content", "")[:120]
            print(f"    [{ltype:10s}] {content}...")
        summary = result.get("summary", "")
        if summary:
            print(f"  Summary: {summary[:200]}...")

    def print_strategies(self, result: dict):
        strats = result.get("strategies", [])
        print(f"  Found {len(strats)} strategies:")
        for s in strats[:3]:
            count = s.get("supporting_lesson_count", 0)
            desc = s.get("description", "")[:120]
            print(f"    [{count} lessons] {desc}...")

    def print_goals(self, goals: list):
        for g in goals:
            status = g.get("status", "?")
            desc = g.get("description", "")[:80]
            print(f"    [{status:10s}] {desc}")

    def print_goal_tree(self, tree: dict):
        def _walk(node, indent=0):
            desc = node.get("description", "")[:60]
            status = node.get("status", "?")
            print(f"    {'  ' * indent}[{status}] {desc}")
            for child in node.get("children", []):
                _walk(child, indent + 1)
        if "root" in tree:
            _walk(tree["root"])
        elif "goals" in tree:
            for g in tree["goals"]:
                _walk(g)
        else:
            _walk(tree)

    def print_variables(self, variables: dict):
        for name, value in variables.items():
            val_str = json.dumps(value) if not isinstance(value, str) else value
            if len(val_str) > 80:
                val_str = val_str[:77] + "..."
            print(f"    {name:25s} = {val_str}")
