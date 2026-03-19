"""Autonomous orchestrator agent — uses Gemini function calling with Mubit as tools.

The agent decides when to store memories, recall context, set goals, checkpoint,
and reflect. No hardcoded pipeline — the LLM drives the flow.
"""

import io
import logging
import sys
import warnings

# Suppress the AFC compatibility warning from google-genai
logging.getLogger("google.genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*AFC.*")

from google import genai
from google.genai import types

from . import config
from .memory import Memory
from .tools import AGENT_TOOLS


SYSTEM_INSTRUCTION = (
    "You are an autonomous research advisor with access to a persistent memory system "
    "(Mubit) and web search. You help companies make technology decisions.\n\n"
    "You have these capabilities:\n"
    "- **Web search**: Search the internet for current information\n"
    "- **store_memory**: Save findings, lessons, or rules to your long-term memory\n"
    "- **recall_memory**: Search your memory for prior knowledge before starting research\n"
    "- **get_assembled_context**: Get a comprehensive context block from all your memories\n"
    "- **create_checkpoint**: Save your progress so it can be recovered\n"
    "- **set_goal / update_goal**: Track what you're trying to achieve\n"
    "- **reflect_on_session**: Extract higher-order lessons from your research\n"
    "- **archive_artifact**: Store final deliverables as immutable artifacts\n"
    "- **check_memory_health**: Check your memory statistics\n"
    "- **surface_strategies**: Find patterns across your accumulated lessons\n\n"
    "**How to work:**\n"
    "1. ALWAYS start by recalling prior knowledge — you may have done related research before\n"
    "2. Set a goal for what you're trying to achieve\n"
    "3. Research using web search, storing important findings in memory as you go\n"
    "4. Checkpoint your progress after major milestones\n"
    "5. When done researching, get your assembled context and produce a recommendation\n"
    "6. Archive your final recommendation\n"
    "7. Reflect on the session to extract lessons for future use\n"
    "8. Check memory health at the end\n\n"
    "Be thorough but efficient. Use memory tools proactively — they make you smarter "
    "over time. When you find something important, store it immediately."
)

MAX_TURNS = 25  # Safety limit for the tool-use loop


class OrchestratorAgent:
    """Autonomous agent that uses Gemini function calling with Mubit tools."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self._client = genai.Client(api_key=config.GOOGLE_API_KEY)
        self._conversation: list[types.Content] = []

    def run(self, query: str) -> str:
        """Run the agent on a query. Returns the final text response."""
        print(f"\n  Query: {query}\n")

        # Start conversation with user query
        self._conversation = [
            types.Content(parts=[types.Part(text=query)], role="user"),
        ]

        final_text = ""
        turn = 0

        while turn < MAX_TURNS:
            turn += 1

            # Call Gemini with tools (suppress AFC compatibility warning)
            _stderr = sys.stderr
            sys.stderr = io.StringIO()
            try:
                response = self._client.models.generate_content(
                    model=config.MODEL,
                    contents=self._conversation,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        tools=AGENT_TOOLS,
                        tool_config=types.ToolConfig(
                            include_server_side_tool_invocations=True,
                        ),
                    ),
                )
            finally:
                sys.stderr = _stderr

            candidate = response.candidates[0]
            parts = candidate.content.parts

            # Separate function calls from text
            function_calls = [p for p in parts if p.function_call]
            text_parts = [p for p in parts if p.text]

            # If there are text parts, accumulate them
            if text_parts:
                final_text = "\n".join(p.text for p in text_parts if p.text)

            # Add model response to conversation
            self._conversation.append(candidate.content)

            # If no function calls, we're done
            if not function_calls:
                if final_text:
                    print(f"  [agent] Final response ({len(final_text)} chars)")
                break

            # Execute function calls and send results back
            function_responses = []
            for part in function_calls:
                fc = part.function_call
                tool_name = fc.name
                args = dict(fc.args) if fc.args else {}

                # Print what the agent is doing
                args_preview = ", ".join(f"{k}={str(v)[:50]}" for k, v in args.items())
                print(f"  [agent] {tool_name}({args_preview})")

                # Execute via Mubit memory wrapper
                result = self.memory.execute_tool(tool_name, args)

                # Print result preview
                preview = result[:150].replace("\n", " ")
                print(f"          → {preview}{'...' if len(result) > 150 else ''}")

                function_responses.append(
                    types.Part(function_response=types.FunctionResponse(
                        name=tool_name,
                        response={"result": result},
                    ))
                )

            # Add all function responses as a single user turn
            self._conversation.append(
                types.Content(parts=function_responses, role="user")
            )

        if turn >= MAX_TURNS:
            print(f"  [agent] Reached max turns ({MAX_TURNS})")

        return final_text
