#!/usr/bin/env python3
"""mubit.learn demo — the easiest setup path.

How mubit.learn works:

  On each LLM call, mubit.learn automatically:
  1. PRE-CALL:  Fetches relevant lessons from Mubit and injects them into the prompt
  2. LLM CALL:  Normal Gemini/OpenAI/Anthropic call — unchanged
  3. POST-CALL: Captures the full interaction (prompt + response), extracts rules/lessons/facts
                from the response, and ingests everything into Mubit via a background thread

  On run.end(): Mubit's reflection agent analyzes all traces and distills higher-order lessons

  Result: your LLM gets smarter over time with ZERO code changes beyond init().
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


# ── STEP 1: Init mubit.learn ─────────────────────────────────────────────
# This single call wraps all supported LLM clients (OpenAI, Anthropic,
# Google GenAI, LiteLLM). After this, every LLM call automatically gets
# memory capabilities — lesson injection before the call, trace capture
# and knowledge extraction after the call.
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
    cache_ttl_seconds=5,
    fail_open=True,
)
print(f"  Session:  {SESSION}")
print(f"  Endpoint: {MUBIT_ENDPOINT}")
print(f"  LLM:      {GEMINI_MODEL}")
print("\n  mubit.learn initialized.")
print("  All Gemini calls are now auto-instrumented — zero code changes needed.")

# This client is automatically wrapped by mubit.learn.
# Under the hood, generate_content() is now:
#   1. pre-call:  fetch lessons from Mubit → inject into prompt
#   2. call:      normal Gemini API call
#   3. post-call: capture interaction + extract rules/lessons → ingest to Mubit
llm = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

pause("Step 2: First LLM call — teach the agent something")

# ── STEP 2: First call — no lessons exist yet ─────────────────────────────
# Pre-call: mubit.learn calls Mubit's get_context() but finds nothing — no
#           lessons to inject yet.
# Post-call: mubit.learn captures the full interaction and scans the response
#            for extractable knowledge (rules with "always"/"never", lessons
#            with "the fix was"/"caused by", facts). All extracted items are
#            sent to Mubit via a background thread — non-blocking.
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

# Wait for the background IngestWorker to send items to Mubit
# and for Mubit to embed + index them.
print("\n  Waiting for auto-ingestion + embedding (8s)...")
time.sleep(8)

# Query Mubit to see what was auto-captured
client_for_query = mubit.Client(
    endpoint=MUBIT_ENDPOINT,
    api_key=MUBIT_API_KEY,
    run_id=SESSION,
)
client_for_query.set_transport("http")

health_1 = client_for_query.memory_health(session_id=SESSION, limit=100)
print(f"  Memory after call #1: {health_1.get('entry_counts', {})}")

pause("Step 3: Ask a DIFFERENT question — lessons from call #1 auto-injected")

# ── STEP 3: Second call — lessons auto-injected ──────────────────────────
# Pre-call: mubit.learn calls Mubit's get_context() and finds the rules/lessons
#           extracted from call #1. It prepends them to the prompt inside
#           <memory_context> tags. The LLM now has context it never had before.
# Post-call: same auto-capture + extraction as call #1.
print("\n╔══════════════════════════════════════════════════════╗")
print("║  STEP 3: LLM call #2 — lessons auto-injected       ║")
print("╚══════════════════════════════════════════════════════╝\n")

CALL_2_PROMPT = (
    "I need to rotate our JWT signing keys this weekend. "
    "What steps should I follow to avoid any downtime?"
)

print(f"  Prompt: {CALL_2_PROMPT}\n")
print("  (mubit.learn is auto-fetching lessons from call #1 and injecting...)\n")

# Clear the lesson cache so mubit.learn fetches fresh context from Mubit
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
# run.end() triggers Mubit's reflection agent, which analyzes all traces
# in the session and distills higher-order lessons. These lessons persist
# and will be injected into future sessions automatically.
print("\n╔══════════════════════════════════════════════════════╗")
print("║  STEP 4: What mubit.learn did automatically         ║")
print("╚══════════════════════════════════════════════════════╝\n")

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
