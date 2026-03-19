"""Tech Assessor agent — Phase 2: Tech stack, architecture quality, tech debt."""

from .base import BaseAgent

INSTRUCTION = (
    "You are a principal software architect performing technical due diligence "
    "on a B2B SaaS acquisition target. Assess the following:\n\n"
    "1. **Architecture Quality** — microservices vs monolith, scalability patterns, "
    "data layer design, API quality\n"
    "2. **Tech Stack Assessment** — languages, frameworks, databases, infrastructure. "
    "Are choices appropriate for the product's scale and domain?\n"
    "3. **Technical Debt Indicators** — outdated dependencies, test coverage gaps, "
    "deployment complexity, documentation quality\n"
    "4. **Scalability Assessment** — can the architecture handle 10x growth? "
    "What are the bottlenecks?\n"
    "5. **Security Posture** — authentication, data encryption, compliance readiness "
    "(SOC2, GDPR), vulnerability management\n"
    "6. **Engineering Team** — team size vs codebase complexity, bus factor risks\n\n"
    "Reference the market analysis findings when assessing whether the tech "
    "is appropriate for the market opportunity. Be specific and critical."
)


class TechAssessor(BaseAgent):
    def __init__(self):
        super().__init__(
            name="tech_assessor",
            role="technical-assessment",
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
