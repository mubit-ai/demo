# Care Coordination Agent — How It Works

> Companion to [`how-it-works.html`](how-it-works.html) (same content, with the full example payloads).
> All patients, clinicians, charts, medications and statements are synthetic. Care coordination only —
> no diagnosis, treatment selection, or medication changes.

One synthetic patient (`SYNTH-PT-0042`), one deterministic encounter simulator, one bounded LLM agent
acting through validated structured actions. Across fresh process restarts it learns patient-specific
operational strategies from observed outcomes and one scripted clinician correction — while current chart
instructions always outrank historical memory.

- **Pathway:** deterministic — the harness calls the Mubit SDK at fixed code points; lesson text is derived in code, never by the runtime model.
- **Mubit surface:** `remember` · `recall` · `record_outcome`.

## The scenario

Seven validated actions, 8-action budget: `outreach(channel, local_time)` ·
`explain_instructions(text_id)` · `check_understanding()` · `arrange_transportation()` ·
`book_follow_up(slot)` · `close_encounter()` · `escalate`. Outreach window 08:00–20:00, channels
`sms | phone_call`, follow-up slots `2026-10-21T09:00 / 11:30 / 14:00`.

| Teaching encounter | Hidden, patient-specific ground truth (never shown to the agent) |
|---|---|
| T1 · Contact | SMS gets no reply; calls before 17:00 go to voicemail; a call at ≥17:00 establishes contact. Deliberately *not* a universal "SMS never works" rule. |
| T2 · Communication | The generic label-style text never establishes understanding. A failed comprehension check triggers a scripted clinician correction that adds approved morning/evening wording to the chart; only that text plus a check confirms understanding. |
| T3 · Coordination | Booking before transportation is arranged fails. Transport first, then booking, succeeds. |

## The loop, with example data at each step

Every lesson becomes a hypervector in Mubit's medium; recall is the SDM consensus read over it.

### ① Capture — agent decision in, simulator outcome out

```json
// agent → simulator (pydantic-validated structured decision)
{"action": "outreach", "arguments": {"channel": "sms", "local_time": "09:00"},
 "rationale": "Chart lists sms and phone_call; try the lighter channel first.",
 "recalled_lesson_ids": []}

// simulator → observed outcome (the only ground truth the agent ever sees)
{"action": "outreach", "channel": "sms", "local_time": "09:00", "status": "no_response"}
```

Later in T1: `{"action": "outreach", "channel": "phone_call", "local_time": "17:05", "status": "contact"}`.

### ② Distill — `lessons.py` derives, only from evidence that occurred

```json
{"category": "contact_strategy",
 "patient_id": "SYNTH-PT-0042",
 "applicability": "when no newer contact preference is available for this patient",
 "guidance": "SMS receives no reply; a phone call at 17:00 or later establishes contact",
 "evidence_type": "observed_outcome",
 "evidence_summary": "T1: outreach(sms,09:00)→no_response; outreach(phone_call,17:05)→contact"}
```

A `communication_strategy` lesson equivalently requires: failed comprehension check → scripted clinician
correction (approved text added to chart) → explanation of that text → subsequent confirmed check.

### ③ Gate — what gets discarded

A candidate is persisted only if it re-derives identically from the finished encounter, carries matching
provenance, contains **no treatment-like language** ("switch the evening dose" → dropped) and no
hidden-simulator markers. The runtime model cannot author lesson text at any point.

### ④ Promote — the idempotent write

```python
client.remember(
    content = "[experiment:cc-x] [demo:cc-memory-1]\n" + json.dumps({
        "experiment_id": "cc-x", "patient_id": "SYNTH-PT-0042",
        "source_encounter_id": "T1", "source_execution_id": "…",
        "upsert_key": "cc-memory-1:cc-x:SYNTH-PT-0042:contact_strategy",
        "lesson": {…the validated record above…}}),
    intent="lesson",
    lesson_type="failure",          # learned from a failed attempt, not a correction
    lesson_scope="run", lesson_importance="high",
    agent_id="care-coordination-agent",
    upsert_key="cc-memory-1:cc-x:SYNTH-PT-0042:contact_strategy",   # re-teaching updates this slot
    wait=True, timeout_ms=60000,
    metadata={"experiment": "cc-x", "patient_id": "SYNTH-PT-0042",
              "category": "contact_strategy", "evidence_type": "observed_outcome", …})
```

The real reference ID comes back from Mubit via recall — never fabricated.

### ⑤ Inject — recall once before action 1, frozen and hashed

```text
in:  recall(query="care coordination operational lessons for synthetic patient SYNTH-PT-0042",
            limit=10, entry_types=["lesson"], evidence_only=True, prefer_current_run=True)
out: evidence entry {"id": "5d3f…", "content": "[experiment:cc-x] [demo:cc-memory-1]\n{…}"}

after strict re-validation (marker / experiment / patient / category / schema),
the model receives only prompt-safe fields, frozen for the whole execution:
[{"id": "5d3f…", "category": "contact_strategy",
  "applicability": "when no newer contact preference is available…",
  "guidance": "SMS receives no reply; a call at 17:00 or later…",
  "evidence_summary": "T1: outreach(sms,09:00)→no_response; …"}]
snapshot SHA-256: identical across all three comparison arms
```

Prompt policy fixes the authority order: **current chart → current observations → recalled lessons.**

### ⑥ Attribute — reinforce only what was cited

```python
memory.record(reference_id="5d3f…", succeeded=True,
              rationale="E1: contact established on first call after 17:00")
# → client.record_outcome(reference_id="5d3f…", outcome="success", signal=1.0,
#                         verified_in_production=False)
```

## What gets measured

- **E1 — three-arm comparison** (fresh processes, hash-verified identical start, zero writes):
  `no_memory` vs `full_memory` vs `ablated_contact` (only contact lessons removed). Deterministic
  scoring — no LLM judge. Removing one lesson category must reverse exactly the behaviors it drove.
- **E2 — current-preference override:** the chart now says "morning calls, 09:00–11:00" while the stored
  lesson says "call ≥17:00". The simulator rewards only the current instruction; the run reports PASS/FAIL
  on memory not overriding current truth.

## What this demonstrates

- Deterministic pathway: memory auditable by code inspection; the LLM chooses actions, never memory content.
- Over-indexing control: conditional lesson wording, E2 current-truth-wins, ablation-measured influence.
- Durable cross-run learning: lessons survive restarts; Mubit is the only memory (trace files are audit artifacts).
- Influence attribution: reinforcement touches only cited lessons.

## Run it

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # GEMINI_API_KEY, MUBIT_ENDPOINT, MUBIT_API_KEY
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7880   # browser demo
.venv/bin/python compare.py --experiment cc-your-id                # three-arm comparison
.venv/bin/python -m unittest test_demo                              # offline checks
```

Part of the Mubit demo suite: [Supply Chain Agent](../supply_chain_agent/how-it-works.md) ·
[Memory Router](../memory_router/how-it-works.md) · benchmark evidence:
[mubit-cl-bench](https://github.com/mubit-ai/mubit-cl-bench).
