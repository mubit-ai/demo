# Google ADK + MuBit: Travel Planning Multi-Agent

A multi-agent travel planner using ADK's `SequentialAgent` orchestration with MuBit memory for cross-agent and cross-session context.

## What it demonstrates

- **ADK SequentialAgent**: Three specialized agents execute in sequence
- **Tool calling**: Flight and hotel search tools called by Gemini
- **Shared state**: Each agent writes results via `output_key`, readable by subsequent agents
- **MuBit memory**: All session events are stored in MuBit, enabling cross-session learning
- **MAS coordination**: Agent registration, checkpoints, outcome recording

## Agents

| Agent | Model | Tools | Role |
|---|---|---|---|
| Flight Finder | gemini-2.0-flash | `search_flights` | Finds best flights |
| Hotel Finder | gemini-2.0-flash | `search_hotels` | Finds accommodations |
| Itinerary Planner | gemini-2.0-flash | — | Creates day-by-day plan |
| Travel Coordinator | SequentialAgent | — | Orchestrates the above |

## MuBit APIs exercised

- `MubitMemoryService.add_session_to_memory()` — automatic via ADK Runner
- `MubitMemoryService.search_memory()` — automatic via ADK Runner
- `mubit_memory.register_agent()` — registers all 3 specialized agents
- `mubit_memory.checkpoint()` — snapshots state after planning completes
- `mubit_memory.record_outcome()` — records planning success/failure
- `mubit_memory.surface_strategies()` — surfaces travel planning patterns

## Setup

1. Start MuBit locally:
   ```bash
   make run-mubit   # from repo root
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set environment variables:
   ```bash
   export GOOGLE_API_KEY="..."
   export MUBIT_ENDPOINT="http://127.0.0.1:3000"  # optional
   export MUBIT_API_KEY=""                          # optional
   ```

## Run

```bash
python main.py
```

## What to observe

- Each agent prints its output as it executes
- The Flight Finder calls `search_flights` and recommends a flight
- The Hotel Finder calls `search_hotels` and recommends a hotel
- The Itinerary Planner combines everything into a day-by-day plan
- Post-run: agents are registered, checkpoint created, outcome recorded
- On re-runs: MuBit memory informs the agents with context from previous trips
