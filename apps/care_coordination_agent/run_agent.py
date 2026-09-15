"""Run one agent encounter against the deterministic simulator, with optional
real Mubit memory.

Teaching:   .venv/bin/python run_agent.py --scenario T1 --mode teaching --memory none --experiment cc-x
Recall run: .venv/bin/python run_agent.py --scenario T1 --mode read-only --memory full --experiment cc-x
Inspect:    .venv/bin/python run_agent.py --inspect-memory --experiment cc-x

Results are whatever the model actually did; nothing here claims or fabricates
deterministic success for LLM runs or memory IDs. Requires GEMINI_API_KEY, and
MUBIT_ENDPOINT/MUBIT_API_KEY for any memory mode (see .env.example).
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from memory import Memory, missing_memory_config, normalize_for_prompt, snapshot_hash
from simulator import ACTION_BUDGET

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DATA = ROOT / ".demo"
SCENARIO_IDS = ("T1", "T2", "T3", "E1", "E2")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIO_IDS)
    parser.add_argument("--experiment", default=None,
                        help="experiment id (cc-...); default: a fresh one, printed")
    parser.add_argument("--execution-id", default=None, help="id; default: generated")
    parser.add_argument("--mode", default="read_only", choices=("teaching", "read_only"),
                        help="read_only performs zero memory writes (default)")
    parser.add_argument("--memory", default="none", choices=("none", "full"),
                        help="full recalls and freezes real Mubit lessons before action 1")
    parser.add_argument("--inspect-memory", action="store_true",
                        help="print recalled normalized lessons for the experiment; no agent run")
    parser.add_argument("--print-trace", action="store_true", help="also dump the JSON trace")
    args = parser.parse_args(argv)

    experiment = args.experiment or ("cc-" + uuid.uuid4().hex[:12])
    needs_memory = args.inspect_memory or args.memory == "full" or args.mode == "teaching"
    missing = [key for key in ("GEMINI_API_KEY",) if not os.getenv(key, "").strip()]
    if args.inspect_memory:
        missing = []
    elif args.scenario is None:
        parser.error("--scenario is required unless --inspect-memory is used")
    if needs_memory:
        missing += missing_memory_config()
    if missing:
        print(f"Missing configuration: {', '.join(missing)}. No simulated fallback is used.")
        return 2

    memory = None
    if needs_memory:
        memory = Memory(experiment)
    try:
        if args.inspect_memory:
            lessons = memory.recall()
            normalized = normalize_for_prompt(lessons)
            print(f"experiment {experiment} | run_id {memory.run_id} | "
                  f"{len(normalized)} lesson(s) | sha256 {snapshot_hash(normalized)}")
            for lesson in normalized:
                print(f"  [{lesson['category']}] {lesson['id']}\n"
                      f"    applicability: {lesson['applicability']}\n"
                      f"    guidance: {lesson['guidance']}\n"
                      f"    evidence: {lesson['evidence_summary']}")
            return 0

        from agent import Gemini, run_encounter
        execution = args.execution_id or uuid.uuid4().hex
        provider = Gemini()
        try:
            run = run_encounter(provider, args.scenario, experiment_id=experiment,
                                execution_id=execution, memory=memory,
                                memory_mode=args.memory, run_mode=args.mode,
                                metadata={"launcher": "run_agent.py",
                                          "launched_at": datetime.now(timezone.utc).isoformat()})
        finally:
            provider.close()
    finally:
        if memory is not None:
            memory.close()

    result = run["result"]
    print(f"scenario {result['scenario_id']} | experiment {result['experiment_id']} | "
          f"execution {result['execution_id']} | model {provider.model} | "
          f"mode {result['run_mode']} | memory {result['memory_mode']}")
    for event in run["trace"]["events"]:
        if event["type"] == "agent_step":
            print(f"  step {event['step']}: {event['action']['name']} "
                  f"{json.dumps({k: v for k, v in event['action'].items() if k != 'name'})} "
                  f"-> {event['outcome_status']}")
        elif event["type"] in ("invalid_decision", "provider_error", "agent_error",
                               "memory_recall_failed", "memory_write_failed",
                               "lesson_validation_failed"):
            print(f"  step {event.get('step', '-')} {event['type']}: "
                  f"{event.get('reason') or event.get('message')}")
        elif event["type"] == "memory_write_finished":
            print(f"  lesson stored: {event['category']} -> reference {event['reference_id']}")
        elif event["type"] == "memory_snapshot_frozen":
            print(f"  memory frozen: {event['count']} lesson(s), sha256 {event['sha256']}")
    print(f"result: {result['status']} | actions {result['actions_used']}/{ACTION_BUDGET} | "
          f"model calls {result['model_calls']} | error: {result['error'] or 'none'}")
    if result["completion_reason"]:
        print(f"reason: {result['completion_reason']}")
    if result["run_mode"] == "teaching":
        print(f"lessons: derived {result['lessons_derived']}, stored {result['lessons_stored']}"
              + (f", references {result['reference_ids']}" if result["reference_ids"] else ""))

    path = DATA / "runs" / (result["execution_id"] + ".json")
    save(path, run["trace"])
    print(f"trace: {path}")
    if args.print_trace:
        print(json.dumps(run["trace"], indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
