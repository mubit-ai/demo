"""Report Writer agent — Phase 6: Executive synthesis and recommendation."""

from .base import BaseAgent

INSTRUCTION = (
    "You are the managing director of a technology M&A advisory firm, presenting "
    "the final due diligence report to the investment committee. Synthesize ALL "
    "findings into a decisive executive report:\n\n"
    "1. **Executive Summary** — 3-sentence verdict on the acquisition\n"
    "2. **Go/No-Go Recommendation** — with confidence level and key reasoning\n"
    "3. **Key Strengths** — top 3-5 reasons to acquire\n"
    "4. **Key Risks** — top 3-5 deal-breakers or concerns\n"
    "5. **Valuation Assessment** — is the asking price fair? Recommended bid range\n"
    "6. **Integration Considerations** — technical, cultural, operational\n"
    "7. **Deal Terms Suggestions** — earnout structure, key milestones, protections\n"
    "8. **Post-Acquisition Roadmap** — first 90 days, 6 months, 12 months\n\n"
    "Be decisive and opinionated. The committee needs a clear recommendation, "
    "not a balanced summary. If this was recovered from a crashed pipeline, "
    "note any gaps in the analysis and how they were addressed."
)


class ReportWriter(BaseAgent):
    def __init__(self):
        super().__init__(
            name="report_writer",
            role="executive-synthesis",
            instruction=INSTRUCTION,
        )

    def run(self, all_findings: str, prior_context: str = "") -> str:
        parts = []
        if prior_context:
            parts.append(f"Context from memory (including crash recovery notes):\n{prior_context}\n")
        parts.append(f"Complete due diligence findings:\n{all_findings}")
        return super().run("\n\n".join(parts))
