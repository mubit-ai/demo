"""Mubit client wrapper — executes tool calls from the agent."""

import json

import mubit


class Memory:
    """Wraps mubit.Client and dispatches tool calls from the agent."""

    def __init__(self, endpoint: str, api_key: str, session_id: str):
        self.client = mubit.Client(endpoint=endpoint, api_key=api_key, run_id=session_id)
        self.client.set_transport("http")
        self.session_id = session_id

    def set_session(self, session_id: str):
        self.session_id = session_id
        self.client.set_run_id(session_id)

    def execute_tool(self, tool_name: str, args: dict) -> str:
        """Dispatch a tool call to the appropriate Mubit API. Returns result as string."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return f"Unknown tool: {tool_name}"
        try:
            result = handler(args)
            return result if isinstance(result, str) else json.dumps(result, default=str)
        except Exception as e:
            msg = str(e)
            if "Max retries exceeded" in msg or "Connection refused" in msg:
                return "Mubit API temporarily unavailable — continue without this result"
            return f"Error: {msg[:200]}"

    # ── Tool handlers ────────────────────────────────────────────────────

    def _tool_store_memory(self, args: dict) -> str:
        content = args.get("content", "")
        intent = args.get("intent", "fact")
        importance = args.get("importance", "medium")
        occurrence_time = args.get("occurrence_time")
        self.client.remember(
            session_id=self.session_id,
            agent_id="orchestrator",
            content=content[:3000],
            intent=intent,
            importance=importance,
            metadata={"source": "orchestrator", "session": self.session_id},
            occurrence_time=occurrence_time,
        )
        return f"Stored in memory as {intent} (importance: {importance})"

    def _tool_store_mental_model(self, args: dict) -> str:
        content = args.get("content", "")
        entity = args.get("entity", "unknown")
        self.client.remember(
            session_id=self.session_id,
            agent_id="orchestrator",
            content=content[:3000],
            intent="mental_model",
            importance="critical",
            metadata={
                "source": "orchestrator",
                "entity": entity,
                "consolidated": True,
                "session": self.session_id,
            },
        )
        return f"Stored mental model for entity '{entity}' (importance: critical)"

    def _tool_recall_memory(self, args: dict) -> str:
        query = args.get("query", "")
        types_str = args.get("types", "mental_model,fact,lesson,rule")
        entry_types = [t.strip() for t in types_str.split(",") if t.strip()]
        min_timestamp = args.get("min_timestamp")
        max_timestamp = args.get("max_timestamp")
        budget = args.get("budget")
        result = self.client.recall(
            session_id=self.session_id,
            query=query,
            entry_types=entry_types,
            limit=5,
            include_linked_runs=True,
            min_timestamp=min_timestamp,
            max_timestamp=max_timestamp,
            budget=budget,
        )
        evidence = result.get("evidence", [])
        if not evidence:
            return "No relevant memories found."
        lines = []
        for e in evidence:
            etype = e.get("entry_type", "?")
            content = e.get("content", "")[:500]
            score = e.get("score", 0)
            stale_marker = " [STALE]" if e.get("is_stale") else ""
            lines.append(f"[{etype}, relevance={score:.2f}]{stale_marker} {content}")
        return "\n\n".join(lines)

    def _tool_get_assembled_context(self, args: dict) -> str:
        query = args.get("query", "")
        max_tokens = args.get("max_tokens", 1500)
        result = self.client.get_context(
            session_id=self.session_id,
            query=query,
            entry_types=["mental_model", "fact", "lesson", "rule", "feedback"],
            max_token_budget=max_tokens,
        )
        block = result.get("context_block", "")
        if not block:
            return "No context available yet — store some memories first."
        sources = result.get("source_counts_by_entry_type", {})
        return f"Context ({sum(sources.values())} sources: {json.dumps(sources)}):\n\n{block}"

    def _tool_create_checkpoint(self, args: dict) -> str:
        label = args.get("label", "checkpoint")
        snapshot = args.get("snapshot", "")
        self.client.checkpoint(
            session_id=self.session_id,
            label=label,
            context_snapshot=snapshot,
        )
        return f"Checkpoint '{label}' created."

    def _tool_set_goal(self, args: dict) -> str:
        description = args.get("description", "")
        priority = args.get("priority", "high")
        result = self.client.control.add_goal({
            "run_id": self.session_id,
            "description": description,
            "priority": priority,
        })
        goal_id = result.get("goal_id", result.get("id", "unknown"))
        return f"Goal created (id: {goal_id}): {description}"

    def _tool_update_goal(self, args: dict) -> str:
        goal_id = args.get("goal_id", "")
        status = args.get("status", "achieved")
        self.client.control.update_goal({
            "run_id": self.session_id,
            "goal_id": goal_id,
            "status": status,
        })
        return f"Goal {goal_id} updated to '{status}'."

    def _tool_reflect_on_session(self, args: dict) -> str:
        result = self.client.reflect(session_id=self.session_id)
        lessons = result.get("lessons", [])
        if not lessons:
            summary = result.get("summary", "No lessons extracted yet.")
            return f"Reflection complete. {summary}"
        lines = [f"Reflection extracted {len(lessons)} lessons:"]
        for l in lessons:
            ltype = l.get("lesson_type", "?")
            content = l.get("content", "")[:200]
            lines.append(f"  [{ltype}] {content}")
        summary = result.get("summary", "")
        if summary:
            lines.append(f"\nSummary: {summary[:300]}")
        return "\n".join(lines)

    def _tool_archive_artifact(self, args: dict) -> str:
        content = args.get("content", "")
        kind = args.get("artifact_kind", "recommendation")
        result = self.client.archive(
            session_id=self.session_id,
            content=content[:5000],
            artifact_kind=kind,
            agent_id="orchestrator",
            origin_agent_id="orchestrator",
            family="orchestrator",
            labels=["autonomous", kind],
        )
        ref_id = result.get("reference_id", "unknown")
        return f"Archived as {kind} (reference: {ref_id})"

    def _tool_check_memory_health(self, args: dict) -> str:
        result = self.client.memory_health(session_id=self.session_id)
        counts = result.get("entry_counts", {})
        total = sum(counts.values()) if counts else 0
        sections = result.get("section_health", [])
        lines = [f"Memory health — {total} entries:"]
        for etype, count in counts.items():
            lines.append(f"  {etype}: {count}")
        if sections:
            lines.append("Section confidence:")
            for s in sections:
                name = s.get("section_name", "?")
                count = s.get("count", 0)
                conf = s.get("avg_confidence", 0)
                lines.append(f"  {name}: {count} entries, confidence={conf:.2f}")
        return "\n".join(lines)

    def _tool_surface_strategies(self, args: dict) -> str:
        result = self.client.surface_strategies(session_id=self.session_id)
        strats = result.get("strategies", [])
        if not strats:
            return "No strategies found yet — need more lessons stored first."
        lines = [f"Found {len(strats)} strategies:"]
        for s in strats[:5]:
            count = s.get("supporting_lesson_count", 0)
            desc = s.get("description", "")[:200]
            lines.append(f"  [{count} lessons] {desc}")
        return "\n".join(lines)

    # ── Registration ─────────────────────────────────────────────────────

    def register_agent(self):
        """Register the orchestrator agent."""
        self.client.register_agent(
            session_id=self.session_id,
            agent_id="orchestrator",
            role="autonomous-advisor",
            capabilities=["research", "analysis", "recommendation",
                          "memory-management", "goal-tracking"],
            read_scopes=["fact", "lesson", "rule", "trace", "handoff",
                         "feedback", "step_outcome", "checkpoint"],
            write_scopes=["fact", "lesson", "trace", "feedback", "archive_block"],
        )
