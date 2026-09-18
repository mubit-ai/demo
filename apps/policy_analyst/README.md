# Policy Analyst — bi-temporal memory, end to end

> Companion to [`how-it-works.html`](how-it-works.html). All policies, customers,
> and claims are synthetic.

An analyst answers two kinds of questions about synthetic insurance policies:
*What do the terms say today?* and *What did the terms say when the claim was
filed?* The terms change mid-stream — one genuine replacement, one refinement.
A current-state-only memory answers the first class correctly and the second
class wrong, every time. The bi-temporal read answers both exactly.

This demo needs **no LLM**: answers are read off stored facts, so every verdict
is deterministic. It implements three documented patterns in one agent:

| Pattern | This app |
| --- | --- |
| [Bi-temporal memory](https://docs.mubit.ai/patterns/bi-temporal-memory) | Every fact carries `occurrence_time` (when it became true) and a validity window. Current read: plain recall — the server hard-excludes superseded beliefs. History read: history-intent query — the server returns superseded facts as first-class evidence, marked `is_stale`. |
| [Write-time reconciliation](https://docs.mubit.ai/patterns/write-time-reconciliation) | Newer facts are written over the same policy key; the store supersedes or reconciles the old entry, and the old entry stays queryable as history. |
| [Cross-run recall, user partitioning](https://docs.mubit.ai/patterns/cross-run-recall) | One Mubit run per customer plus `user_id` on every write and read; isolation is proven negatively (Blobfax's read never returns Acme's facts). |

## The fixture (hidden truth)

Two customers, one shared policy key that diverges:

| Customer | Key | Valid from | Fact |
| --- | --- | --- | --- |
| Acme | coverage_water | 2026-01-01 → 2026-06-30 | "Water damage is covered up to $50,000." |
| Acme | coverage_water | 2026-07-01 → open | "Water damage is excluded; a separate flood rider is required." |
| Acme | deductible | 2026-01-01 → 2026-04-30 | "The deductible is $500 per claim." |
| Acme | deductible | 2026-05-01 → open | "…waived for preferred vendors." |
| Blobfax | coverage_water | 2026-01-01 → open | "Water damage is covered up to $25,000." |

## Phases (separate processes, one experiment)

1. **setup** — write the 5 facts (`remember` with `occurrence_time`, `user_id`,
   window metadata, upsert key). Verify every fact is retrievable; rewrite any
   that is not (same upsert key — idempotent retry). Emit the store state:
   which entries the server marked `is_stale`.
2. **adjudicate** — close the loop on 2 historical claims with known verdicts:
   cite the fact valid at the filing date, then `record_outcome(entry_ids=…,
   user_id=…, idempotency_key=…)` with the adjudication result.
3. **evaluate** — 6 held-out questions in two arms. The **bi-temporal arm**
   picks the read that matches the question (history-intent recall for as-of,
   plain recall otherwise). The **current-state-only arm** always reads the
   current snapshot — exactly how an overwrite-in-place memory answers.
   No writes during evaluation.

## Example data at each step

**Write** (Acme water, new terms):

```python
client.remember(
    content="[fact:1] Water damage is excluded; a separate flood rider is required.",
    intent="fact", session_id=run_acme, user_id="acme",
    occurrence_time=ts("2026-07-01"),                       # when it became true
    metadata={"policy_key": "coverage_water", "customer": "acme",
              "valid_from": "2026-07-01", "valid_to": None},
    upsert_key="policy-analyst-v1:acme:coverage_water:2026-07-01", wait=True)
# the store then marks the 2026-01-01 entry stale (superseded_by → this entry)
```

**Current read** (plain recall): returns only the live fact — the new terms.

**History read** (history-intent query: "What were the policy terms before the
changes?"): returns both versions; the superseded one arrives marked
`is_stale: true`. The window filter picks the fact valid on 2026-04-10:
"covered up to $50,000" — the answer a current-only memory cannot give.

**Attribute**:

```python
client.record_outcome(reference_id=fact_id, entry_ids=[fact_id],
    outcome="success", signal=1.0, user_id="acme",
    idempotency_key="policy-analyst-v1:claim CLM-1042 …",
    rationale="claim CLM-1042 adjudicated covered under the terms valid on 2026-04-10")
```

## Measured result (committed live traces)

```json
{"bi_temporal": {"correct": 6, "of": 6, "current_class_correct": 3, "as_of_class_correct": 3},
 "current_state_only": {"correct": 3, "of": 6, "current_class_correct": 3, "as_of_class_correct": 0}}
```

Both arms answer every "today" question correctly. Every "when the claim was
filed" question separates them: the current-state-only memory is wrong on all
three, the bi-temporal read exact on all three. Zero cross-customer leaks.

## Run it

```sh
make policy-analyst             # setup → adjudicate → evaluate, fresh experiment
.venv/bin/python demo.py evaluate --experiment <id>   # re-evaluate an experiment
.venv/bin/python -m unittest test_demo                # offline checks (15/15)
```

Needs only a Mubit endpoint and key — no LLM key. Part of the Mubit demo suite:
[Care Coordination](../care_coordination_agent/how-it-works.md) ·
[Supply Chain](../supply_chain_agent/how-it-works.md) ·
[Memory Router](../memory_router/how-it-works.md) ·
[On-Call Triage](../oncall_agent/how-it-works.md) ·
benchmark evidence: [mubit-cl-bench](https://github.com/mubit-ai/mubit-cl-bench).
