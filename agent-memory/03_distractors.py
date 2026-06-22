#!/usr/bin/env python3
"""
OPTIONAL deep-cut — "a dict can't do this."

The 2-day demo proves memory + learning at N=1. A skeptic says "a dictionary
keyed on the question could do that." So here we make retrieval EARN it:

  • seed ~8 memories — most about unrelated topics, plus one OUTDATED policy that
    contradicts the current one,
  • then ask a PARAPHRASED question worded nothing like what's stored,
  • and show Mubit's semantic ranking surfaces the RIGHT, CURRENT policy on top.

A keyword/dict lookup cannot do this: the query doesn't contain the stored words.
Honors DEMO_SCENARIO (billing | fintech | …).

    python3 03_distractors.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(line_buffering=True)

try:
    import _mubit
    from mubit import Client  # noqa: F401
except ModuleNotFoundError:
    sys.stderr.write(
        "\n[setup] Mubit SDK not found. Install it:\n"
        "  pip install -e %s/sdk/python/mubit-sdk && pip install requests\n\n"
        % os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    sys.exit(2)

from _ui import banner, bold, cyan, dim, green, red, white, yellow
from scenario import (
    CORRECT_POLICY,
    DISTRACTORS,
    EXPECTED_KEYWORDS,
    OUTDATED_POLICY,
    PARAPHRASED_QUESTION,
    SCENARIO,
)

AGENT_ID = "demo-agent"


def main() -> None:
    if not DISTRACTORS:
        print("This scenario defines no distractors; nothing to demo.")
        return
    sess = os.environ.get("DEMO_SESSION", "mubitdemo-distractors-%d" % int(time.time()))
    client, sess = _mubit.connect(run_id=sess)
    banner(f"DEEP-CUT — semantic ranking vs a pile of distractors  ·  {SCENARIO}", f"session={sess}")

    seeds = [CORRECT_POLICY, OUTDATED_POLICY] + list(DISTRACTORS)
    seeds = [s for s in seeds if s]
    print(f"  {dim('Seeding %d memories: 1 current policy, 1 OUTDATED policy, %d unrelated topics…' % (len(seeds), len(DISTRACTORS)))}")
    stored = 0
    for text in seeds:
        ok, _err = _mubit.store(client, sess, text, intent="fact", agent_id=AGENT_ID)
        stored += 1 if ok else 0
    if stored < len(seeds):
        print(f"  {red('✗ only stored %d/%d memories — is the server/Redis healthy?' % (stored, len(seeds)))}")
        return
    print(f"    {green('✓')} stored {stored} memories")
    time.sleep(0.5)

    print()
    print(f"  {yellow('Paraphrased question (shares almost no words with anything stored):')}")
    print(f"    {white(PARAPHRASED_QUESTION)}")
    print(f"  {dim('Recalling…')}")
    _res, texts, dt_ms, ok = _mubit.recall(client, sess, PARAPHRASED_QUESTION, limit=5)
    if not ok or not texts:
        print(f"  {red('✗ recall returned nothing (server/embedder issue).')}")
        return

    print(f"  {cyan(bold('Top %d recalled, by semantic relevance:' % len(texts[:5])))}")
    for i, t in enumerate(texts[:5], 1):
        print(f"    {cyan('%d.' % i)} {t[:96]}")
    print(f"  {dim('recall ~%.0f ms' % dt_ms)}")

    top = texts[0].lower()
    hit_current = all(k.lower() in top for k in EXPECTED_KEYWORDS)  # only the current policy has them all
    print()
    if hit_current:
        print(f"  {green(bold('Mubit ranked the CURRENT policy first'))} — over an outdated, contradictory one")
        print(f"  {green('and over the unrelated memories — for a question worded nothing like what was stored.')}")
    else:
        print(f"  {yellow('Top result was not the current policy this run — semantic ranking can vary; re-run or inspect above.')}")
    print(f"  {dim('A keyword/dict lookup cannot do this: the query never uses the stored wording.')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  {red('Demo step hit an error: %s: %s' % (type(e).__name__, str(e)[:160]))}")
        sys.exit(1)
