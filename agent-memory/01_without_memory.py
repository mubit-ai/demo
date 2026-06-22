#!/usr/bin/env python3
"""
BEFORE — the agent with no memory.

A stateless support agent answers the customer's question on two different
"days". There is NO memory layer. Day 2 runs as a brand-new OS process, so it
shares nothing with day 1 — and the agent fumbles the same way again. It never
learned. This is every stateless LLM agent in production today.

    python3 01_without_memory.py            # runs day 1, then day 2 (separate processes)
    python3 01_without_memory.py --day 1    # one day only (used by the orchestrator)
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(line_buffering=True)

from _ui import banner, bold, cyan, dim, red, verdict_label, white, yellow
from agent import backend, backend_note, respond
from scenario import CORRECT_POLICY, CUSTOMER, QUESTION, score_answer, verdict

WHO = "baseline"
STATE = os.path.join(os.environ.get("DEMO_STATE_DIR", tempfile.gettempdir()), "demo_status_%s" % WHO)


def _write_status(day, v):
    try:
        with open(STATE, "w" if day == 1 else "a") as f:
            f.write("day%d=%s\n" % (day, v))
    except OSError:
        pass


def run_day(day: int) -> None:
    banner(f"DAY {day}  —  Support agent WITHOUT memory", "pid=%d" % os.getpid())
    print(f"  Customer ({CUSTOMER}): {white(QUESTION)}")

    # The agent has no memory, ever. It answers from scratch every time.
    answer, label = respond(QUESTION)
    frac, found = score_answer(answer)
    print(f"  Agent {dim('(' + backend_note(label) + ')')}: {white(answer)}")
    print(f"  {verdict_label(frac, found)}")
    _write_status(day, verdict(frac))

    if day == 1:
        print()
        print(f"  {yellow('Supervisor corrects the agent:')}")
        print(f"    {CORRECT_POLICY}")
        print(f"  {red('There is no memory layer — when this process exits, the correction is lost.')}")
    else:
        print()
        lead = "Same wrong answer." if backend() == "compose" else "Still wrong."
        print(f"  {red(bold(lead))} A brand-new process; nothing carried over, nothing learned.")
        print(f"  {dim('Without a memory layer, the agent has no record of the earlier session.')}")


def main() -> None:
    args = sys.argv[1:]
    if "--day" in args:
        try:
            run_day(int(args[args.index("--day") + 1]))
        except Exception as e:  # never show a customer a traceback
            print(f"\n  {red('Demo step hit an error: %s: %s' % (type(e).__name__, str(e)[:160]))}")
            sys.exit(1)
        return

    here = os.path.abspath(__file__)
    env = os.environ.copy()
    rc1 = subprocess.run([sys.executable, here, "--day", "1"], env=env).returncode
    time.sleep(0.4)
    print(cyan("\n        … one day later, a fresh process answers the same customer …"))
    time.sleep(0.4)
    rc2 = subprocess.run([sys.executable, here, "--day", "2"], env=env).returncode
    sys.exit(rc1 or rc2)


if __name__ == "__main__":
    main()
