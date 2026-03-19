# Autonomous Orchestrator Agent — Mubit as Tools

An autonomous agent where the **LLM decides** when to use memory, not hardcoded
pipeline code. Mubit APIs are exposed as Gemini function-calling tools.

## How It's Different

| | Discovery / Crash Recovery | Orchestrator |
|---|---|---|
| **Control flow** | Hardcoded pipeline | LLM-driven |
| **Memory calls** | At predetermined points | Agent chooses when |
| **Agent count** | 4-6 specialized agents | 1 autonomous agent |
| **Mubit integration** | Explicit API calls in code | Function calling tools |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Gemini LLM (function calling)                          │
│                                                         │
│  "I should check my memory first..."                    │
│  → recall_memory(query="fintech payments")              │
│                                                         │
│  "Let me search the web for payment APIs..."            │
│  → [Google Search]                                      │
│                                                         │
│  "I found something important, let me save it..."       │
│  → store_memory(content="...", intent="fact")            │
│                                                         │
│  "Good progress, let me checkpoint..."                  │
│  → create_checkpoint(label="research-complete")          │
│                                                         │
│  "Time to reflect on what I've learned..."              │
│  → reflect_on_session()                                 │
└─────────────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
    ┌─────────┐        ┌───────────┐
    │  Mubit  │        │  Google   │
    │  Memory │        │  Search   │
    └─────────┘        └───────────┘
```

## Demo Flow

**Session 1**: "What fintech tech stack should I use?"
- Agent autonomously recalls prior knowledge, searches web, stores findings,
  checkpoints progress, produces recommendation, archives it, reflects.

**Session 2**: "Now add fraud detection to the stack"
- Agent recalls Session 1's fintech research from memory
- Builds on prior findings to recommend fraud detection tools
- Demonstrates cross-session learning

## Mubit Tools Available to the Agent

| Tool | Maps to | Description |
|------|---------|-------------|
| `store_memory` | `remember()` | Save findings to long-term memory |
| `recall_memory` | `recall()` | Search for prior knowledge |
| `get_assembled_context` | `get_context()` | Token-budgeted context assembly |
| `create_checkpoint` | `checkpoint()` | Save progress snapshot |
| `set_goal` | `add_goal()` | Track research goals |
| `update_goal` | `update_goal()` | Mark goals achieved |
| `reflect_on_session` | `reflect()` | Extract lessons |
| `archive_artifact` | `archive()` | Store immutable artifacts |
| `check_memory_health` | `memory_health()` | Memory statistics |
| `surface_strategies` | `surface_strategies()` | Pattern clustering |

## Running

```bash
cd demo
make orchestrator
```
