# On-Call Triage Agent — the outcome-attribution loop, end to end

One synthetic on-call agent triages incidents for four fictional services
(`checkout`, `search`, `payments`, `email`): it runs up to two diagnostic
probes, then chooses one fix. A deterministic environment hides each incident
class's true cause. The cold keyword baseline is wrong for the three teaching
classes and right for the controls — so any measured lift must come from
memory.

This app exists to implement, in one place, the complete documented loop from
docs.mubit.ai:

| Loop stage (docs) | This app | Call |
| --- | --- | --- |
| Capture entry IDs behind the decision ([outcome-attribution loop](https://docs.mubit.ai/patterns/outcome-attribution-loop)) | `recall` returns lessons; the decision must cite the IDs it used | `client.recall(entry_types=["lesson"], evidence_only=True)` |
| Attribute the verdict to those entries | the environment, not the model, resolves the incident; every cited entry is credited | `record_outcome(reference_id=…, entry_ids=cited, signal=±1.0, idempotency_key=…)` |
| Dense per-step process rewards ([step-level outcomes](https://docs.mubit.ai/recipes/step-level-outcomes)) | one outcome per probe (informative/inconclusive) and one for the fix | `record_step_outcome(step_id, step_name, outcome, signal, directive_hint)` |
| Distill lessons from evidence ([learning loop](https://docs.mubit.ai/concepts/learning-loop)) | the app stores raw facts only; Mubit's reflection decides the lessons | `advanced.reflect({run_id, include_step_outcomes: true})` |

Notes matching the docs exactly: the Python typed `reflect()` helper has no
`include_step_outcomes` kwarg, so the raw passthrough
`client.advanced.reflect({...})` is used (SDK ≥ 0.12.0); `record_outcome`
carries `entry_ids` for multi-entry attribution and an `idempotency_key` so a
retried call never double-counts reinforcement;
`verified_in_production=False` throughout because this environment is
synthetic.

## Teaching runs the loop twice

1. **Observe.** The three teaching incidents run under the cold policy. Each
   attempt is stored as raw evidence (`remember(intent="fact")` — nothing here
   authors a fix preference), each probe and the fix get a step outcome with
   an *observational* `directive_hint` (hints describe what was observed,
   never the hidden correct answer), then `reflect()` closes the loop and
   Mubit produces the lessons.
2. **Apply + attribute.** The same incidents run again in a fresh execution.
   Now recall returns lessons, the LLM plan cites them, the environment
   delivers an objective verdict, and `record_outcome` credits exactly the
   cited entries — the primary `reference_id` plus every contributing ID, with
   an idempotency key per attempt.

Evaluation then compares Memory OFF vs ON over six held-out incidents:
paraphrases of the taught classes (H1–H3) plus three controls where the
baseline is already correct (C1–C3). Grounding is enforced in code: a fix that
differs from the baseline must cite supplied lesson IDs, or the run fails.

## Running

Python 3.11+ and `apps/oncall_agent/.env` with `MUBIT_ENDPOINT`,
`MUBIT_API_KEY`, and `GEMINI_API_KEY` (copy `.env.example`). From the repo
root:

```bash
make oncall-agent
```

Each invocation starts a fresh experiment and runs both phases in separate
processes. CLI equivalent:

```bash
.venv/bin/python demo.py teach --experiment oncall-your-id
.venv/bin/python demo.py evaluate --experiment oncall-your-id
```

Output is JSONL: decisions (with cited lesson IDs), outcomes, evidence/step
writes, reflection results, and OFF/ON metrics (resolved incidents, wasted
probes). Evaluation fails explicitly if memory does not improve resolution —
no result is manufactured.

## Checks

```bash
python -m unittest test_demo   # offline: baseline truth table, instrumentation,
                               # attribution shapes, scope filtering, grounding
```

All services, incidents, and causes are fictional. Nothing pages a human,
touches a production system, or sends email.
