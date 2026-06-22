"""
Thin, resilient Mubit SDK helper for the demo.

Cross-process memory works because every call is scoped to one stable SESSION
string (used as both run_id and session_id) — the same pattern as
demo/live/scripts/_common.py. All calls are wrapped so a transient server
blip never throws a traceback in front of a customer.
"""

import os
import time
import uuid

from mubit import Client

DEFAULT_ENDPOINT = "http://localhost:3000"


def session_id() -> str:
    # run_demo.sh sets a fresh one per run; standalone falls back to a fixed id.
    return os.environ.get("DEMO_SESSION", "mubitdemo")


def _api_key() -> str:
    return (
        os.environ.get("MUBIT_API_KEY")
        or os.environ.get("MUBIT_BOOTSTRAP_ADMIN_API_KEY")
        or "mbt_local_admin_secret"
    )


def connect(run_id=None):
    """Return (client, session_id)."""
    sess = run_id or session_id()
    client = Client(
        endpoint=os.environ.get("MUBIT_ENDPOINT", DEFAULT_ENDPOINT),
        api_key=_api_key(),
        run_id=sess,
    )
    client.set_transport(os.environ.get("MUBIT_TRANSPORT", "http"))
    return client, sess


def use_llm() -> bool:
    return os.environ.get("DEMO_LLM", "").strip().lower() in ("1", "true", "yes") and bool(
        os.environ.get("GEMINI_API_KEY")
    )


def evidence_texts(result) -> list:
    """Pull the human-readable text out of a recall result's evidence list."""
    out = []
    for ev in (result or {}).get("evidence", []) or []:
        if isinstance(ev, dict):
            t = ev.get("text") or ev.get("content") or ev.get("snippet") or ev.get("summary") or ""
        else:
            t = str(ev)
        if t:
            out.append(t.strip())
    return out


def store(client, sess, content, intent, agent_id="billing-support", retries=4):
    """Write a memory, retrying transient server blips. Returns (ok, error)."""
    last = None
    for attempt in range(retries):
        try:
            client.remember(content=content, session_id=sess, intent=intent, agent_id=agent_id)
            return True, None
        except Exception as e:  # transient server/transport hiccup
            last = str(e)[:200]
            time.sleep(0.6 * (attempt + 1))
    return False, last


def reflect(client, sess):
    """Trigger server-side reflection. Returns (lessons, error)."""
    try:
        r = client.reflect(session_id=sess) or {}
        return (r.get("lessons", []) or []), None
    except Exception as e:
        return [], str(e)[:200]


def recall(client, sess, query, limit=6):
    """
    Retrieve THIS thread's memory for `query`. Returns (result, texts, latency_ms, ok).

    `prefer_current_run=True` scopes retrieval to this session, so a fresh run
    returns nothing (honest "it hasn't learned yet") and a learned run returns
    only what it learned here — no cross-run noise. Never raises.
    """
    mode = "agent_routed" if use_llm() else "direct_lane"
    t0 = time.perf_counter()
    try:
        res = client.recall(
            query=query,
            session_id=sess,
            mode=mode,
            direct_lane="semantic_search",
            prefer_current_run=True,
            limit=limit,
        ) or {}
        ok = True
    except Exception as e:
        res, ok = {"_error": str(e)[:200]}, False
    dt_ms = (time.perf_counter() - t0) * 1000.0
    return res, evidence_texts(res), dt_ms, ok


def selfcheck():
    """
    Preflight: round-trip a throwaway memory to confirm the control plane can
    actually INGEST (not just answer /health). Catches a stopped Redis / broken
    ingest worker before the customer ever sees the demo. Returns (ok, detail).
    """
    try:
        client, _ = connect(run_id="mubitdemo-selfcheck-%s" % uuid.uuid4().hex[:8])
    except Exception as e:
        return False, "cannot construct client: %s" % (str(e)[:140])
    sess = client._transport.state.run_id
    ok, err = store(client, sess, "selfcheck", "fact", agent_id="selfcheck", retries=2)
    if not ok:
        return False, "ingest failed: %s" % (err or "unknown")
    return True, "ok"
