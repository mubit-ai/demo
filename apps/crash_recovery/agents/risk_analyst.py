"""Risk Analyst agent — Phase 4: Technical, market, regulatory, and team risks."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a risk analyst specializing in technology M&A due diligence. "
    "Based on ALL prior findings (market, technical, competitive), produce a "
    "comprehensive risk assessment:\n\n"
    "1. **Technical Risks** — architecture fragility, key-person dependency, "
    "scalability ceilings, security vulnerabilities, tech debt cost\n"
    "2. **Market Risks** — market contraction, TAM overestimation, regulatory "
    "changes, buyer behavior shifts\n"
    "3. **Competitive Risks** — competitor responses, commoditization, "
    "big tech entry, pricing pressure\n"
    "4. **Execution Risks** — team retention, culture integration, "
    "product roadmap feasibility\n"
    "5. **Financial Risks** — revenue concentration, churn acceleration, "
    "unit economics deterioration\n\n"
    "For each risk, provide:\n"
    "- **Likelihood** (Low/Medium/High)\n"
    "- **Impact** (Low/Medium/High/Critical)\n"
    "- **Mitigation Strategy**\n\n"
    "Present as a structured risk matrix. Be candid — this is a $35M decision."
)


class RiskAnalyst(BaseAgent):
    def __init__(self):
        super().__init__(
            name="risk_analyst",
            role="risk-analysis",
            instruction=INSTRUCTION,
        )

    def run(self, target_company: str, prior_findings: str = "",
            prior_context: str = "") -> str:
        parts = [f"Target company:\n{target_company}"]
        if prior_context:
            parts.insert(0, f"Context from memory:\n{prior_context}\n")
        if prior_findings:
            parts.append(f"\nAll prior phase findings:\n{prior_findings}")
        return super().run("\n\n".join(parts))
