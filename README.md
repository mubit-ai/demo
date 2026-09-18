# Mubit demos

Thirteen small agents that use [Mubit](https://docs.mubit.ai) as their memory.
Together they cover the documented usage surface: the learning loop, outcome
and step attribution, bi-temporal memory, consolidation, checkpoints,
prompt and skill optimization, events, and guardrails. Each demo runs a
teaching phase that stores what happened in Mubit. A later run then makes new
decisions with and without the stored lessons, and reports the difference.

Every demo folder carries a `how-it-works.html` explainer page (with a
Markdown companion) that walks the loop with example data at each step, and a
committed `live-*` trace from a real run.

| Demo | Agent task | What goes into memory | Evaluation |
| --- | --- | --- | --- |
| [`memory_router`](apps/memory_router) | Send a support request to one of three specialists | Observed routes and step outcomes; Mubit reflection writes the routing lessons | Six new requests (three paraphrases, three controls), memory off and on: first-route accuracy, handoffs, resolution steps. Also a Gemini function-calling mode where the model mediates the routing decision |
| [`supply_chain_agent`](apps/supply_chain_agent) | Choose how to recover a component shortage | Operator-confirmed guidance from two incidents | Three new cases, each with no memory, full memory, and the applicable lesson removed: alignment, downtime, spend, tokens |
| [`care_coordination_agent`](apps/care_coordination_agent) | Coordinate one synthetic patient's encounters through validated actions | Code-derived operational lessons from observed outcomes and one clinician correction | Fresh encounters in three arms (no memory, full memory, one lesson category ablated) plus a current-instruction override test |
| [`oncall_agent`](apps/oncall_agent) | Triage incidents: two diagnostic probes, then one fix | Raw attempts and per-step rewards; Mubit reflection writes the triage lessons; verdicts are attributed to the cited entries | Six held-out incidents (paraphrases and controls), memory off and on: resolved, wasted probes |
| [`policy_analyst`](apps/policy_analyst) | Adjudicate claims against versioned policy terms | Facts carry `occurrence_time` and validity windows; per-customer runs | Current and as-of questions over changed terms: the correct clause cited in both modes |
| [`team_pipeline`](apps/team_pipeline) | Three agents review a change through handoffs | Handoff and feedback entries; the reviewer writes team-wide lessons; the reviewer subscribes to coordination events | New changes with and without team lessons: issues fixed on the first pass |
| [`self_tuner`](apps/self_tuner) | Route support tickets with a versioned prompt | Prompt versions, optimize candidates, A/B outcomes, a guardrail rule, a skill | Tickets resolved by the optimized challenger against the current champion; activation audit trail |
| [`librarian`](apps/librarian) | Keep a lesson library from accreting near-duplicates | Seed lessons and entity facts; sleep-time consolidation distills them | Distilled evidence still answers; archive/dereference restores the original entry |
| [`researcher`](apps/researcher) | Multi-stage research under a context-window wipe | A checkpoint per stage, restored into a fresh run | Recovered brief completes every stage; wiped arm loses earlier clues |
| [`data_analyst`](apps/data_analyst) | Answer analytics questions through a query engine | Tribal-knowledge rules (aggregates before joins, date prefixes) attributed to the queries they shaped | Cold arm fails after a schema rename; memory arm answers all questions, outcomes attributed to the exact rules used |
| [`helpdesk`](apps/helpdesk) | Resolve internal IT requests | Per-user facts in the user's own run; runbook and admin guardrail in the main run | Requests resolved in fewer turns; the admin guardrail blocks an unapproved escalation the cold arm grants |
| [`soc_triage`](apps/soc_triage) | Triage security alerts | Benign-pattern lessons from analyst verdicts (`record_outcome`); a drift rule for look-alikes | Look-alike from a new host escalated with citation; investigation probes and false closes drop |
| [`code_reviewer`](apps/code_reviewer) | Review PRs against team conventions | Convention lessons linked to incident IDs | Violations found and cited; no false blockers |

Three earlier multi-agent pipelines stay in the repository as heavier examples
of the same memory calls inside pipeline code:

| App | What it shows |
| --- | --- |
| [`crash_recovery`](apps/crash_recovery) | A due-diligence pipeline crashes mid-run; Mubit detects the crash, restores state, and resumes |
| [`discovery`](apps/discovery) | A software-research pipeline with live web search and shared agent memory |
| [`orchestrator`](apps/orchestrator) | The LLM decides when to use memory: Mubit APIs exposed as function-calling tools |

[`concepts/sdm-vs-vector-store.html`](concepts/sdm-vs-vector-store.html) is a
standalone page on how the SDM/HDC medium differs from a vector store,
including the write and read procedure.

All environments are synthetic. No ticketing, healthcare, purchasing, security
or other business system is called.

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/)
- A Mubit endpoint and API key
- A Gemini API key (the default model is `gemini-3.6-flash`)
- Node.js 18 or later, only for the page tests of the supply chain and care coordination agents

## Configure

Copy `.env.example` to `.env` at the repository root and set the values. `make` loads
this file for every demo. A `.env` inside one demo folder also works when you run that
demo from its folder.

Most demos talk to the control plane only, so any Mubit 0.13+ endpoint works.
The librarian needs a server with fast sleep-time consolidation
(`MUBIT_CL_CONSOLIDATE_IDLE_SECS=5 MUBIT_CL_CONSOLIDATE_INTERVAL_SECS=5`;
see its `demo.py`).

## Run

```sh
make memory-router               # teach, then compare memory off and on in a new process
make oncall-agent                # observe + reflect, then apply + attribute, then evaluate
make policy-analyst              # bi-temporal setup + adjudication, then two-arm evaluation
make team-pipeline               # handoffs, event-driven reviewer, shared team lessons
make self-tuner                  # prompt versions, optimize, A/B activation, skill
make librarian                   # consolidation distillation + archive/dereference
make researcher                  # checkpoint survival across a context wipe, two arms
make data-analyst                # tribal-knowledge rules, schema drift, two arms
make helpdesk                    # per-user memory, runbook, admin guardrail
make soc-triage                  # benign-pattern lessons, drift re-classification
make code-reviewer               # convention lessons, incident-linked findings
make supply-chain                # web page on http://127.0.0.1:7870 (set PORT to change)
make supply-chain-check          # teach, then compare in a new process, without the page
make care-coordination           # web page on http://127.0.0.1:7880 (set PORT to change)
make care-coordination-compare   # 3-arm comparison on a fresh experiment
make test                        # offline tests for all thirteen demos; no keys needed
```

Every `make` demo uses a new experiment identifier on each run, so memory from
an earlier run does not change the result. The web pages keep their identifier
in the demo's `.demo/state.json` until you select **New experiment**.

## Live traces

Each demo commits the recorded output of a real run next to its code
(`live-*.jsonl` / `live-*.json`): the decisions, recalled evidence, entry IDs,
outcomes, and metrics. The traces are what the README tables and the
`how-it-works` pages cite. They are audit artifacts only — the memory itself
lives in Mubit, and re-running any demo regenerates equivalent files under a
new experiment identifier.

## Layout

```text
apps/<demo>/
├── demo.py (or agent.py + memory.py)   policy, synthetic environment, Mubit calls
├── __main__.py                         runs the phases as separate processes
├── test_demo.py                        offline suite (test doubles, no keys)
├── how-it-works.html / .md             explainer with example data at each step
├── live-*.jsonl|json                   recorded real run cited by the README
└── README.md                           how to run this one demo alone
```

Each demo folder has its own `README.md`, `requirements.txt` and `.env.example`, so
one folder can be copied out of this repository and run alone.

## Where Mubit is called

| Demo | SDK calls | Location |
| --- | --- | --- |
| `memory_router` | `remember`, `record_step_outcome`, `advanced.reflect`, `recall`, `record_outcome` | `Memory` in [`apps/memory_router/demo.py`](apps/memory_router/demo.py) |
| `supply_chain_agent` | `remember`, `recall`, `record_outcome` | `Memory` in [`apps/supply_chain_agent/agent.py`](apps/supply_chain_agent/agent.py) |
| `care_coordination_agent` | `remember`, `recall`, `record_outcome` | `Memory` in [`apps/care_coordination_agent/memory.py`](apps/care_coordination_agent/memory.py) |
| `oncall_agent` | `remember`, `record_step_outcome`, `advanced.reflect`, `recall`, `record_outcome` | `Memory` in [`apps/oncall_agent/demo.py`](apps/oncall_agent/demo.py) |
| `policy_analyst` | `remember` (with `occurrence_time`), `recall`, `record_outcome` | `Memory` in [`apps/policy_analyst/demo.py`](apps/policy_analyst/demo.py) |
| `team_pipeline` | `remember`, `recall`, `handoff`, `feedback`, SSE via `GET /v2/control/events/subscribe` | `Memory` in [`apps/team_pipeline/demo.py`](apps/team_pipeline/demo.py) |
| `self_tuner` | `remember`, `recall`, `reflect`, `optimize_prompt`, `advanced.set_prompt`/`get_prompt`, `advanced.activate_prompt_version`, `advanced.create_project`, `advanced.create_skill`, `record_outcome` | `Memory` in [`apps/self_tuner/demo.py`](apps/self_tuner/demo.py) |
| `librarian` | `remember`, `recall`, `lessons`, `memory_health`, `archive`, `dereference` | `Memory` in [`apps/librarian/demo.py`](apps/librarian/demo.py) |
| `researcher` | `remember`, `checkpoint`, `recall`, `get_context` | `Memory` in [`apps/researcher/demo.py`](apps/researcher/demo.py) |
| `data_analyst` | `remember`, `recall`, `record_outcome` (multi-entry attribution) | `Memory` in [`apps/data_analyst/demo.py`](apps/data_analyst/demo.py) |
| `helpdesk` | `remember`, `recall` (two scopes) | `Memory` in [`apps/helpdesk/demo.py`](apps/helpdesk/demo.py) |
| `soc_triage` | `remember`, `recall`, `record_outcome` | `Memory` in [`apps/soc_triage/demo.py`](apps/soc_triage/demo.py) |
| `code_reviewer` | `remember`, `recall` | `Memory` in [`apps/code_reviewer/demo.py`](apps/code_reviewer/demo.py) |

The three pipelines call a wider surface through their shared `memory.py`
(`register_agent`, `checkpoint`, `handoff`, `diagnose`, `memory_health`,
`surface_strategies`, `archive`/`dereference`).

SDK reference: [docs.mubit.ai/sdk/sdk-methods](https://docs.mubit.ai/sdk/sdk-methods).
