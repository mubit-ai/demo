# CrewAI + MuBit: Support Ticket Triage Crew

A three-agent crew that processes customer support tickets with MuBit-backed memory for cross-session learning.

## What it demonstrates

- **Multi-agent crew**: Classifier, Researcher, and Responder agents work sequentially
- **MuBit memory**: CrewAI's Memory system is backed by MuBit storage, so all agent observations persist
- **MAS coordination**: Agent registration, handoffs between agents, checkpoints, outcome recording
- **Cross-session learning**: On re-runs, the Researcher agent retrieves lessons from previous triage runs

## Agents

| Agent | Role | What it does |
|---|---|---|
| Classifier | Ticket Classifier | Categorizes severity, type, and escalation flags |
| Researcher | Solution Researcher | Queries MuBit memory for similar past tickets and solutions |
| Responder | Response Drafter | Crafts an empathetic customer reply using classification + research |

## MuBit APIs exercised

- `MubitCrewMemory.register_agent()` — registers all 3 agents
- `MubitStorage.save()` / `.search()` — automatic via CrewAI Memory
- `MubitCrewMemory.handoff()` — records classifier→researcher→responder handoffs
- `MubitCrewMemory.checkpoint()` — snapshots state after triage completion
- `MubitCrewMemory.record_outcome()` — records triage success/failure
- `MubitCrewMemory.surface_strategies()` — surfaces learned patterns

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
   export OPENAI_API_KEY="sk-..."
   export MUBIT_ENDPOINT="http://127.0.0.1:3000"  # optional
   export MUBIT_API_KEY=""                          # optional
   ```

## Run

```bash
python main.py
```

## What to observe

- Each agent prints its reasoning (verbose mode)
- The final output is a professional customer response email
- Post-run: handoffs, checkpoint, and outcome are recorded in MuBit
- Running again: the Researcher should find relevant memories from the previous run
