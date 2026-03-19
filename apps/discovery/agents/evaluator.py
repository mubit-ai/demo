"""Evaluator agent — scores and ranks discovered tools."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a technology evaluation analyst. Given research findings "
    "from multiple researchers, create a structured evaluation matrix.\n\n"
    "For each tool discovered, score it on a 1-10 scale across:\n"
    "- Pricing fit (does it match the company's budget/stage?)\n"
    "- Feature completeness (does it cover the company's needs?)\n"
    "- Integration ease (how hard is it to integrate?)\n"
    "- Ecosystem/community (documentation, support, community size)\n\n"
    "Present the results as a comparison table. If you have context from "
    "previous evaluations in your memory, reference those findings to "
    "provide more confident scoring.\n\n"
    "Identify the top pick and runner-up in each category."
)


class Evaluator(BaseAgent):
    def __init__(self):
        super().__init__(
            name="evaluator",
            role="tool-evaluation",
            instruction=INSTRUCTION,
        )

    def run(self, findings: dict[str, str], mubit_context: str = "") -> str:
        parts = []
        if mubit_context:
            parts.append(f"Context from past evaluations:\n{mubit_context}\n")
        for name, finding in findings.items():
            parts.append(f"--- {name} ---\n{finding}\n")
        return super().run("\n".join(parts))
