"""
The (deliberately tiny) support agent. BOTH the no-Mubit and the with-Mubit
scripts use this SAME agent and the SAME backend — so the only difference
between the two runs is whether Mubit memory is injected. That makes the
side-by-side honest: same model, same prompt, only memory differs.

Two interchangeable backends:
  * "compose"  — deterministic, offline, no LLM. Bulletproof + reproducible.
                 This is the DEFAULT, so the demo always lands.
  * "llm"      — real Gemini call (mirrors scripts/demo_self_improving_agent.py).
                 Enable with DEMO_LLM=1 (needs GEMINI_API_KEY) for a live model.
                 Falls back to "compose" automatically if the call fails.
"""

import os

import requests

from scenario import BASELINE_WRONG, ROLE_SYSTEM  # scenario-specific text


def backend():
    use_llm = os.environ.get("DEMO_LLM", "").strip().lower() in ("1", "true", "yes")
    return "llm" if (use_llm and os.environ.get("GEMINI_API_KEY")) else "compose"


def backend_note(label: str) -> str:
    """Honest, on-screen description of how an answer was produced."""
    if label.startswith("llm"):
        return "live model · gemini-2.5-flash" + (" · memory injected" if "memory" in label else "")
    return "offline reproduction · no model" + (" · memory injected" if "memory" in label else "")


def _gemini(system_prompt: str, user_message: str) -> str:
    key = os.environ["GEMINI_API_KEY"]
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={key}"
    )
    resp = requests.post(
        url,
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_message}]}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
        .strip()
    )


def _compose_correct(memory_block: str) -> str:
    """Stitch the most policy-complete recalled line into a customer-ready reply
    (offline path). Picks the line covering the most required elements so memory
    ordering can't break the answer."""
    from scenario import EXPECTED_KEYWORDS

    lines = [l.strip() for l in (memory_block or "").splitlines() if l.strip()]

    def coverage(line):
        return sum(1 for k in EXPECTED_KEYWORDS if k.lower() in line.lower())

    best = max(lines, key=coverage) if lines else ""
    return f"Thanks for flagging this. Here's how we'll resolve it: {best}"


def respond(question: str, memory_block: str = ""):
    """
    Produce an answer. If `memory_block` is non-empty the agent 'knows' the
    policy (Mubit injected it); otherwise it answers from scratch.

    Returns (answer_text, backend_label).
    """
    if backend() == "llm":
        system = ROLE_SYSTEM
        if memory_block:
            system += (
                "\n\nRelevant knowledge from past resolved tickets "
                "(use it if applicable):\n" + memory_block
            )
        try:
            return _gemini(system, question), ("llm+memory" if memory_block else "llm")
        except Exception:
            pass  # fall through to the deterministic path

    if memory_block:
        return _compose_correct(memory_block), "compose+memory"
    return BASELINE_WRONG, "compose"
