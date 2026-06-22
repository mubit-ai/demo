"""
Scenario selector — pick one with DEMO_SCENARIO=<name> (default: billing).

    DEMO_SCENARIO=billing   make demo-memory      # SaaS billing support
    DEMO_SCENARIO=fintech   make demo-memory      # fintech / neobank

To add your own, drop a module in scenarios/ exposing the same fields (see
scenarios/billing.py) and set DEMO_SCENARIO to its name.

Scoring (transparent on purpose): an answer is correct only if it surfaces ALL
of EXPECTED_KEYWORDS. A capable model can luck into one or two from general
knowledge, so a partial answer still FAILs — a deliberately strict, auditable
bar built around the company-specific policy the model can't simply guess.
"""
import importlib
import os

_NAME = os.environ.get("DEMO_SCENARIO", "billing").strip().lower() or "billing"
try:
    _m = importlib.import_module("scenarios.%s" % _NAME)
except Exception:  # unknown name -> fall back to billing
    _m = importlib.import_module("scenarios.billing")
    _NAME = "billing"

NAME = _NAME
SCENARIO = _m.SCENARIO
CUSTOMER = _m.CUSTOMER
ROLE_SYSTEM = _m.ROLE_SYSTEM
QUESTION = _m.QUESTION
EXPECTED_KEYWORDS = list(_m.EXPECTED_KEYWORDS)
BASELINE_WRONG = _m.BASELINE_WRONG
CORRECT_POLICY = _m.CORRECT_POLICY
FAILED_TRACE = _m.FAILED_TRACE
LESSON = _m.LESSON
OUTDATED_POLICY = getattr(_m, "OUTDATED_POLICY", "")
DISTRACTORS = list(getattr(_m, "DISTRACTORS", []))
PARAPHRASED_QUESTION = getattr(_m, "PARAPHRASED_QUESTION", QUESTION)


def score_answer(text):
    """Return (fraction_correct, [keywords_found]) for an answer string."""
    t = (text or "").lower()
    found = [k for k in EXPECTED_KEYWORDS if k.lower() in t]
    frac = round(len(found) / len(EXPECTED_KEYWORDS), 2) if EXPECTED_KEYWORDS else 1.0
    return frac, found


def verdict(frac):
    # PASS only on a complete answer (all required elements). A lucky partial
    # from a capable model is still a FAIL — see module docstring.
    return "PASS" if frac >= 0.999 else "FAIL"
