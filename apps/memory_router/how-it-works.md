# Memory-Augmented Router — How It Works

> Companion to [`how-it-works.html`](how-it-works.html) (same content, with the full example payloads).
> All requests, specialists and outcomes are fictional.

A keyword-baseline router delegates support requests to three fictional specialists and keeps getting the
wrong specialist first. The agent stores only raw evidence and per-step outcomes — **Mubit itself**
reflects, gates, and decides which lessons exist, which are discarded, and what gets recalled into future
runs. Recorded live result: first-route accuracy **50% → 83.3%**, handoffs **3 → 1**.

- **Pathways demonstrated:** (B) Mubit-decided lessons — default; (C) model-mediated tool calls —
  `--mode tools`; (A) deterministic harness calls — the sibling apps.
- **Mubit surface:** `remember(intent="fact")` · `record_step_outcome` · `record_outcome` ·
  `advanced.reflect` · `recall`.

## The scenario

A regex baseline routes by keywords: `api|endpoint → technical`, `invoice|receipt → billing`, else
`account`. The held-out set is built to break it in both directions:

| Case | Request | Correct | Baseline says |
|---|---|---|---|
| H1 | "Our endpoint stopped working when the subscription rolled over." | billing | technical ✗ |
| H2 | "Please correct the organisation printed on my receipt." | account | billing ✗ |
| H3 | "All users are unable to sign in this morning." | technical | account ✗ |
| C1 | "API returns a parser error on malformed JSON." | technical | technical ✓ |
| C2 | "Please send a duplicate invoice." | billing | billing ✓ |
| C3 | "I forgot my login password." | account | account ✓ |

Lessons must transfer across paraphrases (H-cases) without capturing the keyword-adjacent negative
controls (C-cases) — C2 shares the word "invoice" with a billing-context lesson, so naive similarity is
actively misleading here.

## The three integration pathways

1. **A · deterministic** (sibling apps): harness recalls before the decision and records outcomes after;
   lesson content authored outside the model.
2. **B · Mubit-decided** (this app, default): agent stores raw facts + step outcomes; Mubit's
   `reflect()` proposes lessons server-side and the **validation gate** (accept ≥ 0.6, reject ≤ 0.25,
   revalidation from realized success rates) decides what becomes a lesson, what stays pending, and what
   is discarded — rejected entries return marked `is_stale` and are filtered at recall.
3. **C · model-mediated tools** (`--mode tools`): the harness calls nothing; the router model invokes
   `mubit_recall` as a function tool — deciding whether to consult memory (skipping is allowed),
   what query to use, and how often (capped at 2). Grounding is enforced in code: an override must cite
   lesson IDs the tool actually returned, or the run fails.

## The teaching loop, with example data at each step

Every lesson becomes a hypervector in Mubit's medium; recall is the SDM consensus read over it.

### ① Capture — teaching case T1, before any lesson exists

```json
{"event": "decision", "case": "T1", "request": "API access stopped immediately after renewal.",
 "initial": "technical_agent", "lesson_ids": []}          // no lessons exist yet
{"event": "outcome", "case": "T1", "initial_status": "handoff",
 "resolved_by": "billing_agent", "handoffs": 1}            // disclosed only after the decision
```

### ② Store raw evidence + step outcomes — real events from `live-v2-teach.jsonl`

Nothing in the write path authors a preferred route, rule, or hindsight directive:

```json
{"event": "evidence_stored", "source": "5edb…/T1", "job_id": "e10e09a4-cac5-4b88-9eda-8aa68bd1063c",
 "record_ids": ["84678ca9-1fe9-4a36-981a-689396ed6655"]}

{"event": "step_recorded", "source": "5edb…/T1", "step": 0,
 "result": {"accepted": true, "step_outcome_id": "50bff3c5-…"}}    // initial route: signal −1.0
{"event": "step_recorded", "source": "5edb…/T1", "step": 1,
 "result": {"accepted": true, "step_outcome_id": "b288499b-…"}}    // handoff resolution: signal +1.0
```

The committed teach trace shows exactly this `[-1, +1]` pattern for all three teaching cases: wrong first
route, resolved after handoff.

### ③ Mubit decides the lessons — verbatim `reflect()` output

One call — `advanced.reflect(include_step_outcomes=True)` — and the server produces (and gates) the
lessons; the app never prompts for them:

```json
{"degraded": false, "lessons_stored": 3, "lessons": [
  {"lesson_id": "4a7687f5-1c91-49fc-a1fa-0659f72a767f", "lesson_type": "success",
   "content": "Requests concerning API access stopping immediately after a renewal should be routed
               directly to the billing agent instead of the technical agent."},
  {"lesson_id": "ee23b165-…", "content": "Requests regarding incorrect workspace names appearing on
               invoices should be routed directly to the account agent instead of the billing agent."},
  {"lesson_id": "c32706e1-…", "content": "Requests about login failures for everyone during a system
               outage should be routed directly to the technical agent instead of the account agent."}]}
```

A lesson that keeps failing decays through its outcome counters and returns marked `is_stale` — filtered
before any model sees it.

### ④–⑤ Recall and decide — held-out H1, memory ON (from `live-v2-evaluate.jsonl`)

```json
{"case": "H1", "request": "Our endpoint stopped working when the subscription rolled over.",
 "baseline": "technical_agent",                          // the keyword said technical
 "initial": "billing_agent",                             // lesson-cited override
 "lesson_ids": ["4a7687f5-1c91-49fc-a1fa-0659f72a767f"],
 "reason": "The request involves an API endpoint failing right after a subscription renewal…"}
→ {"case": "H1", "initial_status": "succeeded"}          // correct on the first route

// the honest counter-example — H2 found no applicable lesson and declined an ungrounded override:
{"case": "H2", "initial": "billing_agent", "lesson_ids": [],
 "reason": "…does not match the available lessons…"}
→ {"case": "H2", "initial_status": "handoff", "resolved_by": "account_agent"}
```

Validation in code (both modes): the initial must be a valid specialist; cited IDs must be a subset of
supplied/returned IDs; an override without citations is rejected as ungrounded.

### ⑥ Attribute — two granularities

`record_outcome` on cited lessons only (a lesson that wasn't used can't be reinforced), and
`record_step_outcome(±1.0)` per step — the dense process signal that reflection consumes above.

### Tool mode — the same recall as a model-chosen function call

```json
// model → harness
{"function_call": {"name": "mubit_recall",
                   "args": {"query": "api stopped working after renewal"}}}

// harness → model (a real recall; the final decision may cite only these IDs)
{"function_response": {"name": "mubit_recall",
                       "response": {"lessons": [
                         {"id": "4a7687f5-…", "content": "Requests concerning API access stopping…",
                          "conditions": []}]}}}
```

Skipping the tool is allowed and correct for the easy controls (C1–C3).

## Measured results (committed traces)

| | Memory OFF | Memory ON |
|---|---:|---:|
| First-route accuracy | 50% | **83.3%** |
| Unnecessary handoffs | 3 | **1** |

Per case (ON): H1 ✓ lesson-cited override · H2 handoff (no applicable lesson, no ungrounded guess) ·
H3 ✓ lesson-cited override · C1–C3 ✓ baseline kept. Fresh process, six held-out requests, zero writes
during evaluation.

## What this demonstrates

- Mubit-decided memory: what to store, what to discard, and what to recall are decided by reflect + validation gate from raw evidence — not by hand-written rules or free-form LLM writes.
- Pattern transfer without capture: paraphrases flip (H1, H3); keyword-adjacent controls stay put (C2).
- Grounded overrides only: invented citations fail the run; `is_stale` entries never reach the model.
- A third integration shape: the model spends a tool call on memory only when it judges it useful.

## Run it

```sh
make memory-router                                            # teach + evaluate, fresh experiment
.venv/bin/python demo.py evaluate --experiment <id> --mode tools   # model-mediated recall
.venv/bin/python -m unittest test_demo                        # offline checks incl. tools mode (5/5)
```

Part of the Mubit demo suite: [Care Coordination Agent](../care_coordination_agent/how-it-works.md) ·
[Supply Chain Agent](../supply_chain_agent/how-it-works.md) · benchmark evidence:
[mubit-cl-bench](https://github.com/mubit-ai/mubit-cl-bench).
