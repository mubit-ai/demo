# MuBit + Agno Demo: Research & Writing Team

A two-agent Agno team demonstrating cross-session memory with MuBit:

- **Researcher**: Investigates topics and stores findings in MuBit
- **Writer**: Drafts content using recalled research from MuBit memory

## Setup

```bash
# Start MuBit locally
make run-mubit

# Install dependencies
pip install -r requirements.txt

# Set your OpenAI API key
export OPENAI_API_KEY="sk-..."
```

## Run

```bash
python main.py
```

The demo runs two phases:

1. **Research Phase** — Researcher investigates RAG systems, stores findings as facts in MuBit, creates a checkpoint, and hands off to the Writer
2. **Writing Phase** — Writer recalls research from MuBit memory and drafts a blog post section, demonstrating cross-session knowledge transfer

## What to Look For

- Run 2's Writer discovers facts stored by Run 1's Researcher via MuBit recall
- MAS coordination: agent registration, handoff, outcomes, strategy surfacing
- Reflection extracts lessons from the combined evidence
