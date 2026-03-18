# LangChain + MuBit: Conversational Research Assistant

A multi-turn conversational assistant that uses MuBit's `MubitChatMemory` to persist semantic memory across sessions.

## What it demonstrates

- **Session 1**: The assistant answers 3 research questions about the 2008 financial crisis. Each Q&A pair is automatically stored in MuBit.
- **Session 2**: A new session asks follow-up questions. MuBit retrieves relevant context from Session 1, enabling cross-session memory without any manual plumbing.

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
   export MUBIT_ENDPOINT="http://127.0.0.1:3000"  # optional, this is the default
   export MUBIT_API_KEY=""                          # optional, empty for local dev
   ```

## Run

```bash
python main.py
```

## What to observe

- Session 1 answers are coherent and build on each other within the session
- Session 2 responses reference facts from Session 1 (e.g., mentioning Dodd-Frank, subprime mortgages) even though it's a different session ID
- This cross-session recall happens because MuBit's `get_context()` retrieves semantically relevant memories regardless of session boundaries

## How it works

```
MubitChatMemory.load_memory_variables()
    → calls MuBit /v2/control/context (semantic retrieval)
    → returns [SystemMessage] with relevant context

MubitChatMemory.save_context()
    → calls MuBit /v2/control/ingest (stores user + assistant messages)
```
