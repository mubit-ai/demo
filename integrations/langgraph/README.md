# LangGraph + MuBit: Multi-Agent Code Review Pipeline

A StateGraph with 3 agent nodes that reviews code diffs, storing findings in MuBit and learning from past reviews.

## What it demonstrates

- **Graph-based multi-agent**: Planner, Reviewer (looping), and Summarizer nodes in a StateGraph
- **Conditional routing**: Reviewer loops back for each checklist item, then routes to Summarizer
- **MuBit store**: Each finding is stored via `PutOp`, past findings retrieved via `SearchOp`
- **MAS coordination**: Agent registration, handoffs, checkpoints, outcome recording
- **Cross-session learning**: Past review findings inform new reviews

## Graph Structure

```
START → planner → reviewer ─┐
                     ↑       │ (more items?)
                     └───────┘
                             │ (done)
                             ↓
                         summarizer → END
```

## Agents

| Node | Role | What it does |
|---|---|---|
| Planner | Review Planner | Analyzes diff, produces checklist of review items |
| Reviewer | Item Reviewer | Evaluates one item at a time, stores finding in MuBit |
| Summarizer | Review Summarizer | Assembles findings + MuBit context into final review |

## MuBit APIs exercised

- `store.batch([PutOp(...)])` — store each finding
- `store.batch([SearchOp(...)])` — search for past review findings
- `store.register_agent()` — register planner/reviewer/summarizer
- `store.checkpoint()` — after planner creates checklist
- `store.handoff()` — planner→reviewer, reviewer→summarizer
- `store.get_context()` — summarizer pulls assembled context
- `store.record_outcome()` — record review completion

## Sample code under review

The example reviews a Python module with intentional issues:
- SQL injection vulnerabilities (string formatting in queries)
- No input validation
- Password hash exposure in API response
- Missing error handling
- No transaction safety

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

- The Planner identifies 4-6 review items from the code
- The Reviewer loops through each item, making a separate LLM call and MuBit store for each
- The Summarizer produces a structured review with severity levels and an approve/reject recommendation
- On re-runs, the Planner's SearchOp retrieves findings from previous reviews
