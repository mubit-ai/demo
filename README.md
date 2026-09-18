# Mubit demos

Four small agents that use [Mubit](https://docs.mubit.ai) as their memory. Each demo
runs a teaching phase that stores what happened in Mubit. A later run then makes new
decisions with and without the stored lessons, and reports the difference. Every demo
folder also carries a `how-it-works.html` explainer page (with a Markdown companion)
that walks the loop with example data at each step.

| Demo | Agent task | What goes into memory | Evaluation |
| --- | --- | --- | --- |
| [`memory_router`](apps/memory_router) | Send a support request to one of three specialists | Observed routes and step outcomes; Mubit reflection writes the routing lessons | Six new requests (three paraphrases, three controls), memory off and on: first-route accuracy, handoffs, resolution steps |
| [`supply_chain_agent`](apps/supply_chain_agent) | Choose how to recover a component shortage | Operator-confirmed guidance from two incidents | Three new cases, each with no memory, full memory, and the applicable lesson removed: alignment, downtime, spend, tokens |
| [`care_coordination_agent`](apps/care_coordination_agent) | Coordinate one synthetic patient's encounters through validated actions | Code-derived operational lessons from observed outcomes and one clinician correction | Fresh encounters in three arms (no memory, full memory, one lesson category ablated) plus a current-instruction override test |
| [`oncall_agent`](apps/oncall_agent) | Triage incidents: two diagnostic probes, then one fix | Raw attempts and per-step rewards; Mubit reflection writes the triage lessons; verdicts are attributed to the cited entries | Six held-out incidents (paraphrases and controls), memory off and on: resolved, wasted probes |

All environments are synthetic. No ticketing, healthcare, purchasing or other business
system is called.

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/)
- A Mubit endpoint and API key
- A Gemini API key (the default model is `gemini-3.6-flash`)
- Node.js 18 or later, only for the page tests of the supply chain and care coordination agents

## Configure

Copy `.env.example` to `.env` at the repository root and set the values. `make` loads
this file for every demo. A `.env` inside one demo folder also works when you run that
demo from its folder.

## Run

```sh
make memory-router               # teach, then compare memory off and on in a new process
make oncall-agent                # observe + reflect, then apply + attribute, then evaluate
make supply-chain                # web page on http://127.0.0.1:7870 (set PORT to change)
make supply-chain-check          # teach, then compare in a new process, without the page
make care-coordination           # web page on http://127.0.0.1:7880 (set PORT to change)
make care-coordination-compare   # 3-arm comparison on a fresh experiment
make test                        # offline tests for all four demos; no keys needed
```

`make memory-router`, `make supply-chain-check`, `make oncall-agent` and
`make care-coordination-compare` use a new experiment identifier on every run, so
memory from an earlier run does not change the result. The web pages keep their
identifier in the demo's `.demo/state.json` until you select **New experiment**.

## Layout

```text
apps/
├── memory_router/
│   ├── demo.py             routing policy, synthetic specialists, Mubit calls
│   ├── __main__.py         runs the teach and evaluate phases as two processes
│   └── live-v2-*.jsonl     the recorded live runs that the README cites
├── supply_chain_agent/
│   ├── agent.py            Gemini decisions, Mubit calls, teach and compare loops
│   ├── scenarios.py        synthetic incidents, simulator, scripted operator feedback
│   ├── app.py, index.html  local API, event stream, web page
│   └── check_live.py       teach, restart, compare — as one script
├── care_coordination_agent/
│   ├── agent.py            bounded Gemini decisions inside a validated action loop
│   ├── lessons.py          deterministic, evidence-grounded lesson derivation
│   ├── compare.py          3-arm controlled comparison with frozen lesson snapshots
│   └── app.py, index.html  local API, event stream, web page
└── oncall_agent/
    ├── demo.py             triage policy, synthetic incidents, Mubit calls
    └── __main__.py         teach (observe, then apply + attribute), then evaluate
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

SDK reference: [docs.mubit.ai/sdk/sdk-methods](https://docs.mubit.ai/sdk/sdk-methods).
