# Memory-augmented router: minimal proof

One deterministic router delegates to `billing_agent`, `technical_agent`, or
`account_agent`. All requests and outcomes are fictional. No LLM, UI, or agent
framework is required. Runtime lessons live only in Mubit.

```text
request → Mubit lesson retrieval → router → specialist → outcome
                                           ↓ handoff      ↓
                                      resolving agent → reflection → Mubit
```

## Run across two processes

Python 3.11+ and a running Mubit endpoint/key are required. From the repo root:

```sh
python3 -m venv apps/memory_router/.venv
apps/memory_router/.venv/bin/pip install -r apps/memory_router/requirements.txt
export MUBIT_ENDPOINT='https://your-mubit-endpoint'
export MUBIT_API_KEY='your-key'

# Use a fresh experiment ID for clean teaching; retain it for evaluation.
apps/memory_router/.venv/bin/python apps/memory_router/demo.py teach --experiment fictional-001
apps/memory_router/.venv/bin/python apps/memory_router/demo.py evaluate --experiment fictional-001
```

The two commands share a persistent Mubit run scoped by experiment ID. Each
process also has a unique execution ID. They do not share Python state or a local
lesson file. Reusing an already taught experiment intentionally produces warm
routes. Choose a new ID to start clean; nothing is deleted.

The local requirements pin the same SDK as the adjacent supply-chain demo;
the root lockfile's older SDK is not used. Missing configuration fails explicitly.
Failed writes or lessons that cannot be retrieved stop teaching rather than
silently claiming success. A successful live `teach` followed by `evaluate` is
the persistence acceptance check; the offline test is not a substitute.

## What learns

Initially, API problems go to technical, invoice problems to billing, and other
requests to account. The router extracts a small, fixed vocabulary of observable
characteristics. This vocabulary contains no corrected destinations.

For example, technical hands an API-after-renewal case to billing, which resolves
it. Reflection constructs `when=[api, renewal], prefer=billing_agent,
avoid=technical_agent` from that observed outcome. It stores the rule plus the
supporting decision/outcome through `remember(intent="lesson", wait=True)`.
There are no pre-seeded routing lessons.

Later, “Our endpoint stopped working when the subscription rolled over” retrieves
that conditional lesson and routes straight to billing. An ordinary API parser
error lacks the renewal characteristic, so the lesson does not apply.

Mubit retrieval requests only lesson evidence, with working memory and linked
runs disabled. The adapter validates the experiment marker and rule shape, rejects
stale entries, and checks applicability before supplying rules to the router.
Historical request text and outcomes remain in stored evidence, outside the
router's decision input. Conflicting destinations cause baseline fallback.

Each JSONL decision prints its baseline, chosen specialist, applicable rules,
Mubit lesson IDs, and originating execution/case. The subsequent outcome records
initial-route success, failure, or handoff, every specialist step, and resolution.
During teaching, a reused lesson receives `record_outcome` feedback based on
**first-route success**, not eventual resolution. Failed unresolved cases do not
invent a preferred destination. Evaluation makes no memory writes.

## Controlled evaluation

Both arms use the same fixed router and six unseen requests. Three are paraphrases
of teaching categories; three are negative controls missing the learned condition.
Memory OFF performs no retrieval. Memory ON retrieves relevant lessons per request.
Evaluation labels enter only the synthetic environment after routing.

Expected results when all three teaching lessons are retrieved:

| Metric (6 cases) | Memory OFF | Memory ON |
|---|---:|---:|
| Correct first-route rate | 50% | 100% |
| Unnecessary handoffs | 3 | 0 |
| Total specialist steps | 9 | 6 |

Live verification on 2026-09-08 measured these exact results using experiment
`live-dc297582d10f`. A separate evaluation process retrieved all three persisted
Mubit lesson IDs, changed all three paraphrased routes, and left the negative
controls unchanged. See [live-evaluation.jsonl](live-evaluation.jsonl) for the
credential-free trace. Teaching persisted its lessons before an unsupported SDK
`close()` call caused a cleanup error; that call has been removed, and the separate
evaluation completed successfully.

The CLI prints measured results and exits unsuccessfully if first-route rate does
not improve. These expected numbers are also verified with an offline test double:

```sh
python3 -m unittest discover -s apps/memory_router -p 'test_*.py' -v
```

The test checks relevant retrieval, cross-experiment isolation, a fresh client's
read of persisted test data, held-out route changes, unchanged negative controls,
read-only evaluation, conflicting lessons, failed outcomes, and lost writes.
Its disk-backed fake exists only in the test file and does not verify Mubit.

This proves conditional policy transfer within a designed vocabulary, not broad
semantic generalization or statistical reliability. Specialists use a hidden
fixture owner to simulate diagnosis/handoff; reflection trusts one resolved
example. Add an LLM or repeated-evidence threshold only when testing those claims.

Inspired by the small explicit loop in the
[Mubit SRE demo](https://github.com/mubit-ai/mubit-sre-demo), without its fleet or UI.
