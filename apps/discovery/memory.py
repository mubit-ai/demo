"""Mubit memory wrapper — typed methods for each API call."""

import json

import mubit


class Memory:
    """Typed wrapper around mubit.Client for the discovery pipeline."""

    def __init__(self, endpoint: str, api_key: str, session_id: str):
        self.client = mubit.Client(endpoint=endpoint, api_key=api_key, run_id=session_id)
        self.client.set_transport("http")
        self.session_id = session_id

    def set_session(self, session_id: str):
        self.session_id = session_id
        self.client.set_run_id(session_id)

    # ── Core memory operations ───────────────────────────────────────────

    def store_finding(self, agent_name: str, content: str, intent: str = "fact",
                      importance: str = "medium", occurrence_time: int | None = None):
        """remember() — store an agent's output."""
        self.client.remember(
            session_id=self.session_id,
            agent_id=agent_name,
            content=content[:2000],
            intent=intent,
            importance=importance,
            metadata={"agent": agent_name, "session": self.session_id},
            occurrence_time=occurrence_time,
        )

    def store_mental_model(self, agent_name: str, content: str, entity: str):
        """remember() — store a curated mental model summary (highest retrieval priority)."""
        self.client.remember(
            session_id=self.session_id,
            agent_id=agent_name,
            content=content[:3000],
            intent="mental_model",
            importance="critical",
            metadata={
                "agent": agent_name,
                "entity": entity,
                "consolidated": True,
                "session": self.session_id,
            },
        )

    def recall_prior(self, query: str, entry_types: list[str] | None = None,
                     limit: int = 5, min_timestamp: int | None = None,
                     max_timestamp: int | None = None, budget: str | None = None) -> str:
        """recall() — search for prior research. Returns formatted context."""
        result = self.client.recall(
            session_id=self.session_id,
            query=query,
            entry_types=entry_types or ["fact", "lesson", "mental_model"],
            limit=limit,
            min_timestamp=min_timestamp,
            max_timestamp=max_timestamp,
            budget=budget,
        )
        evidence = result.get("evidence", [])
        if not evidence:
            return ""
        lines = []
        for e in evidence:
            stale_marker = " [STALE]" if e.get("is_stale") else ""
            lines.append(f"[{e.get('entry_type')}]{stale_marker} {e.get('content', '')[:200]}")
        return "\n".join(lines)

    def get_context(self, query: str, max_tokens: int = 800) -> str:
        """get_context() — returns assembled context block string."""
        result = self.client.get_context(
            session_id=self.session_id,
            query=query,
            entry_types=["mental_model", "fact", "lesson", "rule", "feedback"],
            max_token_budget=max_tokens,
        )
        return result.get("context_block", "")

    # ── Multi-agent coordination ─────────────────────────────────────────

    def store_handoff(self, from_agent: str, to_agent: str, content: str) -> str | None:
        """handoff() — record agent-to-agent handoff."""
        result = self.client.handoff(
            session_id=self.session_id,
            task_id="discovery",
            from_agent_id=from_agent,
            to_agent_id=to_agent,
            content=content,
            requested_action="execute",
        )
        return result.get("handoff_id")

    def store_feedback(self, handoff_id: str, verdict: str, comments: str = ""):
        """feedback() — submit feedback on a handoff."""
        self.client.feedback(
            session_id=self.session_id,
            handoff_id=handoff_id,
            verdict=verdict,
            comments=comments,
        )

    # ── Checkpoints & archives ───────────────────────────────────────────

    def checkpoint(self, label: str, snapshot: str):
        """checkpoint() — create a named memory checkpoint."""
        self.client.checkpoint(
            session_id=self.session_id,
            label=label,
            context_snapshot=snapshot,
        )

    def archive_report(self, content: str) -> str | None:
        """archive() — store recommendation as exact-reference artifact."""
        result = self.client.archive(
            session_id=self.session_id,
            content=content[:3000],
            artifact_kind="recommendation_report",
            agent_id="recommender",
            origin_agent_id="recommender",
            family="software-discovery",
            labels=["recommendation", "tech-stack"],
        )
        return result.get("reference_id")

    def verify_archive(self, reference_id: str) -> dict:
        """dereference() — retrieve archived artifact by reference ID."""
        return self.client.dereference(
            session_id=self.session_id,
            reference_id=reference_id,
        )

    # ── Learning & reflection ────────────────────────────────────────────

    def record_success(self, rationale: str):
        """Store a success lesson and record outcome."""
        self.client.remember(
            session_id=self.session_id,
            agent_id="recommender",
            content=rationale,
            intent="lesson",
            lesson_type="success",
            lesson_scope="global",
            lesson_importance="high",
        )
        try:
            lessons = self.client.lessons({"run_id": self.session_id, "limit": 5})
            lesson_list = lessons.get("lessons", [])
            if lesson_list:
                self.client.record_outcome(
                    session_id=self.session_id,
                    reference_id=lesson_list[0]["id"],
                    outcome="success",
                    signal=0.85,
                    rationale=rationale,
                )
        except Exception:
            pass

    def reflect(self) -> dict:
        """reflect() — extract lessons from session findings."""
        return self.client.reflect(session_id=self.session_id)

    def strategies(self) -> dict:
        """surface_strategies() — cluster high-value lessons."""
        return self.client.surface_strategies(session_id=self.session_id)

    def health(self) -> dict:
        """memory_health() — entry counts and section confidence."""
        return self.client.memory_health(session_id=self.session_id)

    # ── Agent registration ───────────────────────────────────────────────

    def register_agents(self, agents):
        """Register a list of agents with their roles."""
        for agent in agents:
            self.client.register_agent(
                session_id=self.session_id,
                agent_id=agent.name,
                role=agent.role,
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

    def print_context(self, block: str):
        if not block:
            print("  (entries may still be indexing)")
            return
        print(f"  {'':>2}{'':>66}")
        for line in block.split("\n")[:20]:
            print(f"    {line}")
        if block.count("\n") > 20:
            print(f"    ... ({block.count(chr(10)) - 20} more lines)")
