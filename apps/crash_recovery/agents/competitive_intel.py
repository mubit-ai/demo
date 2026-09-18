"""Competitive Intelligence agent — Phase 3: Competitors, moats, differentiation."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a competitive intelligence analyst specializing in B2B SaaS markets. "
    "Analyze the competitive landscape for the acquisition target:\n\n"
    "1. **Direct Competitors** — top 3-5 companies in the same space. For each: "
    "product overview, pricing, funding/revenue, key differentiators\n"
    "2. **Indirect Competitors** — adjacent products that could expand into this space\n"
    "3. **Competitive Moats** — what defensible advantages does the target have? "
    "(data network effects, switching costs, proprietary tech, brand)\n"
    "4. **Market Positioning** — where does the target sit? (leader, challenger, niche)\n"
    "5. **Switching Costs** — how hard is it for customers to leave? What's the churn risk?\n"
    "6. **Threat Assessment** — likelihood of new entrants, big tech entry, commoditization\n\n"
    "Use real, current data from the web. Reference earlier market and technical "
    "findings to contextualize the competitive position."
)


class CompetitiveIntel(BaseAgent):
    def __init__(self):
        super().__init__(
            name="competitive_intel",
            role="competitive-analysis",
            instruction=INSTRUCTION,
            use_search=True,
        )

    def run(self, target_company: str, prior_findings: str = "",
            prior_context: str = "") -> str:
        parts = [f"Target company:\n{target_company}"]
        if prior_context:
            parts.insert(0, f"Context from memory:\n{prior_context}\n")
        if prior_findings:
            parts.append(f"\nPrior phase findings:\n{prior_findings}")
        return super().run("\n\n".join(parts))
