#!/usr/bin/env bash
#
# Agent Memory & Learning — local demo (presenter orchestrator).
#
#   bash run_demo.sh                # interactive: pauses between acts (press Enter)
#   AUTO=1 bash run_demo.sh         # no pauses (dry run / recording)
#   DEMO_LLM=1 bash run_demo.sh     # force the live Gemini agent (needs GEMINI_API_KEY)
#   DEMO_LLM=0 bash run_demo.sh     # force the deterministic offline reproduction
#   DEMO_SCENARIO=fintech bash run_demo.sh   # switch scenario (billing | fintech | …)
#
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(cd ../.. && pwd)"   # repo root (this demo lives at demo/agent-memory/)

# Single-command UX: pull GEMINI_API_KEY from the repo .env if present so the
# live-model path "just works". Everything else stays pinned to localhost.
if [ -z "${GEMINI_API_KEY:-}" ] && [ -f "$REPO/.env" ]; then
  GEMINI_API_KEY="$(grep -E '^GEMINI_API_KEY=' "$REPO/.env" | head -1 | cut -d= -f2-)"
  [ -n "$GEMINI_API_KEY" ] && export GEMINI_API_KEY
fi

export PYTHONPATH="$REPO/sdk/python/mubit-sdk/src${PYTHONPATH:+:$PYTHONPATH}"
export MUBIT_ENDPOINT="${MUBIT_ENDPOINT:-http://localhost:3000}"
export MUBIT_API_KEY="${MUBIT_API_KEY:-mbt_local_admin_secret}"
export MUBIT_TRANSPORT="${MUBIT_TRANSPORT:-http}"
export DEMO_SCENARIO="${DEMO_SCENARIO:-billing}"               # billing | fintech | …
export DEMO_SESSION="${DEMO_SESSION:-mubitdemo-$(date +%s)}"   # fresh + shared by both days
export DEMO_STATE_DIR="$(mktemp -d)"
trap 'rm -rf "$DEMO_STATE_DIR"' EXIT
# Lead with the live agent when a key is available; deterministic offline otherwise.
export DEMO_LLM="${DEMO_LLM:-$([ -n "${GEMINI_API_KEY:-}" ] && echo 1 || echo 0)}"

BOLD=$'\033[1m'; DIM=$'\033[2m'; CYAN=$'\033[96m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; OFF=$'\033[0m'
[ -t 1 ] || { BOLD=; DIM=; CYAN=; GREEN=; RED=; YEL=; OFF=; }

say()  { printf '%b\n' "$*"; }
rule() { printf '%s\n' "${CYAN}══════════════════════════════════════════════════════════════════${OFF}"; }
pause(){ if [ "${AUTO:-0}" = "1" ]; then sleep "${1:-1}"; else printf '%s' "${DIM}  ↵ press Enter…${OFF}"; read -r _ || true; fi; }
tag()  { case "$1" in PASS) printf '%s' "${GREEN}${BOLD}[PASS]${OFF}";; FAIL) printf '%s' "${RED}[FAIL]${OFF}";; *) printf '%s' "${YEL}[n/a]${OFF}";; esac; }
rstat(){ grep -h "day$2=" "$DEMO_STATE_DIR/demo_status_$1" 2>/dev/null | tail -1 | cut -d= -f2; }
fail() { say "${RED}✗ $1${OFF}"; say "${DIM}$2${OFF}"; exit 1; }

# ── Preflight: server, embedder, SDK, and a REAL ingest round-trip ───────────
curl -fsS -m 3 "$MUBIT_ENDPOINT/v2/core/health" >/dev/null 2>&1 \
  || fail "Mubit is not responding at $MUBIT_ENDPOINT" "  Start it from the repo root:  make run-mubit"
curl -fsS -m 3 "http://localhost:8080/health" >/dev/null 2>&1 \
  || fail "Embedding service is not responding at http://localhost:8080" \
          "  Start it:  cd deploy/embedding-service && uv sync && DEVICE=mps uv run uvicorn app:app --host 0.0.0.0 --port 8080"
python3 -c "import mubit, requests" 2>/dev/null \
  || fail "Python deps missing" "  pip install -e $REPO/sdk/python/mubit-sdk && pip install requests"
# /health can be green while the control plane (Redis-backed ingest) is down —
# so prove we can actually WRITE a memory before the customer sees anything.
SC="$(python3 -c "import _mubit; ok,m=_mubit.selfcheck(); print(('OK' if ok else 'FAIL')+'|'+m)" 2>&1 || true)"
case "$SC" in
  OK*) : ;;
  *) fail "Mubit can't ingest — the control plane (Redis) is likely down" \
          "  detail: ${SC#FAIL|}\n  fix:    docker start mubit-local-redis   (or  make redis-up)\n          then make sure the server is running:  make run-mubit" ;;
esac

QUERY="$(python3 -c 'from scenario import QUESTION; print(QUESTION)' 2>/dev/null || echo 'What is the policy?')"

[ -t 1 ] && { clear 2>/dev/null || true; }
rule
say "  ${BOLD}AGENT MEMORY & LEARNING${OFF} — does the agent remember, and does it learn?"
say "  ${DIM}A support agent answers the same customer on two different days.${OFF}"
say "  ${DIM}Once like a stateless agent. Once on Mubit. Watch the difference.${OFF}"
say "  ${DIM}scenario=${DEMO_SCENARIO}   backend=$( [ "${DEMO_LLM:-0}" = 1 ] && echo 'live Gemini agent' || echo 'deterministic offline (set DEMO_LLM=1 for the live model)' )${OFF}"
rule
pause

# ── Act 1: no memory ─────────────────────────────────────────────────────────
say ""; say "${BOLD}${RED}ACT 1 — the agent today (no memory).${OFF}"
python3 01_without_memory.py
pause

# ── Act 2: the same agent on Mubit ───────────────────────────────────────────
say ""; say "${BOLD}${GREEN}ACT 2 — the same agent, on Mubit.${OFF}"
python3 02_with_mubit.py

# ── The climax: the contrast on one screen ───────────────────────────────────
say ""; rule
say "  ${BOLD}THE CONTRAST${OFF} — same agent, same question, two days:"
say "    Without Mubit (no memory):  day 1 $(tag "$(rstat baseline 1)")  →  day 2 $(tag "$(rstat baseline 2)")   ${DIM}forgot, repeated the mistake${OFF}"
say "    On Mubit:                   day 1 $(tag "$(rstat mubit 1)")  →  day 2 $(tag "$(rstat mubit 2)")   ${BOLD}corrected once, right ever after${OFF}"
rule
pause

# ── Act 3: prove it's real server-side state, not a script variable ──────────
say ""; say "${BOLD}${CYAN}ACT 3 — don't trust the script? A separate client hits the server over HTTP.${OFF}"
set +e  # proof-points must never abort the close
say "${DIM}\$ curl -X POST /v2/control/query   (run_id=${DEMO_SESSION})${OFF}"
curl -fsS -m 8 -X POST "$MUBIT_ENDPOINT/v2/control/query" \
  -H "Authorization: Bearer $MUBIT_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"run_id\":\"$DEMO_SESSION\",\"query\":\"$QUERY\",\"mode\":\"direct_lane\",\"direct_lane\":\"semantic_search\",\"prefer_current_run\":true,\"limit\":3}" 2>/dev/null \
  | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); ev=d.get("evidence",[]) or []
    print("  evidence returned: %d" % len(ev))
    for e in ev[:3]: print("   -",(e.get("text") or e.get("content") or "")[:90])
except Exception:
    print("  (server returned no parseable response — momentarily busy)")'

say ""; say "${DIM}\$ curl -X POST /v2/control/lessons   (a first-class object with scope + type — fields a vector DB does not have)${OFF}"
curl -fsS -m 8 -X POST "$MUBIT_ENDPOINT/v2/control/lessons" \
  -H "Authorization: Bearer $MUBIT_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"run_id\":\"$DEMO_SESSION\",\"limit\":5}" 2>/dev/null \
  | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); ls=d.get("lessons",d.get("results",[])) or []
    print("  lessons stored: %d" % len(ls))
    for l in ls[:3]:
        print("   - %s | scope=%s type=%s" % ((l.get("content") or "")[:70], l.get("scope") or l.get("lesson_scope"), l.get("lesson_type") or l.get("type")))
except Exception:
    print("  (server returned no parseable response — momentarily busy)")'
set -e

# ── Close: the takeaway ──────────────────────────────────────────────────────
say ""; rule
say "  ${BOLD}Same model. Same prompt. Same question.${OFF}"
say "  ${RED}Without Mubit:${OFF} wrong on day 1, wrong again on day 2 — no memory of the customer."
say "  ${GREEN}With Mubit:${OFF}    wrong once, corrected once, ${BOLD}right ever after${OFF} — and it ${BOLD}reflected${OFF} the rule itself."
rule
