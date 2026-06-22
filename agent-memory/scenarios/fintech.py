"""Scenario: Fintech / neobank support — an unauthorized card transaction (dispute).

The company-specific bits a generic model won't reliably volunteer: a *provisional
credit within 10 business days* and the *Regulation E* basis for the dispute.
"""

SCENARIO = "Fintech / neobank support"
CUSTOMER = "Jordan (cardholder)"
ROLE_SYSTEM = (
    "You are a support agent for a consumer fintech / neobank. "
    "Answer the customer's question directly and concisely (2-3 sentences)."
)

QUESTION = "There's a charge on my debit card I didn't authorize. What happens now?"

# A correct answer must surface ALL of these (company + regulatory policy).
EXPECTED_KEYWORDS = ["provisional credit", "10 business days", "Regulation E"]

# Vague, unhelpful offline baseline — contains none of EXPECTED_KEYWORDS.
BASELINE_WRONG = (
    "I'm so sorry to see that charge! Our team will look into the suspicious "
    "activity on your account and follow up. In the meantime, you can lock your "
    "card from the app to prevent further use."
)

CORRECT_POLICY = (
    "For an unauthorized card transaction: open a dispute in the app within 60 "
    "days, and tell the customer they will receive a provisional credit within "
    "10 business days while we investigate the claim under Regulation E."
)

FAILED_TRACE = (
    "The agent's reply to the unauthorized-charge question was vague reassurance "
    "and did not explain the customer's rights or the formal claim process; a "
    "supervisor flagged it as incorrect and provided the correct policy."
)

LESSON = (
    "When a customer reports an unauthorized card transaction, open a dispute "
    "and tell them they receive a provisional credit within 10 business days "
    "while we investigate under Regulation E."
)

# ── deep-cut (03_distractors.py): semantic ranking vs noise ──────────────────
OUTDATED_POLICY = (
    "Deprecated 2023 policy: unauthorized charges were reversed at the agent's "
    "discretion within 24 hours with no formal dispute filed. (Superseded.)"
)
DISTRACTORS = [
    "To raise your daily ATM withdrawal limit, verify your identity in the app under Card -> Limits.",
    "Direct deposits can post up to two days early when the employer submits the payment file ahead of schedule.",
    "International card purchases incur a 1% FX fee; there are no monthly account fees.",
    "To close an account, withdraw the remaining balance and submit a closure request under Settings.",
    "Mobile check deposits clear in one business day; unusually large checks may be held longer.",
    "Apple Pay / Google Pay: add the card under Wallet; tokenized cards still work while the physical card is locked.",
]
PARAPHRASED_QUESTION = "I see a payment on my card that I never made — how do I get my money back?"
