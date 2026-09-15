# Care Coordination — operational memory lab

One synthetic patient, one deterministic encounter simulator, one bounded LLM
agent that acts through validated structured actions, real Mubit persistent
learning across fresh executions, a controlled three-arm behavioral
comparison, and a one-page browser demo. The claim under test is not "the
agent remembers patient facts" but "the agent learns patient-specific
operational strategies from previous outcomes and clinician corrections, and
those lessons change its policy in later encounters — while current chart
instructions always outrank historical memory."

**All patients, clinicians, charts, medications, and clinical statements are
synthetic. No healthcare system is connected. The demo performs care
coordination only — no diagnosis, treatment selection, or medication
changes.** LLM runs are temperature-0 but not guaranteed deterministic;
verdicts are single-sample behavioral evidence, and ties and regressions are
reported exactly as measured.

## What the demo proves

1. A fresh agent starts with no knowledge of what works for this patient.
2. Teaching encounters T1 (contact), T2 (communication), T3 (coordination
   sequence) turn observed outcomes and one scripted clinician correction
   into evidence-backed Mubit lessons (persisted only when the evidence
   actually occurred).
3. Lessons survive process restarts — Mubit is the only durable memory; the
   `.demo/` trace files are audit artifacts.
4. Completely fresh executions under no-memory / full-memory /
   contact-lesson-ablated arms behave differently (E1 combined coordination).
5. A current chart instruction that conflicts with a stored lesson wins
   (E2 current-preference override).

## Architecture

- `scenarios.py` — synthetic charts, teaching + evaluation scenarios, hidden patient model, scripted clinician correction.
- `simulator.py` — deterministic encounter environment, action validation, 8-action budget, completion semantics.
- `agent.py` — Gemini provider (sibling pattern), structured decisions, bounded retry loop, memory modes (none/full/frozen).
- `memory.py` — real Mubit client wrapper, lesson schema, normalization, frozen-snapshot hashing.
- `lessons.py` — deterministic, evidence-grounded lesson derivation and validation.
- `compare.py` — three-arm controlled comparison harness, deterministic scoring, pairwise verdicts.
- `trace.py` — structured, agent-visible event records.
- `app.py` + `index.html` — local FastAPI server (SSE, downloads, one active run) and the single-page demo.
- `run_demo.py`, `run_agent.py`, `test_demo.py`, `test_api.cjs` — deterministic smoke, live CLI, offline checks.

State separation: current chart facts | visible encounter state | observed
outcomes/corrections | learned Mubit lessons are kept strictly apart; lessons
are operational guidance, never a copy of the synthetic EHR, and prompts
receive only the chart, visible state, action catalog, this encounter's
outcomes, and (per arm) the frozen lesson snapshot.

## Prerequisites

- Python 3.11+; `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- A Gemini API key (`GEMINI_API_KEY`; optional `GEMINI_MODEL`, default `gemini-3.6-flash`).
- A running Mubit instance: this repository's standard local development
  setup is `MUBIT_ENDPOINT=http://127.0.0.1:3000` with the repository's
  local development API key (see the root `test_*.py` scripts, e.g.
  `mbt_local_admin_secret`). Hosted `api.mubit.ai` credentials also work
  when valid.
- Configure via `.env` (copy `.env.example`). Credentials are never committed,
  logged, or sent to the browser; the UI shows only a sanitized endpoint.

## Run the browser demo

```sh
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7880
# open http://127.0.0.1:7880
```

The page is the whole story: status bar (experiment, model, sanitized Mubit
endpoint/health, lesson count) → **New Experiment** (fresh `cc-demo-…`
namespace; nothing is ever deleted) → **Run teaching encounters** (T1→T2→T3
cards with action/outcome timelines, corrections, and real Mubit reference
IDs that appear only after successful writes) → **What Mubit learned**
(lesson cards clearly labeled as learned operational lessons, not EHR facts)
→ **Compare fresh runs** (E1 three-arm cards with behavioral metrics,
win/tie/regression verdicts with deterministic reasons, and the ablation
explanation; E2 override section with the current patient request) →
downloads for the teaching trace and comparison report (partial artifacts
stay downloadable after failures).

One execution may be active at a time; overlapping requests get a 409.
Provider failures show "Gemini provider error — execution stopped."; Mubit
failures show "Mubit memory unavailable — no fallback memory was used." and
never degrade into fake memory. Repetitions selectable as 1 or 3.

## CLI alternatives

```sh
.venv/bin/python run_demo.py                                  # deterministic intended paths (no LLM)
.venv/bin/python run_agent.py --scenario T1 --mode teaching --memory none --experiment cc-x
.venv/bin/python run_agent.py --scenario T1 --mode read_only --memory full --experiment cc-x
.venv/bin/python run_agent.py --inspect-memory --experiment cc-x
.venv/bin/python compare.py --experiment cc-x [--repetitions 3]
```

Artifacts: per-execution traces under `.demo/runs/<execution_id>.json`,
comparison reports under `.demo/comparisons/`. These are audit evidence
only — memory lives exclusively in Mubit.

## Checks

```sh
.venv/bin/python -m unittest -v test_demo   # offline: fake provider/memory seams, server API tests
node --test test_api.cjs                    # page helper error handling (no browser needed)
```


All patients, clinicians, charts, medications, appointments, and clinical
statements are synthetic and fixed in code. This demo performs care
coordination only: no diagnosis, treatment selection, medication changes, or
medical recommendations, and the agent can never author or alter clinical
wording — it may explain only chart-approved texts by `text_id`.


## Reference detail
## State separation

| Layer | Contents | Where |
| --- | --- | --- |
| Current chart state | Synthetic, agent-visible facts (patient, instructions, approved education texts, outreach window, follow-up slots, contact status) | `scenarios.py` chart dicts; `Encounter.chart()` |
| Hidden simulator state | Patient-specific ground truth (contact behavior, comprehension behavior, sequencing rule) | `HIDDEN_PATIENT_MODEL`; `Encounter.debug_hidden_state()` is TEST/DEBUG ONLY and is not referenced by any agent-facing code path |
| Observed encounter outcomes | Action history, statuses, observations, flags, completion state | `Encounter.visible_state()` |
| Agent-loop records | Prompts, raw responses, parsed decisions, validation results, retries, usage | `agent.run_encounter()` trace events |
| Learned persistent lessons | Experience-derived operational guidance (contact/communication/coordination), grounded in observed outcomes or the scripted clinician correction; never chart facts | `memory.py` + Mubit; provenance and evidence in every record |

The simulator exposes only the chart, the action catalog, and outcomes of
actions taken during the current encounter. Invalid actions raise
`ActionError` and change nothing; they are never silently normalized.

## Teaching encounters

Each encounter teaches exactly one behavior; later evaluation encounters will
combine them from a fresh start.

| Id | Scenario | Starts with | Deterministic behavior (hidden, patient-specific) |
| --- | --- | --- | --- |
| T1 | Contact strategy | no contact | SMS receives no reply; calls before 17:00 go to voicemail; a call at 17:00 or later establishes contact. Not a universal "SMS never works" rule. |
| T2 | Communication strategy | contact established (chart note) | The generic label-style explanation never establishes understanding. A failed comprehension check triggers a scripted clinician correction that adds fixed approved morning/evening wording to the chart; only that text followed by a comprehension check confirms understanding. |
| T3 | Coordination sequence | contact established (chart note) | Booking the follow-up before the ride is arranged returns an unsuccessful outcome and cannot be closed as success. Arranging transportation first, then booking, succeeds. |

Intended paths leave budget slack: T1 4/8, T2 5/8, T3 4/8 executed actions.
Each encounter carries `experiment_id` (`cc-…`), `execution_id`,
`encounter_id`, and the synthetic `patient_id`; scenario state is deep-copied
per run so fresh executions start from identical conditions. The action
budget is 8 executed actions; exhaustion or ending without a valid
`close_encounter` reports `incomplete`, never success. `escalate` hands off
to the on-call coordinator instead.

## Agent loop (milestone 2)

One agent; no subagents, no planner/reviewer hierarchy, no memory. Each step
the model receives exactly: `chart`, `visible_state` (including this
encounter's action history and observed outcomes), `available_actions`,
`step`, `attempt`, and `attempt_failures` — never hidden simulator state,
never future clinician corrections, never expected solutions. It returns one
structured decision (`action`, `arguments`, `rationale`,
`recalled_lesson_ids`, which must be empty until Mubit recall exists).

Validation chain per decision: pydantic schema (enum action, string
arguments, bounded rationale) → argument-shape check (exact allowed keys) →
simulator validation. Malformed output, wrong arguments, and simulator
rejections are retried up to `MAX_STEP_ATTEMPTS = 3` per step with the
failure reasons fed back; exhaustion fails the run explicitly. Provider
failures fail immediately. Nothing fabricates success: a run ends
`completed`, `escalated`, `incomplete`, or `failed`.

## Memory lifecycle (milestone 3)

`memory.py` wraps the real Mubit SDK exactly as `apps/supply_chain_agent`
does: one run per experiment (`run_id = <experiment>-cc-memory-1`),
`remember(wait=True)` with an upsert key per patient+category, recall
restricted to lesson evidence, and cross-experiment isolation enforced by a
content marker plus strict re-validation. The real reference ID always comes
back from Mubit via recall — never fabricated. Failures raise `MemoryError`
and fail the run explicitly; there is no local JSON fallback, and trace files
are audit evidence only.

Lessons are derived deterministically (`lessons.py`) from evidence the agent
was allowed to observe — the runtime model never authors lesson text:

- **contact_strategy** (observed_outcome): requires an SMS `no_response`
  followed by a successful phone call; guidance stays conditional and
  patient-specific ("when no newer contact preference is available…"), never
  "SMS never works".
- **communication_strategy** (clinician_correction): requires a failed
  comprehension check, the scripted correction adding approved
  morning/evening wording, an explanation of that text, and a subsequent
  confirmed check.
- **coordination_sequence** (observed_outcome): requires an unsuccessful
  booking, transportation then arranged, and a subsequent successful booking.

A candidate is persisted only if it is identical to one freshly derived from
the finished encounter, carries matching provenance, contains no
treatment-like language, and carries no hidden-simulator markers. Modes:
`--mode teaching` (may persist validated lessons; records outcomes for cited
lessons) and `--mode read_only` (zero writes); `--memory none|full` (full
recalls once before action 1, normalizes and sorts deterministically, freezes
the snapshot with a SHA-256 hash for the whole execution, and restricts
`recalled_lesson_ids` to actually-recalled IDs). The system policy fixes the
order of authority: current chart/current explicit instructions → current
encounter observations → recalled historical lessons.

## Controlled comparison (milestone 4)

`compare.py` tests the actual product claim. For each evaluation scenario it
performs **one** real Mubit recall, freezes and hashes the normalized base
snapshot, and derives three read-only arms from it: `no_memory` (empty),
`full_memory` (the exact snapshot), and `ablated_contact` (only
`contact_strategy` lessons removed; communication/coordination lessons stay
structurally identical). Every arm runs completely fresh executions with
hash-verified identical starting simulator state, the same provider settings,
and zero Mubit writes; `--repetitions N` reuses the same frozen snapshot and
starting state per repetition.

**E1 — combined coordination:** a fresh encounter (new appointment details,
full objective chain: contact → current-chart approved explanation →
comprehension → transportation → booking → close) where the previously
learned operational patterns still hold. **E2 — current preference
override:** the current chart carries an explicit patient request (morning
calls, 09:00–11:00) that conflicts with the historical after-17:00 contact
lesson, and the simulator rewards only the current instruction — memory must
not override current truth.

Scoring is deterministic (no LLM judge): completion, failed outreach,
comprehension, premature booking, transport-confirmed booking, premature
closes, actions, retries, usage, plus E2 instruction-following and
historical-conflict metrics. Rubrics are ordered vectors — efficiency never
outranks coordination success — producing win/tie/regression for
full-vs-none and full-vs-ablated, and PASS/FAIL for the E2 override.
Citations are audit evidence of claimed influence and never affect scoring.
The comparison fails explicitly (status `invalid`) when recall fails,
required lesson categories are missing, starting-state hashes drift, or
provider settings differ between arms.

```sh
.venv/bin/python compare.py --experiment cc-your-id                 # 1 repetition
.venv/bin/python compare.py --experiment cc-your-id --repetitions 3 # variance check
```

Reports are written to `.demo/comparisons/` as JSON (experiment metadata,
frozen base hash, arm definitions and hashes, ablated real reference IDs,
per-run results and metrics, pairwise verdicts with reasons, interpretation
notes; no secrets, no hidden simulator state).

## Actions

`outreach(channel, local_time)` · `explain_instructions(text_id)` ·
`check_understanding()` · `arrange_transportation()` ·
`book_follow_up(slot)` · `close_encounter()` · `escalate`

Arguments are validated against chart-listed channels, education text ids,
and slots. Free-text explanations, unknown names, malformed times, and
out-of-window or premature actions are rejected explicitly.
