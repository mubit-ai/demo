"""Scenario: SaaS billing support — the double-charge refund policy."""

SCENARIO = "SaaS billing support"
CUSTOMER = "Dana from Acme Corp"
ROLE_SYSTEM = (
    "You are a billing-support agent for a SaaS company. "
    "Answer the customer's question directly and concisely (2-3 sentences)."
)

# The customer's question (asked on both "days").
QUESTION = "I was double-charged on my last invoice. What's the refund process?"

# A correct answer must surface ALL of these (company policy, not common knowledge).
EXPECTED_KEYWORDS = ["5-7 business days", "support ticket", "refund"]

# The vague, unhelpful answer a fresh agent gives offline (deterministic mode).
# Deliberately contains none of EXPECTED_KEYWORDS.
BASELINE_WRONG = (
    "I'm sorry about the mix-up on your invoice! Our team will take a look at "
    "your account and sort out the extra charge. Reply here with your invoice "
    "number and we'll investigate."
)

# What a human supervisor teaches the agent after it fumbles on day 1. Stored
# as a FACT; Mubit's reflection then distills its own LESSON from it.
CORRECT_POLICY = (
    "For a double-charge complaint: file a support ticket on the customer's "
    "behalf, then tell them the refund is processed within 5-7 business days "
    "after the ticket is filed."
)

# Keyword-free note about the failure, stored as a trace so reflection has real
# evidence (a failure + a correction) to reason over.
FAILED_TRACE = (
    "The agent's reply to the double-charge question was generic and did not "
    "give the customer the actual resolution steps; a supervisor flagged it "
    "as incorrect and provided the correct policy."
)

# Offline fallback only (used if server-side reflection is unavailable).
LESSON = (
    "When a customer reports a double charge, always file a support ticket and "
    "tell them the refund is processed within 5-7 business days after the "
    "ticket is filed."
)

# ── deep-cut (03_distractors.py): semantic ranking vs noise ──────────────────
OUTDATED_POLICY = (
    "Deprecated 2024 policy: double-charge refunds were auto-issued as store "
    "credit within 24 hours and no ticket was required. (Superseded.)"
)
DISTRACTORS = [
    "Password resets: send a reset link from Settings -> Security; the link expires in 60 minutes.",
    "To upgrade a plan mid-cycle, prorate the difference and apply it to the next invoice.",
    "API rate limit is 100 requests/second per key; 429 responses include a Retry-After header.",
    "SSO is configured under Admin -> Identity using SAML 2.0 metadata.",
    "Customers download invoice PDFs from Billing -> History via the download icon.",
    "Seat management: add or remove seats under Team; changes are billed on the next cycle.",
]
PARAPHRASED_QUESTION = "A client says they got billed twice — how do we make them whole?"
