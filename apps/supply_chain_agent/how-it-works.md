# Supply Chain Agent — How It Works

> Companion to [`how-it-works.html`](how-it-works.html) (same content, with the full example payloads).
> All plants, inventory, feedback and consequences are fictional. No purchasing actions or ERP connections.

Cedar Manufacturing (fictional) hits a component shortage. One Gemini agent chooses a recovery option; a
deterministic simulator computes consequences; scripted **operator feedback** teaches the organization's
real tradeoff preferences — stored verbatim in Mubit and carried into fresh processes.

- **Pathway:** deterministic — harness-owned SDK calls; lessons are the operator's confirmed words, byte-checked.
- **Mubit surface:** `remember` · `recall` · `record_outcome`.

## The scenario

The agent starts with a conservative public default — *absent an applicable local precedent, minimize
combined downtime, then spend* — and no local tolerance. The organization's actual preferences exist only
in the private evaluator and the teaching feedback:

| Rule | Preference (fictional, operator-taught) |
|---|---|
| Replenishment | Accept up to **2 hours** receiving downtime, zero donor downtime; cheapest acceptable option first |
| Firm orders | **Zero** receiving and donor downtime — freight justified to prevent even a small delay |
| Fallback | No acceptable option → minimize combined downtime, then spend |

T1/T2 are live teaching decisions with labeled operator feedback; E1/E2/E3 are fresh evaluation cases.
**E3 is the anti-over-indexing control**: a firm order must still expedite — the agent must never learn
"always wait."

## The loop, with example data at each step

Every lesson becomes a hypervector in Mubit's medium; recall is the SDM consensus read over it.

### ① Capture — snapshot in, decision out, consequences out

```json
// snapshot the agent sees (logistics facts only — never the private preference)
{"id": "T1", "plant": "Cedar assembly", "component": "Controller C-24",
 "shortage_quantity": 3, "needed_at": "…T14:00",
 "receiving": {"opens": 8, "closes": 17}, "order_class": "replenishment",
 "options": [
   {"id": "wait",       "kind": "wait",      "cost": 0,    "inspection_hours": 0},
   {"id": "expedite",   "kind": "expedite",  "cost": 1400, "inspection_hours": 1},
   {"id": "transfer",   "kind": "transfer",  "cost": 350,  "donor_stock": 500},
   {"id": "substitute", "kind": "substitute","cost": 800,  "inspection_hours": 1}]}

// decision out (cold: conservative default, nothing to cite)
{"option_id": "substitute", "lesson_ids": [],
 "rationale": "No local precedent. Default: minimize combined downtime — substitute avoids a receiving stoppage."}

// deterministic simulator outcome
{"receiving_downtime_h": 0.0, "donor_downtime_h": 0.0, "recovery_cost": 800}
```

### ② Distill — the operator teaches the rule, verbatim

```json
{"verdict": "Instructive", "text": "…at $800. Accept up to 2 hours of receiving-plant downtime …",
 "confirmed_lesson": {
   "applies_to": "replenishment",
   "applicability": "Cedar stock-replenishment orders only.",
   "guidance": "Accept up to 2 hours of receiving-plant downtime with zero donor downtime. Among options within these limits, choose the lowest recovery spend, then the least downtime.",
   "exceptions": "Do not apply this tolerance to firm customer orders. If no option meets the limits, fall back to the conservative default."}}
```

Correct initial choices receive confirming feedback instead. There is **no generative distillation call** —
no threshold can be invented.

### ③ Gate — byte-equality or nothing is stored

```python
if lesson != operator["confirmed_lesson"]:
    raise ValueError("Lesson differs from the operator-confirmed evidence")
```

Plus schema validation (enum `applies_to`, bounded field lengths). Storage fails loudly on any mismatch.

### ④ Promote — lesson and evidence written as separate entries

```python
client.remember(
    content = f"[experiment:sc-x] [demo:judgment-v2]\n"
              + json.dumps({"source_case": "T1", "lesson": lesson}, sort_keys=True)
              + "\nSupporting observed incident: "
              + json.dumps({"snapshot": …, "decision": …, "outcome": …,
                            "operator_feedback": …, "execution_id": …}, sort_keys=True),
    intent="lesson", lesson_type="success" if operator["verdict"] == "Confirmed" else "failure",
    lesson_scope="run", lesson_importance="high", agent_id="shortage-agent",
    upsert_key=f"judgment-v2:sc-x:T1",           # re-teaching updates the slot
    wait=True, timeout_ms=60000)
```

The full evidence block is stored for audit but **never enters a decision prompt**.

### ⑤ Inject — only the allowlist reaches the next decision

```json
[{"id": "a1b2…", "source_case": "T1", "applies_to": "replenishment",
  "applicability": "Cedar stock-replenishment orders only.",
  "guidance": "Accept up to 2 hours …", "exceptions": "Do not apply … firm customer orders. …"}]

// with that, the fresh-process decision flips to the operator's preference:
{"option_id": "wait", "lesson_ids": ["a1b2…"],
 "rationale": "Operator-confirmed tolerance accepts up to 2 h receiving downtime for replenishment; wait costs $0."}
```

### ⑥ Attribute — cited lessons only

```python
client.record_outcome(reference_id="a1b2…", outcome="success", signal=1.0,
                      rationale="Simulated teaching result: … operator confirmed …",
                      verified_in_production=False)
```

## Measured result (committed live run, 2026-09-07, fresh processes, real Gemini + Mubit)

| Condition | Aligned cases | Downtime | Recovery spend |
|---|---:|---:|---:|
| No memory | 1 / 3 | 0 h | $3,000 |
| **Full memory** | **3 / 3** | 2 h | $1,750 |
| Applicable lesson removed | 1 / 3 | 0 h | $3,000 |

Removing one lesson reversed both replenishment choices — influence is measured by behavior change, not
citations. The firm-order case (E3) stayed correct in every condition: the waiting tolerance never
broadened beyond its stated order class. Memory cost in this run: 40.2% input-token overhead (19.4% total),
reported as measured.

## What this demonstrates

- Human-in-the-loop knowledge enters memory verbatim, scoped, and exception-carrying.
- Over-indexing control: exceptions travel with the lesson; E3 measures no broadening; the removal control measures true influence.
- Persistence across restarts: teaching and evaluation are separate processes.
- Deterministic pathway: fixed SDK call sites, byte-equality gate, prompt allowlist — auditable by inspection.

## Run it

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # GEMINI_API_KEY, MUBIT_ENDPOINT, MUBIT_API_KEY
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7870   # browser demo
.venv/bin/python check_live.py                                     # teach → restart → compare
```

Part of the Mubit demo suite: [Care Coordination Agent](../care_coordination_agent/how-it-works.md) ·
[Memory Router](../memory_router/how-it-works.md) · benchmark evidence:
[mubit-cl-bench](https://github.com/mubit-ai/mubit-cl-bench).
