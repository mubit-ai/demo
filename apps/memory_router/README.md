# Mubit learns how to route

Instrument your agent's decisions and outcomes with Mubit. Mubit turns the
experience into reusable lessons that improve subsequent runs.

One router delegates to `billing_agent`, `technical_agent`, or `account_agent`.
The specialists and cases are fictional. The application records what happened;
**Mubit generates and persists the lessons**. Gemini interprets recalled guidance
when choosing a specialist; it does not generate lessons.

```text
application                            Mubit
request → recall(request) ────────────► relevant lesson candidates
          ↓                             │
router reads guidance ◄────────────────┘
          ↓
specialist → handoff/resolution
          ↓
remember(observed evidence) ──────────► facts
record_step_outcome(+1 / -1) ─────────► step outcomes
advanced.reflect(...) ────────────────► generates and persists lessons
                                        ↓
next process → recall(new request) ◄── learned guidance
```

## Run

Python 3.11+, Mubit credentials, and a Gemini key are required. From the repo root:

```sh
python3 -m venv apps/memory_router/.venv
apps/memory_router/.venv/bin/pip install -r apps/memory_router/requirements.txt
# For a new checkout: copy .env.example to .env and fill in the credentials.
# This workspace already has an ignored .env copied from the supply-chain demo.

apps/memory_router/.venv/bin/python apps/memory_router/demo.py teach --experiment fictional-002
apps/memory_router/.venv/bin/python apps/memory_router/demo.py evaluate --experiment fictional-002
```

The CLI loads its own `.env`, independent of the current working directory;
existing environment variables take precedence. `.env` is ignored by Git.
`GEMINI_MODEL` is optional. Dependencies match the adjacent supply-chain demo.
Its existing `.venv/bin/python` can also run these commands without installing again.

Both processes use the persistent Mubit run `memory-router-v2-<experiment>`;
individual executions have unique IDs. Choose a new experiment ID for a cold
start. Reuse it to demonstrate persistence. The v2 namespace excludes the former
application-generated v1 rules. There is no local memory fallback or lesson cache.

## Customer integration points

The `Memory` class in [demo.py](demo.py) contains the instrumentation:

1. `recall(query=request, entry_types=["lesson"], evidence_only=True)` retrieves
   up to three lesson candidates. Working memory and linked runs are disabled;
   the adapter also rejects stale entries and evidence from other runs.
2. `remember(intent="fact", wait=True)` stores the request, decision, and observed
   specialist steps. It never writes `intent="lesson"` or a preferred-route rule.
3. `record_step_outcome()` gives the initial handoff a negative signal and the
   successful resolution a positive signal. No application-authored hindsight
   directive is provided. Reused lessons also receive `record_outcome()` feedback
   based on first-route success, not eventual resolution.
4. `client.advanced.reflect({"run_id": ..., "include_step_outcomes": True})`
   invokes Mubit reflection after the teaching batch. Mubit produces conditional
   natural-language lessons and persists them, returning their IDs and rationales.
   Empty, degraded, or unpersisted reflection fails the teaching command.

The SDK's typed `client.reflect()` does not expose `include_step_outcomes`.
`client.advanced.reflect()` is the documented SDK method for this flag, not a
local reflection implementation:
[Mubit reflection documentation](https://docs.mubit.ai/patterns/reflection-to-lessons).

The router sees only the current request, its baseline route, and retrieved
lesson content/conditions/IDs. Historical outcomes are not passed to the router;
the synthetic environment reveals the current outcome only after the decision.
Mubit may return unrelated candidates; the router is instructed to reject them,
respect conditions, and cite only the lessons it actually applies. An override
without a recalled ID fails validation.

## Evaluation and traceability

The cold policy sends API/endpoint requests to technical, invoice/receipt requests
to billing, and others to account. With no lessons it needs no model call. With
lessons, a small Gemini call can override that same baseline using the learned
guidance. The policy/prompt is fixed across the evaluation.

Three unseen paraphrases test transfer from teaching cases; three negative
controls lack the distinguishing condition. Memory OFF performs no retrieval.
Evaluation never ingests evidence, records reward, or reflects, so test outcomes
cannot teach later test cases. Labels only enter the specialist simulator.

Live SDK-reflection experiment `sdk-reflect-001` on 2026-09-08 produced:

| Metric (6 cases) | Memory OFF | Memory ON |
|---|---:|---:|
| Correct first-route rate | 50% | 83.3% |
| Unnecessary handoffs | 3 | 1 |
| Total specialist steps | 9 | 7 |

Two paraphrased routes improved. The router declined to equate an organisation
name on a receipt with a workspace name on an invoice when given the lesson's
explicit conditions. All three negative controls remained correct. This partial
generalization is retained as measured, rather than tuning against the held-out
case to obtain a perfect score.

[Teaching trace](live-v2-teach.jsonl) shows observed decisions, step outcome IDs,
and Mubit reflection's three generated lessons with their persisted IDs.
[Evaluation trace](live-v2-evaluate.jsonl) shows a separate process recalling those
same IDs and citing them for changed routes. Negative controls cite no lesson.
The older `live-evaluation.jsonl` is a **v1 historical result**, not evidence of
SDK reflection. The CLI reports measured metrics and fails if accuracy does not
improve; LLM-generated results are not guaranteed to repeat exactly.

```sh
apps/memory_router/.venv/bin/python -m unittest discover -s apps/memory_router -p 'test_*.py' -v
```

Offline tests check SDK call order, fact-only ingestion, reward polarity, scope
filtering, decision-context separation, invalid citations, and empty reflection.
They do not simulate lesson extraction or claim to verify live persistence.

Deliberate limits: a small designed dataset, fixed cold policy, synthetic
specialists with a hidden fixture owner, and one teaching example per category.
This is an architectural demonstration, not a statistical benchmark. It needs
no UI, framework, local rule schema, or custom lesson generator.

The size and explicit loop were inspired by the
[Mubit SRE demo](https://github.com/mubit-ai/mubit-sre-demo).
