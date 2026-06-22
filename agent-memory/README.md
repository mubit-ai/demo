# Agent Memory & Learning — local demo

**A 5-minute, run-it-locally demo that shows why agents need Mubit.**

A support agent answers the *same* customer the *same* question on two different
days. On its own, it fumbles — and the next day (a brand-new process) it fumbles
again. It has no memory. Then we run the **same agent on Mubit**: it's corrected
once, *reflects to write its own rule*, remembers across the process boundary,
and is right ever after. Same model, same prompt — the only difference is Mubit.

> **The pitch:** "Your agents demo great and forget the customer by tomorrow.
> Mubit gives them memory that persists **and a closed loop that learns** — about
> three lines of code, running entirely on your own box."

---

## One command

```bash
make demo-memory            # SaaS billing scenario
make demo-memory-fintech    # fintech / neobank scenario (unauthorized-charge dispute)
```

That's it. It auto-uses a **live Gemini agent** if a `GEMINI_API_KEY` is in the
repo `.env` (the most convincing version — a real model that reasons over the
injected memory), and falls back to a **deterministic offline reproduction**
(clearly labelled "no model") if no key is present. Add `AUTO=1` for no pauses;
`DEMO_LLM=0` to force the offline path.

Prereqs (all local): `make run-mubit` (monolith + Redis on `:3000`), the
embedding service on `:8080`, and `pip install -e sdk/python/mubit-sdk requests`.
The runner pre-flights the server, embedder, SDK, **and a real write round-trip**
(so a stopped Redis is caught before the customer sees anything) and aborts with
the exact fix command if something's missing.

---

## Why this lands

- **Visceral before/after.** With no memory the agent repeats its mistake in a
  fresh process. The pain is obvious in 90 seconds.
- **It learns, it doesn't just store.** Mubit *reflects* on the failure and
  writes a reusable lesson **no one typed** — the moat over a plain vector DB.
- **It's real, not a prompt trick.** Day 2 is a *separate OS process* (printed
  pid) — nothing is in RAM. We prove it with a `curl` from a *different* client.
- **Honest by construction.** Both runs share the *same* agent backend, so the
  only variable is Mubit. Retrieval is thread-scoped (a fresh run recalls
  nothing), and scoring is a strict, documented all-elements check (`scenario.py`).

---

## What to say (≈5 min)

| Time | Act | On screen | Say |
|------|-----|-----------|-----|
| 0:00 | Frame | (title card) | "Same agent, same customer, day 1 vs day 2. Watch the difference." |
| 0:30 | **No memory** | Act 1 | Day 1 wrong → we correct it → Day 2 (new process) **wrong again**. "It forgot. This is your day-two problem." |
| 2:00 | **On Mubit** | Act 2, day 1 | Wrong → we teach the policy as a fact → **Mubit reflects and writes the rule itself**. "A vector DB stores the string you give it. Watch Mubit derive a rule no one typed." |
| 3:00 | **The result** | Act 2, day 2 | Fresh process → recalls the policy → **answer is correct**. (~2 s recall = the control plane *understanding* the query; the raw vector lookup is sub-10 ms.) |
| 3:45 | **Contrast** | the contrast card | Without-Mubit FAIL→FAIL vs On-Mubit FAIL→PASS, side by side. "Same model. Same prompt. Only difference: Mubit." |
| 4:30 | **Prove it** | Act 3 `curl` | A separate HTTP client gets the same memory + the lesson as a first-class object (scope, type). "Real server state, not a script variable." |

**"Isn't this just a vector DB?"** → "A vector DB stores the string you hand it.
Mubit **reflected** on the failure and authored the rule itself. And —" run the
deep-cut:

```bash
python3 demo/agent-memory/03_distractors.py                       # billing
DEMO_SCENARIO=fintech python3 demo/agent-memory/03_distractors.py
```

It seeds 8 memories (mostly unrelated, plus one *outdated* contradictory policy),
asks a **paraphrased** question worded nothing like what's stored, and Mubit
ranks the **current** policy first. A keyword/dict lookup can't do that.

---

## Scenarios — and retargeting to the customer's world

Two ship in the box; pick with `DEMO_SCENARIO`:

- **`billing`** (default) — SaaS billing: the double-charge refund policy.
- **`fintech`** — neobank: an unauthorized-card-charge dispute (provisional
  credit within 10 business days, Regulation E).

To build your own, drop a module in **`scenarios/`** exposing the same fields
(copy `scenarios/billing.py`) and set `DEMO_SCENARIO` to its name. Nothing else
changes.

---

## Files

| File | Purpose |
|------|---------|
| `run_demo.sh` | Presenter orchestrator: preflight (+write round-trip) → Act 1 → Act 2 → contrast card → curl proof |
| `01_without_memory.py` | BEFORE: stateless agent; two days as two processes; forgets |
| `02_with_mubit.py` | AFTER: same agent; teach a fact → Mubit reflects a rule → recalls across processes |
| `03_distractors.py` | OPTIONAL deep-cut: semantic ranking over distractors + an outdated policy + a paraphrased query |
| `scenario.py` | Scenario selector + the (transparent) scoring rule |
| `scenarios/` | `billing.py`, `fintech.py` — the swappable scenario definitions |
| `agent.py` | The tiny shared agent (deterministic + optional live-Gemini backends) |
| `_mubit.py` | Resilient SDK helper: retrying store, safe recall, ingest selfcheck |
| `_ui.py` | Terminal styling (load-bearing text stays bright/bold) |

---

## Troubleshooting

- **"Mubit can't ingest — Redis is likely down"** → `docker start mubit-local-redis`
  (or `make redis-up`), then ensure the server is up (`make run-mubit`). The
  control plane needs Redis; `/health` can be green while ingest is down — which
  is exactly what the preflight write-test guards against.
- **"Mubit is not responding"** → `make run-mubit` (first build is 3–5 min).
- **"Embedding service is not responding"** → start it; first launch downloads the
  768-d model, so warm it *before* the call.
- **Recall feels slow (~2 s)** → that's the control-plane query-*understanding*
  step, not raw retrieval (sub-10 ms in-process HNSW). Own it as "the control
  plane reasoning about the query."
