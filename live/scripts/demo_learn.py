#!/usr/bin/env python3
"""mubit.learn demo — the easiest setup path.

Shows that adding persistent memory to any LLM app takes ONE LINE:
  mubit.learn.init(api_key=..., agent_id=...)

Everything else — lesson extraction, trace capture, context injection,
reflection — is fully automatic. ZERO manual remember/recall/reflect calls.
"""

import json
import os
import time

from google import genai

import mubit
import mubit.learn

SESSION = f"learn-demo-{int(time.time())}"
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
MUBIT_API_KEY = os.environ["MUBIT_API_KEY"]
MUBIT_ENDPOINT = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")

pp = lambda obj: print(json.dumps(obj, indent=2, default=str))


def pause(msg=""):
    print(f"\n{'─' * 50}\n  {msg}\n{'─' * 50}\n")


# ── STEP 1: Init mubit.learn (ONE LINE) ──────────────────────────────────
print("\n╔══════════════════════════════════════════════════════╗")
print("║  STEP 1: mubit.learn.init() — ONE LINE OF SETUP    ║")
print("╚══════════════════════════════════════════════════════╝\n")

run = mubit.learn.init(
    api_key=MUBIT_API_KEY,
    endpoint=MUBIT_ENDPOINT,
    agent_id="demo-agent",
    session_id=SESSION,
    inject_lessons=True,
    auto_reflect=True,
    auto_extract=True,
    max_token_budget=800,
    cache_ttl_seconds=5,  # short TTL so call 2 fetches fresh context
    fail_open=True,
)
print(f"  Session:  {SESSION}")
print(f"  Endpoint: {MUBIT_ENDPOINT}")
print(f"  LLM:      {GEMINI_MODEL}")
print("\n  mubit.learn initialized.")
print("  All Gemini calls are now auto-instrumented — zero code changes needed.")
print("  On each LLM call, mubit.learn will:")
print("    1. PRE-CALL:  Fetch relevant lessons and inject into prompt")
print("    2. POST-CALL: Capture the interaction + auto-extract rules/lessons/facts")

# Create Gemini client — AUTOMATICALLY wrapped by mubit.learn
llm = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

pause("Step 2: First LLM call — teach the agent something")

# ── STEP 2: First call — agent learns about a production incident ─────────
print("\n╔══════════════════════════════════════════════════════╗")
print("║  STEP 2: LLM call #1 — Agent handles an incident   ║")
print("╚══════════════════════════════════════════════════════╝\n")

CALL_1_PROMPT = (
    "You are a senior SRE investigating a production incident. "
    "A developer rotated the JWT signing key but forgot to flush the Redis token cache first. "
    "As a result, stale tokens signed with the old key were served from Redis for 5 minutes, "
    "causing widespread auth failures.\n\n"
    "Write a concise post-mortem summary. Include:\n"
    "- Root cause\n"
    "- What the fix was\n"
    "- Rules to prevent this in future (use 'always' and 'never' phrasing)\n"
    "Keep it under 200 words."
)

print(f"  Prompt: (SRE post-mortem investigation)\n")
response_1 = llm.models.generate_content(
    model=GEMINI_MODEL,
    contents=CALL_1_PROMPT,
)
answer_1 = response_1.text.strip()
print(f"  Response:\n{answer_1}\n")
print("  ──────────────────────────────────────────────")
print("  mubit.learn just automatically:")
print("    ✓ Captured the full interaction (prompt + response)")
print("    ✓ Auto-extracted rules ('always', 'never', 'must')")
print("    ✓ Auto-extracted lessons ('the fix was', 'caused by')")
print("    ✓ Ingested everything into Mubit — ZERO manual calls")

# Wait for ingestion + embedding
print("\n  Waiting for auto-ingestion + embedding (8s)...")
time.sleep(8)

# Show what was auto-extracted
client_for_query = mubit.Client(
    endpoint=MUBIT_ENDPOINT,
    api_key=MUBIT_API_KEY,
    run_id=SESSION,
)
client_for_query.set_transport("http")

health_1 = client_for_query.memory_health(session_id=SESSION, limit=100)
print(f"  Memory after call #1: {health_1.get('entry_counts', {})}")

pause("Step 3: Ask a DIFFERENT question — lessons from call #1 auto-injected")

# ── STEP 3: Second call — different question, lessons auto-injected ───────
print("\n╔══════════════════════════════════════════════════════╗")
print("║  STEP 3: LLM call #2 — lessons auto-injected       ║")
print("╚══════════════════════════════════════════════════════╝\n")

CALL_2_PROMPT = (
    "I need to rotate our JWT signing keys this weekend. "
    "What steps should I follow to avoid any downtime?"
)

print(f"  Prompt: {CALL_2_PROMPT}\n")
print("  (mubit.learn is auto-fetching lessons from call #1 and injecting...)\n")

# Clear cache so it fetches fresh from Mubit
mubit.learn._lesson_cache.clear()

response_2 = llm.models.generate_content(
    model=GEMINI_MODEL,
    contents=CALL_2_PROMPT,
)
answer_2 = response_2.text.strip()
print(f"  Response (informed by auto-extracted lessons):\n{answer_2}\n")
print("  ──────────────────────────────────────────────")
print("  The LLM's response is now informed by the post-mortem from call #1.")
print("  mubit.learn injected the extracted rules/lessons into the prompt")
print("  AUTOMATICALLY — no code changes, no manual recall().")

pause("Step 4: End run + see everything mubit.learn captured")

# ── STEP 4: Wrap up ──────────────────────────────────────────────────────
print("\n╔══════════════════════════════════════════════════════╗")
print("║  STEP 4: What mubit.learn did automatically         ║")
print("╚══════════════════════════════════════════════════════╝\n")

# End the run — triggers auto-reflection
print("  Ending run (triggers automatic reflection)...")
run.end()
time.sleep(5)

# Show final state
health = client_for_query.memory_health(session_id=SESSION, limit=100)
print(f"\n  Final memory state: {health.get('entry_counts', {})}")

lessons_resp = client_for_query.control.lessons({"run_id": SESSION, "limit": 10})
lesson_list = lessons_resp.get("lessons", [])
if lesson_list:
    print(f"\n  Lessons in memory ({len(lesson_list)}):")
    for l in lesson_list:
        ltype = l.get("lesson_type", "?")
        content = l.get("content", "")[:120]
        print(f"    [{ltype:12s}] {content}...")

print("\n  Finished.")
