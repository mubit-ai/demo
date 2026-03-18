# Mubit Live Demo: "Agents That Remember, Learn, and Coordinate"

A 10-minute live coding demo for conferences. Shows a 3-agent software dev team that coordinates via handoffs, fails a task, reflects to extract lessons, and retries successfully.

## Setup (do this before the talk)

### 1. Start the stack

```bash
cd demo/live
docker compose -f docker-compose.demo.yml up -d
```

Wait ~30 seconds for Mubit + Redis to be ready.

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Set environment (or use defaults in .env.demo)

```bash
export MUBIT_ENDPOINT=http://127.0.0.1:3000
export MUBIT_API_KEY=demo-local-key
export MUBIT_TRANSPORT=http
```

### 4. Pre-seed (60 seconds before talk)

```bash
python scripts/00_preseed.py
```

This warms the embedding pipeline and ensures a clean session.

### 5. Launch Jupyter

```bash
jupyter notebook demo.ipynb
```

### Pre-save cell outputs (safety net)

Run all cells once before the talk so outputs are saved. If a cell fails live, the saved output is still visible:

```bash
jupyter nbconvert --execute --to notebook demo.ipynb
```

## Terminal Backup

If Jupyter dies, switch to:

```bash
python scripts/run_all.py
```

Same logic, sequential execution with `Press Enter` pauses between acts.

## Minute-by-Minute Talk Plan

| Time | Act | What to do | Key message |
|------|-----|-----------|-------------|
| 0:00-1:00 | **Setup** | Run cell 2 (Client constructor) | "3 lines to connect. Local Docker. No cloud." |
| 1:00-2:00 | **Register Agents** | Run cell 3 (3x register_agent) | "Scoped read/write permissions per agent role." |
| 2:00-3:00 | **Store + Handoff** | Run cells 4-5 (facts, rule, handoff) | "Handoffs are first-class memory, not strings." |
| 3:00-4:00 | **Developer Works** | Run cells 6-7 (trace, handoff to reviewer) | "Every trace is stored with full provenance." |
| 4:00-5:00 | **Reviewer Rejects** | Run cell 8 (feedback verdict=reject) | "Structured rejection. Queryable memory." |
| 5:00-6:00 | **Record Failure** | Run cells 9-10 (lesson + checkpoint) | "The failure is permanent memory, not a log line." |
| 6:00-7:00 | **Reflect** | Run cell 12 (reflect) | "Analyzes all traces and extracts reusable lessons." |
| 7:00-8:00 | **Context + Strategies** | Run cells 13-14 (strategies, get_context) | "Context now includes the failure lesson. Token-budgeted." |
| 8:00-9:00 | **Retry Succeeds** | Run cells 15-16 (outcome, approval) | "Lesson confidence increases with each success." |
| 9:00-10:00 | **Wrap** | Run cell 17 + show cell 18 | "123K items, sub-100ms p99. pip install mubit-sdk." |

## Fallback Plan

1. **Jupyter kernel dies** -> `python scripts/run_all.py`
2. **Docker crashes** -> Notebook has saved outputs from pre-run
3. **Running slow** -> Skip cells 13 (strategies) and 16 (approval) to save ~1.5 min
4. **API error** -> Core narrative only needs: remember -> fail -> reflect -> get_context

## Files

| File | Purpose |
|------|---------|
| `demo.ipynb` | Main Jupyter notebook (19 cells) |
| `docker-compose.demo.yml` | Self-contained Mubit + Redis |
| `.env.demo` | Environment defaults |
| `requirements.txt` | Python dependencies |
| `scripts/00_preseed.py` | Pre-warm infra before talk |
| `scripts/run_all.py` | Terminal backup script |
| `scripts/_common.py` | Shared connection helpers |
