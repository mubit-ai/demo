# How `mubit.learn` Works

## Setup

```python
mubit.learn.init(api_key=..., agent_id=..., session_id=...)
```

This single call wraps all supported LLM clients (OpenAI, Anthropic, Google GenAI, LiteLLM). After this, every LLM call automatically gets memory capabilities.

## What Happens on Each LLM Call

```
┌──────────────────────────────────────┐
│  llm.models.generate_content(...)    │
└──────────────┬───────────────────────┘
               │
      ┌────────▼────────┐
      │   PRE-CALL       │  Fetch relevant lessons from Mubit
      │   Inject into    │  and prepend to the prompt
      │   prompt         │
      └────────┬────────┘
               │
      ┌────────▼────────┐
      │   LLM CALL       │  Normal Gemini/OpenAI/etc call
      └────────┬────────┘
               │
      ┌────────▼────────┐
      │   POST-CALL      │  Capture the full interaction
      │   Auto-extract   │  (prompt + response) and extract
      │   Ingest         │  rules, lessons, facts from the
      │                  │  response — all in the background
      └────────┬────────┘
               │
               ▼
         return response
```

### Pre-call: Lesson Injection

Before the LLM sees your prompt, `mubit.learn`:
1. Extracts the user's query from the prompt
2. Calls Mubit to retrieve relevant lessons, rules, and facts from past interactions
3. Prepends them to the prompt inside `<memory_context>` tags

The LLM now has context from previous runs without you writing any retrieval code.

### Post-call: Auto-capture & Extraction

After the LLM responds, `mubit.learn`:
1. Captures the full interaction (prompt + response) as a trace
2. Scans the response for extractable knowledge — rules ("always", "never"), lessons ("the fix was", "caused by"), and facts
3. Sends everything to Mubit via a background thread (non-blocking)

### On Run End

When `run.end()` is called (or the process exits):
- Mubit's reflection agent analyzes all traces in the session
- It distills higher-order lessons and stores them for future runs

## In the Demo

**Call #1** (post-mortem): No lessons exist yet. The LLM responds naturally. `mubit.learn` auto-captures the response and extracts rules/lessons from it in the background.

**Call #2** (rotation question): `mubit.learn` fetches the rules extracted from call #1 and injects them into the prompt. The LLM response now references those rules — same model, same question style, but the answer is informed by what it "learned" in call #1.

**`run.end()`**: Triggers reflection, distilling everything into reusable lessons for future sessions.

Zero manual `remember()`, `recall()`, or `reflect()` calls.
