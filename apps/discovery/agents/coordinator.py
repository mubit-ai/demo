"""Coordinator agent — plans research strategy."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a senior solutions architect at a software advisory firm. "
    "Given a business context, analyze the company's needs and create a "
    "concise research plan. Identify the 3 most critical software categories "
    "they need. For each category, specify what to look for: key features, "
    "budget constraints, and integration requirements.\n\n"
    "Output a clear, structured plan that downstream researchers can follow."
)


class Coordinator(BaseAgent):
    def __init__(self):
        super().__init__(
            name="coordinator",
            role="solutions-architect",
            instruction=INSTRUCTION,
        )

    def run(self, query: str, prior_context: str = "") -> str:
        context = query
        if prior_context:
            context = (
                f"Previous research context:\n{prior_context}\n\n"
                f"New request:\n{query}"
            )
        return super().run(context)
