"""Base agent — thin wrapper around google-genai."""

from google import genai
from google.genai import types

from .. import config


class BaseAgent:
    """Minimal agent that calls Gemini with a system instruction."""

    def __init__(self, name: str, role: str, instruction: str, *, use_search: bool = False):
        self.name = name
        self.role = role
        self.instruction = instruction
        self.use_search = use_search
        self._client = genai.Client(api_key=config.GOOGLE_API_KEY)

    def run(self, context: str) -> str:
        """Call Gemini with system instruction + context. Returns text."""
        tools = None
        if self.use_search:
            tools = [types.Tool(google_search=types.GoogleSearch())]

        response = self._client.models.generate_content(
            model=config.MODEL,
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=self.instruction,
                tools=tools,
            ),
        )
        return response.text or ""
