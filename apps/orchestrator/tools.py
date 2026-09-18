"""Mubit APIs as Gemini function calling tool declarations.

The agent decides when to use these — no hardcoded pipeline.
"""

from google.genai import types


# ── Mubit tool declarations for Gemini function calling ──────────────

MUBIT_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="store_memory",
        description=(
            "Store a finding, insight, or decision in long-term memory. "
            "Use this to remember important facts, lessons learned, or rules "
            "discovered during research. Stored memories persist across sessions "
            "and can be recalled later."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "content": types.Schema(
                    type="STRING",
                    description="The content to remember (be detailed and specific)",
                ),
                "intent": types.Schema(
                    type="STRING",
                    enum=["fact", "lesson", "rule", "trace"],
                    description=(
                        "Type: 'fact' for data/findings, 'lesson' for insights/learnings, "
                        "'rule' for guidelines/constraints, 'trace' for activity logs"
                    ),
                ),
                "importance": types.Schema(
                    type="STRING",
                    enum=["low", "medium", "high", "critical"],
                    description="How important this memory is for future recall",
                ),
            },
            required=["content", "intent"],
        ),
    ),
    types.FunctionDeclaration(
        name="recall_memory",
        description=(
            "Search your memory for relevant prior knowledge. Use this before "
            "starting research to check what you already know, or to find "
            "related findings from past sessions."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(
                    type="STRING",
                    description="What to search for in memory",
                ),
                "types": types.Schema(
                    type="STRING",
                    description="Comma-separated entry types to search: fact,lesson,rule",
                ),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_assembled_context",
        description=(
            "Get a structured, token-budgeted context block assembled from all "
            "relevant memories. Use this when you need a comprehensive summary "
            "of everything you know about a topic before making a decision."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(
                    type="STRING",
                    description="Topic to assemble context for",
                ),
                "max_tokens": types.Schema(
                    type="INTEGER",
                    description="Maximum tokens for the context block (default 1500)",
                ),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name="create_checkpoint",
        description=(
            "Save a checkpoint of your current progress. Use this after "
            "completing a significant research milestone so your work can "
            "be recovered if the session is interrupted."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "label": types.Schema(
                    type="STRING",
                    description="Short label for the checkpoint",
                ),
                "snapshot": types.Schema(
                    type="STRING",
                    description="Summary of what you've accomplished so far",
                ),
            },
            required=["label", "snapshot"],
        ),
    ),
    types.FunctionDeclaration(
        name="set_goal",
        description=(
            "Set a research goal to track your progress. Use this at the "
            "start of a task to define what you're trying to achieve."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "description": types.Schema(
                    type="STRING",
                    description="What you want to achieve",
                ),
                "priority": types.Schema(
                    type="STRING",
                    enum=["low", "medium", "high", "critical"],
                    description="Priority level",
                ),
            },
            required=["description"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_goal",
        description=(
            "Update the status of a previously set goal. Use this when "
            "you've achieved a goal or need to mark it as blocked."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "goal_id": types.Schema(
                    type="STRING",
                    description="The goal ID returned by set_goal",
                ),
                "status": types.Schema(
                    type="STRING",
                    enum=["active", "achieved", "failed", "blocked"],
                    description="New status",
                ),
            },
            required=["goal_id", "status"],
        ),
    ),
    types.FunctionDeclaration(
        name="reflect_on_session",
        description=(
            "Trigger reflection to extract higher-order lessons from "
            "everything stored in this session. Use this after completing "
            "research to distill insights."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={},
        ),
    ),
    types.FunctionDeclaration(
        name="archive_artifact",
        description=(
            "Archive a final deliverable (report, recommendation) as an "
            "immutable, exact-reference artifact that can be retrieved later."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "content": types.Schema(
                    type="STRING",
                    description="The full content to archive",
                ),
                "artifact_kind": types.Schema(
                    type="STRING",
                    description="Type of artifact: recommendation, report, analysis",
                ),
            },
            required=["content", "artifact_kind"],
        ),
    ),
    types.FunctionDeclaration(
        name="check_memory_health",
        description=(
            "Check the health and statistics of your memory. Shows entry "
            "counts by type, section confidence, and staleness metrics."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={},
        ),
    ),
    types.FunctionDeclaration(
        name="surface_strategies",
        description=(
            "Find high-level strategies and patterns across all your "
            "accumulated lessons. Use this to discover recurring themes "
            "in your research."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={},
        ),
    ),
]

# Combined tools: Mubit functions + Google Search
AGENT_TOOLS = [
    types.Tool(function_declarations=MUBIT_TOOL_DECLARATIONS),
    types.Tool(google_search=types.GoogleSearch()),
]
