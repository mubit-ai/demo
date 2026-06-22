#!/usr/bin/env python3
"""
AFTER — the same agent, on Mubit.

Day 1: the agent fumbles (same as the no-memory run). A supervisor corrects it;
the correction is written to Mubit as a FACT, and Mubit *reflects* on the failure
and distills its own reusable LESSON (no one types the rule — Mubit authors it).
The process exits — the knowledge now lives only in Mubit, not RAM.

Day 2: a BRAND-NEW process gets the same question, recalls the policy (scoped to
this thread), and answers correctly. Same model, same prompt as the no-memory
run — the only difference is that Mubit remembered.

    python3 02_with_mubit.py            # day 1, then day 2 (separate processes)
    python3 02_with_mubit.py --day 1    # one day only (used by the orchestrator)
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(line_buffering=True)

try:
    import _mubit
    from mubit import Client  # noqa: F401  (import-time sanity check)
except ModuleNotFoundError:
    sys.stderr.write(
        "\n[setup] Mubit SDK not found. Install it:\n"
        "  pip install -e %s/sdk/python/mubit-sdk && pip install requests\n\n"
        % os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    sys.exit(2)

from _ui import banner, bold, cyan, dim, green, red, verdict_label, white, yellow
from agent import backend_note, respond
from scenario import (
    CORRECT_POLICY,
    CUSTOMER,
    EXPECTED_KEYWORDS,
    FAILED_TRACE,
    LESSON,
    QUESTION,
    score_answer,
    verdict,
)

AGENT_ID = "support-agent"
WHO = "mubit"
STATE = os.path.join(os.environ.get("DEMO_STATE_DIR", tempfile.gettempdir()), "demo_status_%s" % WHO)


def _write_status(day, v):
    try:
        with open(STATE, "w" if day == 1 else "a") as f:
            f.write("day%d=%s\n" % (day, v))
    except OSError:
        pass


def run_day(day: int) -> None:
    client, sess = _mubit.connect()
    banner(f"DAY {day}  —  Same agent, WITH Mubit", f"pid={os.getpid()}  ·  session={sess}")

    if day == 1:
        _res, texts, _dt, ok = _mubit.recall(client, sess, QUESTION)
        held = len(texts) if ok else "?"
        print(f"  {dim('Mubit holds %s memories for this customer thread — it has not learned anything yet.' % held)}")
        print(f"  Customer ({CUSTOMER}): {white(QUESTION)}")

        answer, label = respond(QUESTION)  # no memory injected -> fumbles
        frac, found = score_answer(answer)
        print(f"  Agent {dim('(' + backend_note(label) + ')')}: {white(answer)}")
        print(f"  {verdict_label(frac, found)}")
        _write_status(1, verdict(frac))

        print()
        print(f"  {yellow('Supervisor corrects the agent. Teaching Mubit the company policy as a fact…')}")
        ok_fact, err = _mubit.store(client, sess, CORRECT_POLICY, intent="fact", agent_id=AGENT_ID)
        _mubit.store(client, sess, FAILED_TRACE, intent="observation", agent_id=AGENT_ID)
        if not ok_fact:
            print(f"  {red('✗ could not write to Mubit: %s' % err)}")
            print(f"  {yellow('Is the server + Redis healthy?  make redis-up  &&  make run-mubit')}")
            return
        print(f"    {green('✓')} stored the correction")

        print(f"  {dim('(reflecting on the run and distilling a reusable lesson…)')}")
        lessons, _rerr = _mubit.reflect(client, sess)
        if lessons:
            print(f"  {green('✓ Mubit reflected on the failure and wrote this rule itself — no one typed it:')}")
            for ls in lessons[:2]:
                print(f"      {cyan('▸ ' + (ls.get('content') or '').strip())}")
        else:
            # Offline / no server LLM: store the lesson directly so day 2 still works.
            _mubit.store(client, sess, LESSON, intent="lesson", agent_id=AGENT_ID)
            print(f"    {dim('(reflection unavailable — stored the correction as a lesson directly)')}")

        # Make sure day 2 will have something to recall before we exit.
        _r, vtexts, _d, vok = _mubit.recall(client, sess, QUESTION)
        if vok and not vtexts:
            print(f"  {yellow('⚠ nothing is queryable yet — the write may not have landed.')}")
        print(f"  {dim('Process exits. The knowledge now lives in Mubit/RocksDB, not in RAM.')}")
        return

    # ── Day 2: brand-new process ───────────────────────────────────────────
    print(f"  Customer ({CUSTOMER}): {white(QUESTION)}")
    print(f"  {dim('Recalling from Mubit (scoped to this thread)…')}")
    res, texts, dt_ms, ok = _mubit.recall(client, sess, QUESTION)

    if not ok:
        print(f"  {red('✗ could not reach Mubit to recall: %s' % res.get('_error', 'unknown'))}")
        print(f"  {yellow('Check the server + embedder are up (see README troubleshooting).')}")
        _write_status(2, "ERR")
        return

    # Show the policy-bearing memories (the fact + the reflected rule); the raw
    # failure-trace stays stored for reflection but isn't a "recalled answer".
    policy_texts = [t for t in texts if any(k.lower() in t.lower() for k in EXPECTED_KEYWORDS)] or texts
    print(f"  {cyan(bold('Mubit recalled %d grounded memories:' % len(policy_texts)))}")
    for i, t in enumerate(policy_texts[:3], 1):
        print(f"    {cyan('[mem %d]' % i)} {t}")
    print(f"  {dim('recall ~%.0f ms (control-plane query understanding; raw vector lookup is sub-10 ms)' % dt_ms)}")
    if _mubit.use_llm() and res.get("final_answer"):
        print(f"  {dim('server-synthesized answer (confidence=%s): %s' % (res.get('confidence'), (res.get('final_answer') or '')[:110]))}")

    memory_block = "\n".join(policy_texts[:3])
    answer, label = respond(QUESTION, memory_block=memory_block)
    frac, found = score_answer(answer)
    v = verdict(frac)
    print()
    print(f"  Agent {dim('(' + backend_note(label) + ')')}: {white(answer)}")
    print(f"  {verdict_label(frac, found)}")
    _write_status(2, v)
    print()
    if v == "PASS":
        print(f"  {green(bold('Correct — because it remembered.'))} Same agent, same question as the no-memory run.")
        print(f"  {dim('The only difference is Mubit: it recalled the policy it learned yesterday, in a brand-new process.')}")
    else:
        print(f"  {yellow('Day 2 recalled %d memories, but the answer is still incomplete.' % len(texts))}")
        print(f"  {dim('If 0 were recalled, the day-1 write did not land — check the server/Redis and re-run.')}")


def main() -> None:
    args = sys.argv[1:]
    if "--day" in args:
        try:
            run_day(int(args[args.index("--day") + 1]))
        except Exception as e:  # never show a customer a traceback
            print(f"\n  {red('Demo step hit an error: %s: %s' % (type(e).__name__, str(e)[:160]))}")
            print(f"  {yellow('The server may be momentarily busy. Re-run, or check README troubleshooting.')}")
            sys.exit(1)
        return

    here = os.path.abspath(__file__)
    env = os.environ.copy()
    if not env.get("DEMO_SESSION"):
        env["DEMO_SESSION"] = "mubitdemo-%d" % int(time.time())  # fresh, shared by both child processes
    rc1 = subprocess.run([sys.executable, here, "--day", "1"], env=env).returncode
    time.sleep(0.4)
    print(cyan("\n        … one day later, a fresh process answers the same customer …"))
    time.sleep(0.4)
    rc2 = subprocess.run([sys.executable, here, "--day", "2"], env=env).returncode
    sys.exit(rc1 or rc2)


if __name__ == "__main__":
    main()
