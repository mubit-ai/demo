"""Recommender agent — synthesizes evaluation into a final recommendation."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a chief technology advisor presenting a final recommendation. "
    "Based on the evaluation matrix, produce a complete tech stack "
    "recommendation report:\n\n"
    "1. **Recommended Stack** -- one tool per category with reasoning\n"
    "2. **Alternative Options** -- runner-up for each category\n"
    "3. **Integration Architecture** -- how the tools connect\n"
    "4. **Estimated Costs** -- monthly/annual costs at the company's scale\n"
    "5. **Implementation Roadmap** -- suggested order of adoption\n\n"
    "If you have context from past recommendations for similar companies, "
    "reference those to strengthen your advice. Be decisive and opinionated."
)


class Recommender(BaseAgent):
    def __init__(self):
        super().__init__(
            name="recommender",
            role="stack-recommendation",
            instruction=INSTRUCTION,
        )

    def run(self, evaluation: str, mubit_context: str = "") -> str:
        context = evaluation
        if mubit_context:
            context = (
                f"Lessons from past recommendations:\n{mubit_context}\n\n"
                f"Current evaluation:\n{evaluation}"
            )
        return super().run(context)
