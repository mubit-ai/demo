"""Market Analyst agent — Phase 1: TAM/SAM/SOM, market trends, segment dynamics."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a senior market research analyst specializing in B2B SaaS due diligence. "
    "Given a target company description, produce a comprehensive market analysis:\n\n"
    "1. **Total Addressable Market (TAM)** — global market size for the company's category\n"
    "2. **Serviceable Addressable Market (SAM)** — realistic segment the company targets\n"
    "3. **Serviceable Obtainable Market (SOM)** — what the company can realistically capture\n"
    "4. **Growth Trends** — market CAGR, key growth drivers, emerging trends\n"
    "5. **Buyer Behavior** — who buys, how they evaluate, typical deal cycles\n"
    "6. **Regulatory Environment** — data privacy, compliance requirements\n\n"
    "Use real, current data from the web. Cite specific numbers and sources. "
    "Be thorough — this analysis feeds into a $35M acquisition decision."
)


class MarketAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="market_analyst",
            role="market-research",
            instruction=INSTRUCTION,
            use_search=True,
        )

    def run(self, target_company: str, prior_findings: str = "",
            prior_context: str = "") -> str:
        parts = [f"Target company:\n{target_company}"]
        if prior_context:
            parts.insert(0, f"Context from memory:\n{prior_context}\n")
        if prior_findings:
            parts.append(f"\nPrior findings:\n{prior_findings}")
        return super().run("\n\n".join(parts))
