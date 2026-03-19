"""Researcher agent — uses Google Search to find tools in a category."""

from .base import BaseAgent

INSTRUCTIONS = {
    "payments": (
        "You are a payments infrastructure specialist. Search the web for the "
        "top 3-5 payment processing tools suitable for the business described "
        "in the research plan. For each tool, find and report:\n"
        "- Product name and company\n"
        "- Pricing model (transaction fees, monthly costs)\n"
        "- Key features relevant to the business\n"
        "- Integration complexity (SDKs, APIs, time to integrate)\n"
        "- Notable customers or case studies\n\n"
        "Use real, current data from the web. Be specific with pricing numbers."
    ),
    "billing": (
        "You are a billing and revenue operations specialist. Search the web "
        "for the top 3-5 subscription billing and revenue recognition tools "
        "suitable for the business described in the research plan. For each tool:\n"
        "- Product name and company\n"
        "- Pricing (per-subscription, flat fee, percentage-based)\n"
        "- Key features: recurring billing, usage-based billing, dunning, "
        "revenue recognition, tax handling\n"
        "- Integration with payment processors (especially Stripe, Adyen)\n"
        "- Notable customers\n\n"
        "Use real, current data from the web. Be specific."
    ),
    "fraud": (
        "You are a fraud prevention specialist. Search the web for the top "
        "3-5 fraud detection and prevention tools suitable for the business "
        "described in the research plan. For each tool:\n"
        "- Product name and company\n"
        "- Pricing model\n"
        "- Detection capabilities: payment fraud, account takeover, "
        "identity verification, chargeback prevention\n"
        "- Integration approach (API, SDK, built-in to payment processor)\n"
        "- False positive rates or accuracy metrics if available\n\n"
        "Use real, current data from the web. Be specific."
    ),
}


class Researcher(BaseAgent):
    def __init__(self, category: str):
        instruction = INSTRUCTIONS.get(category)
        if not instruction:
            raise ValueError(f"Unknown category: {category}. Choose from: {list(INSTRUCTIONS)}")
        super().__init__(
            name=f"{category}_researcher",
            role=f"{category}-research",
            instruction=instruction,
            use_search=True,
        )
        self.category = category
