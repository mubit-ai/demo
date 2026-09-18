# Software Discovery — Multi-Agent Pipeline

A multi-agent system that researches and recommends software tools for a business
using live web search (Gemini + Google Search) and persistent memory (Mubit).

## Architecture

```
User Query
    │
    ▼
Coordinator ──────── Plans research strategy (3 categories)
    │
    ├──▶ Payments Researcher ─┐
    ├──▶ Billing Researcher  ─┤  (parallel, with Google Search)
    └──▶ Fraud Researcher ────┘
                              │
                              ▼
                         Evaluator ──── Scores tools (uses Mubit context from past runs)
                              │
                              ▼
                        Recommender ─── Final stack recommendation
```

## Memory Flow

- **Run 1**: Agents research from scratch. Findings stored as facts/lessons in Mubit.
  Post-run: `reflect()` extracts higher-order lessons, `surface_strategies()` clusters patterns.
- **Run 2**: `recall()` retrieves Run 1 findings. `get_context()` injects lessons into
  evaluator/recommender prompts. Cross-run learning produces better recommendations.

## Mubit APIs Used

| API | Purpose |
|-----|---------|
| `remember()` | Store agent outputs as typed memory entries |
| `recall()` | Search for prior research before each run |
| `get_context()` | Token-budgeted context assembly for LLM prompts |
| `reflect()` | Extract lessons from session findings |
| `surface_strategies()` | Cluster high-value lessons across runs |
| `memory_health()` | Entry counts and confidence metrics |
| `register_agent()` | Register agents with roles |
| `handoff()` | Record agent-to-agent task transfers |
| `feedback()` | Submit verdicts on handoffs |
| `checkpoint()` | Snapshot pipeline state |
| `archive()` / `dereference()` | Store and retrieve exact-reference artifacts |
| `record_outcome()` | Record success/failure with reinforcement signal |

## Running

```bash
cd demo
export MUBIT_ENDPOINT=https://api.mubit.ai
export MUBIT_API_KEY="mbt_..."
export GOOGLE_API_KEY="..."

make discovery
```
