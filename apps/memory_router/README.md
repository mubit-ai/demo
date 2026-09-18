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
reduced handoffs from **3 to 1**.
[Teaching trace](live-v2-teach.jsonl) · [Evaluation trace](live-v2-evaluate.jsonl)

## Integration modes

This app demonstrates two of the three Mubit integration pathways; the sibling
apps cover the third:

1. **Deterministic (harness-owned).** `apps/care_coordination_agent` and
   `apps/supply_chain_agent`: the harness calls `recall` / `remember` /
   `record_outcome` at fixed code points, and lesson text is derived in code
   from observed evidence — the runtime model never authors a lesson.
2. **Mubit-decided (this app, default).** The agent stores only raw observed
   evidence (`remember(intent="fact")`) and per-step outcomes; Mubit's
   `reflect()` — with the server-side validation gate — decides which lessons
   are created, which stay `pending`, and which are discarded (`is_stale`,
   filtered at recall).
3. **Model-mediated tool calls (this app, `--mode tools`).** The harness calls
   nothing; the router model itself invokes `mubit_recall` as a function tool,
   deciding whether and what to recall per request, and must ground any
   baseline override in lesson IDs the tool actually returned. Validation is
   identical to the preloaded mode.

```bash
make memory-router                                  # default (preloaded lessons)
.venv/bin/python demo.py evaluate --experiment <id> --mode tools
```

Offline checks cover both modes: `python -m unittest test_demo`
(`test_tools_mode_recalls_then_routes_grounded` verifies the tool round-trip
and rejects ungrounded overrides without a live model).
