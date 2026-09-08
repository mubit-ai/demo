# Memory-Augmented Router

One router delegates to three fictional specialists. Mubit learns from failed
routes and successful handoffs, then supplies lessons for future requests.

```text
                     ┌── billing_agent
request → router ────├── technical_agent
          ↑          └── account_agent
          │                 │
       recall            outcomes
          │                 ↓
          └──── Mubit ← reflect
```

## Running

Requires Python 3.11+, `uv`, and `apps/memory_router/.env` with
`MUBIT_ENDPOINT`, `MUBIT_API_KEY`, and `GEMINI_API_KEY`.
Use `.env.example` for a new checkout; this workspace is already configured.

From the repo root:

```bash
make memory-router
```

Dependencies are handled by `uv`. Each invocation starts a fresh experiment and
runs both phases automatically in separate processes:

- **Run 1:** Route three requests, store observed evidence with `remember()`,
  record step outcomes, and invoke Mubit SDK reflection to generate lessons.
- **Run 2:** Retrieve those persistent lessons and compare Memory OFF vs ON on
  six new requests, including paraphrases and negative controls. No learning
  occurs during evaluation.

Output includes routing decisions, cited lesson IDs, first-route accuracy,
handoffs, and resolution steps. Gemini applies guidance; Mubit generates it via
`advanced.reflect()` with `include_step_outcomes` enabled.

The recorded live run improved first-route accuracy from **50% to 83.3%** and
reduced handoffs from **3 to 1**. Results can vary.
[Teaching trace](live-v2-teach.jsonl) · [Evaluation trace](live-v2-evaluate.jsonl)
