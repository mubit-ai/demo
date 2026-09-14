# Mubit demos

Two small agents that use [Mubit](https://docs.mubit.ai) as their memory. Each demo
runs a teaching phase that stores what happened in Mubit. A later run then makes new
decisions with and without the stored lessons, and reports the difference.

| Demo | Agent task | What goes into memory | Evaluation |
| --- | --- | --- | --- |
| [`memory_router`](apps/memory_router) | Send a support request to one of three specialists | Observed routes and step outcomes; Mubit reflection writes the routing lessons | Six new requests (three paraphrases, three controls), memory off and on: first-route accuracy, handoffs, resolution steps |
| [`supply_chain_agent`](apps/supply_chain_agent) | Choose how to recover a component shortage | Operator-confirmed guidance from two incidents | Three new cases, each with no memory, full memory, and the applicable lesson removed: alignment, downtime, spend, tokens |

Both environments are synthetic. No ticketing, purchasing or other business system is called.

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/)
- A Mubit endpoint and API key
- A Gemini API key (the default model is `gemini-3.6-flash`)
- Node.js 18 or later, only for one page test of the supply chain agent

## Configure

Copy `.env.example` to `.env` at the repository root and set the values. `make` loads
this file for both demos. A `.env` inside one demo folder also works when you run that
demo from its folder.

## Run

```sh
make memory-router        # teach, then compare memory off and on in a new process
make supply-chain         # web page on http://127.0.0.1:7870 (set PORT to change)
make supply-chain-check   # teach, then compare in a new process, without the page
make test                 # offline tests for both demos; no keys needed
```

`make memory-router` and `make supply-chain-check` use a new experiment identifier on
every run, so memory from an earlier run does not change the result. The web page keeps
its identifier in `apps/supply_chain_agent/.demo/state.json` until you select
**New experiment**.

## Layout

```text
apps/
├── memory_router/
│   ├── demo.py             routing policy, synthetic specialists, Mubit calls
│   ├── __main__.py         runs the teach and evaluate phases as two processes
│   ├── tests/              offline tests with test doubles
│   └── traces/             the recorded live run that the README cites
└── supply_chain_agent/
    ├── agent.py            Gemini decisions, Mubit calls, teach and compare loops
    ├── scenarios.py        synthetic incidents, simulator, scripted operator feedback
    ├── app.py              local API and event stream
    ├── index.html          web page
    └── tests/              offline tests, and opt-in live and browser checks
```

Each demo folder has its own `README.md`, `requirements.txt` and `.env.example`, so
one folder can be copied out of this repository and run alone.

## Where Mubit is called

| Demo | SDK calls | Location |
| --- | --- | --- |
| `memory_router` | `remember`, `record_step_outcome`, `advanced.reflect`, `recall`, `record_outcome` | `Memory` in [`apps/memory_router/demo.py`](apps/memory_router/demo.py) |
| `supply_chain_agent` | `remember`, `recall`, `record_outcome` | `Memory` in [`apps/supply_chain_agent/agent.py`](apps/supply_chain_agent/agent.py) |

SDK reference: [docs.mubit.ai/sdk/sdk-methods](https://docs.mubit.ai/sdk/sdk-methods).
