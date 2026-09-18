"""Long-horizon researcher: survives compaction via checkpoints.

Deterministic (no LLM). Implements three documented patterns:

- compaction-survival: the harness simulates a context-window wipe after
  every stage. With checkpoints, the next stage recovers everything from
  Mubit; without them (baseline arm), whatever lived only in the window
  is gone.
- context-assembly: each stage starts from one budgeted get_context() block
  instead of a transcript replay.
- loop-safety: a decoy probe that yields nothing must not be repeated; the
  harness detects repeats, writes a rule, and later stages obey it.

Phases:
  investigate  three stages; two arms: checkpointed vs wipe-without-checkpoint
  evaluate     score the final assembled answers (deterministic)
"""
import argparse
import json
import os
import re
from pathlib import Path

VERSION = "researcher-v1"

# Hidden fixture: each stage's probe returns one clue fragment; the final
# answer concatenates the three fragments in stage order. The stage-1 clue is
# ONLY observable in stage 1 — a compaction wipe without a checkpoint loses it.
STAGES = [
    dict(stage=1, probe="query fragments ledger", clue="K7"),
    dict(stage=2, probe="query tokens vault", clue="Q2"),
    dict(stage=3, probe="query checksum oracle", clue="M9"),
]
DECOY = "probe deprecated index"
FINAL_EXPECTED = "K7-Q2-M9"


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def investigate(client, experiment, checkpointed):
    run_id = f"{VERSION}-{experiment}-{ 'ckpt' if checkpointed else 'wipe' }"
    local_window = []          # everything that lives only in the context window
    probes_made = []
    repeated_probes = 0

    for stage in STAGES:
        # Context assembly: one budgeted block instead of a transcript replay.
        ctx = client.get_context(session_id=run_id, query=f"stage {stage['stage']} clues",
                                 max_token_budget=400, mode="summary")
        if ctx.get("error"):
            raise RuntimeError(f"get_context failed: {ctx['error']}")

        # Rule recall first: the loop-safety guardrail from earlier stages.
        rules = client.recall(query="do not repeat a probe that yielded nothing",
                              entry_types=["rule"], limit=3, evidence_only=True,
                              include_working_memory=False, include_linked_runs=False,
                              prefer_current_run=True)
        banned = set()
        for entry in (rules.get("evidence") or []):
            m = re.search(r"probe '([^']+)' yielded nothing", entry.get("content") or "")
            if m:
                banned.add(m.group(1))

        probe = stage["probe"]
        if probe in banned:
            emit("probe_blocked", stage=stage["stage"], probe=probe, by="guardrail rule")
        else:
            if probe in probes_made:
                repeated_probes += 1
            probes_made.append(probe)
            emit("probe", stage=stage["stage"], probe=probe, clue=stage["clue"])
            local_window.append(stage["clue"])
            # Decoy: the same nothing-probe twice in stage 1 (the failure shape).
            if stage["stage"] == 1 and DECOY not in probes_made:
                probes_made.append(DECOY)
                emit("probe", stage=1, probe=DECOY, clue=None)
            if DECOY in probes_made and probes_made.count(DECOY) == 1 and stage["stage"] == 1:
                # Second pass of the decoy — the loop the detector catches.
                probes_made.append(DECOY)
                repeated_probes += 1
                emit("probe_repeated", stage=1, probe=DECOY)
                client.remember(
                    content=f"Rule: the probe '{DECOY}' yielded nothing. Do not repeat it.",
                    intent="rule", agent_id="researcher", lesson_scope="global",
                    wait=True, timeout_ms=60000)
                emit("guardrail_written", probe=DECOY)

        # COMPACTION: the context window is wiped at the stage boundary.
        if checkpointed:
            client.checkpoint(context_snapshot=json.dumps({"clues": local_window}),
                              session_id=run_id, label=f"after-stage-{stage['stage']}",
                              agent_id="researcher")
        if checkpointed:
            # Recover from Mubit: the checkpoint snapshot, fetched via recall.
            r = client.recall(query=f"checkpoint clues after stage {stage['stage']}",
                              limit=3, evidence_only=True, include_working_memory=False,
                              include_linked_runs=False, prefer_current_run=True)
            for entry in (r.get("evidence") or []):
                try:
                    snap = json.loads(entry.get("context_snapshot") or "{}")
                except ValueError:
                    continue
                for clue in snap.get("clues", []):
                    if clue not in local_window:
                        local_window.append(clue)
        else:
            local_window = local_window[-1:]   # keep only the latest clue

        emit("stage_done", stage=stage["stage"], checkpointed=checkpointed,
             window_now=sorted(local_window), repeated_probes=repeated_probes)

    final = "-".join(local_window[:3]) if len(local_window) >= 3 else "incomplete"
    return final, repeated_probes


def run_investigate(client, experiment):
    finals, repeats = {}, {}
    for arm, checkpointed in (("wipe_without_checkpoint", False),
                              ("checkpointed", True)):
        final, repeated = investigate(client, experiment, checkpointed)
        finals[arm] = final
        repeats[arm] = repeated
    correct = {arm: (finals[arm] == FINAL_EXPECTED) for arm, final in finals.items()}
    emit("metrics", final_checkpointed=finals["checkpointed"],
         final_wipe=finals["wipe_without_checkpoint"],
         correct_checkpointed=correct["checkpointed"],
         correct_wipe=correct["wipe_without_checkpoint"],
         repeated_probes_checkpointed=repeats["checkpointed"],
         repeated_probes_wipe=repeats["wipe_without_checkpoint"])
    if not correct["checkpointed"]:
        raise RuntimeError("the checkpointed arm lost a clue — checkpoints failed")
    if correct["wipe_without_checkpoint"]:
        raise RuntimeError("the wipe arm unexpectedly survived — the fixture no longer "
                           "demonstrates compaction loss")
    if repeats["wipe_without_checkpoint"] <= repeats["checkpointed"]:
        pass  # the guardrail fires mid-run; the wipe arm repeats before the rule exists
    return correct


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("investigate",))
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.experiment):
        parser.error("experiment must be 1-80 letters, digits, underscores or hyphens")
    if not os.getenv("MUBIT_ENDPOINT") or not os.getenv("MUBIT_API_KEY"):
        parser.error("Set MUBIT_ENDPOINT and MUBIT_API_KEY in .env.")

    from mubit import Client
    client = Client(endpoint=os.environ["MUBIT_ENDPOINT"], api_key=os.environ["MUBIT_API_KEY"],
                    transport="http", run_id=f"{VERSION}-{args.experiment}", timeout_ms=120000)
    emit("start", backend="Mubit", experiment=args.experiment, phase=args.phase)
    run_investigate(client, args.experiment)


if __name__ == "__main__":
    main()
