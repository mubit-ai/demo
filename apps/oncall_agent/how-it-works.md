# On-Call Triage Agent — How It Works

> Companion to [`how-it-works.html`](how-it-works.html). All services, incidents and causes are fictional.

An on-call agent triages incidents for four fictional services — it runs up to two diagnostic
probes, then chooses one fix. The environment's true causes are hidden; the cold keyword baseline is
wrong for the three teaching classes and right for the controls, so any lift must come from memory.
This app implements the complete documented learning loop
([outcome attribution](https://docs.mubit.ai/patterns/outcome-attribution-loop) ·
[step-level outcomes](https://docs.mubit.ai/recipes/step-level-outcomes) ·
[the learning loop](https://docs.mubit.ai/concepts/learning-loop)):

```
recall() → capture entry IDs → act (probes + fix) → objective verdict
   → record_outcome(entry_ids=…, idempotency_key=…)
   → record_step_outcome() per probe and per fix
   → advanced.reflect(include_step_outcomes=True)
```

## Example data at each step

### Recall, and capture the IDs behind the decision

```text
in:  recall(query="On-call triage lessons for: Checkout failures jumped when we shipped
            this morning's change.", entry_types=["lesson"], evidence_only=True)
out: [{"id": "f00d…", "content": "Checkout deploy-day error spikes are dependency flaps;
        restart the dependency instead of rolling back", "conditions": []}]
```

### Act — the plan cites what it uses (grounding enforced in code)

```json
{"probes": ["dependency_health"], "fix": "restart_dependency",
 "lesson_ids": ["f00d…"], "reason": "Lesson matches checkout deploy-day error spikes",
 "baseline_fix": "rollback"}
// an override that cites an ID that was never supplied fails the run
```

### Objective verdict — the environment, never the model, decides

```json
{"step": "probe/0", "name": "dependency_health", "ok": true}      // informative
{"step": "fix",     "name": "restart_dependency", "ok": true}     // incident resolved
```

### Dense per-step process rewards

```python
client.record_step_outcome(
    step_id="exec/T1/probe/0", step_name="dependency_health",
    outcome="success", signal=1.0,
    rationale='{"incident": "Error rate spiked on checkout…", "observed_step": …}',
    directive_hint=None)                    # only uninformative steps get a hint,
                                           # and hints are observational — they never
                                           # name the hidden correct fix or probe
```

### Attribute the verdict to exactly the entries used

```python
client.record_outcome(
    reference_id="f00d…",                   # the entry the decision hinged on
    outcome="success", signal=1.0,
    rationale="Synthetic exec/T1: fix restart_dependency resolved the incident",
    entry_ids=["f00d…"],                    # multi-entry attribution: every contributor
    idempotency_key="oncall-agent-v1:exec/T1",   # a retried call never double-counts
    verified_in_production=False)           # synthetic environment, honestly not production
```

### Let Mubit decide the lessons

```python
client.advanced.reflect({"run_id": run, "include_linked_runs": False,
                         "include_step_outcomes": True})
# (the typed reflect() helper has no include_step_outcomes flag — raw passthrough per the docs)
```

The app stores raw facts only (`remember(intent="fact")`); Mubit's reflection proposes the lessons
and the validation gate decides which exist. A lesson that keeps failing decays through its outcome
counters and returns marked `is_stale` — filtered before the model sees it.

## Teaching runs the loop twice

1. **Observe** — teaching incidents run under the cold policy; every attempt is stored as raw
   evidence with per-step outcomes; `reflect()` produces the lessons.
2. **Apply + attribute** — the same incidents run again; now recall returns lessons, the plan cites
   them, the environment delivers the verdict, and `record_outcome` credits the cited entries.

Evaluation compares Memory OFF vs ON on six held-out incidents: paraphrases of the taught classes
(H1–H3) plus controls where the baseline is already right (C1–C3). Resolution must improve or the
run fails explicitly.

## What this demonstrates

- The docs' outcome-attribution loop running verbatim: capture IDs → act → objective verdict → credit exactly those entries.
- Multi-entry attribution and idempotent outcomes as first-class citizens.
- Step-level process rewards with observational `directive_hint`s (never leaking hidden truth).
- Mubit-decided lessons (reflect + gate), `is_stale` filtering at recall, grounded overrides only.

## Run it

```sh
make oncall-agent                                    # teach + evaluate, fresh experiment
.venv/bin/python demo.py teach --experiment oncall-x
.venv/bin/python -m unittest test_demo               # offline checks (6/6)
```

Part of the Mubit demo suite: [Care Coordination](../care_coordination_agent/how-it-works.md) ·
[Supply Chain](../supply_chain_agent/how-it-works.md) · [Memory Router](../memory_router/how-it-works.md) ·
[Policy Analyst](../policy_analyst/how-it-works.md) ·
benchmark evidence: [mubit-cl-bench](https://github.com/mubit-ai/mubit-cl-bench).
