"""Financial Modeler agent — Phase 5: Revenue projections, valuation signals."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a financial analyst specializing in SaaS company valuation for M&A. "
    "Based on ALL prior findings, produce a financial model and valuation analysis:\n\n"
    "1. **Revenue Analysis** — ARR breakdown (new vs expansion vs churned), "
    "growth rate trajectory, net revenue retention\n"
    "2. **Unit Economics** — CAC, LTV, LTV/CAC ratio, payback period, "
    "gross margin analysis\n"
    "3. **Cost Structure** — R&D spend ratio, S&M efficiency, G&A overhead, "
    "path to profitability\n"
    "4. **Revenue Projections** — 3-year forecast with bull/base/bear scenarios. "
    "Factor in market growth, competitive dynamics, and risk factors\n"
    "5. **Valuation Analysis** — compare against:\n"
    "   - Revenue multiples (ARR-based)\n"
    "   - Comparable transactions\n"
    "   - DCF with WACC assumptions\n"
    "6. **Deal Assessment** — is the $35M asking price justified? "
    "What should the bid range be?\n\n"
    "Be quantitative. Show your assumptions and calculations."
)


class FinancialModeler(BaseAgent):
    def __init__(self):
        super().__init__(
            name="financial_modeler",
            role="financial-modeling",
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
