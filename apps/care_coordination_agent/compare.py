"""Controlled three-arm behavioral comparison over the evaluation scenarios.

For each evaluation scenario (E1 combined coordination, E2 current preference
override) it runs completely fresh agent executions under three memory arms
derived from ONE real Mubit recall: no memory, the full frozen snapshot, and
the snapshot with only contact_strategy lessons removed. Every arm run is
read-only, starts from a hash-verified identical simulator state, uses the
same provider settings, and is scored deterministically from simulator state
and trace. No LLM judge, no memory writes, no fabricated wins: a single
stochastic sample per arm is evidence of behavior, not causal proof.
"""
import argparse
import hashlib
import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from agent import MAX_STEP_ATTEMPTS, SYSTEM_POLICY, run_encounter
from memory import Memory, MemoryError, missing_memory_config, normalize_for_prompt, snapshot_hash
from scenarios import PATIENT_ID, SCENARIOS
from simulator import ACTION_BUDGET, start_encounter

ARMS = ("no_memory", "full_memory", "ablated_contact")
EVALUATION_SCENARIOS = ("E1", "E2")
REQUIRED_CATEGORIES = ("contact_strategy", "communication_strategy", "coordination_sequence")
# The after-17:00 region is what the historical contact lesson taught; used
# only for report-side analysis of stale-strategy attempts, never in prompts.
STALE_EVENING_FROM = "17:00"
ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".demo"


class ComparisonInvalid(ValueError):
    """The comparison's controlled conditions cannot be guaranteed."""


def _minutes(hhmm):
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def starting_state_view(scenario_id):
    """Public, agent-visible starting configuration only; no hidden state."""
    probe = start_encounter(scenario_id, experiment_id="cc-start-probe",
                            execution_id="start-probe", encounter_id="start-probe")
    state = probe.visible_state()
    return {
        "scenario_id": scenario_id,
        "chart": probe.chart(),
        "objectives": state["objectives"],
        "action_budget": state["action_budget"],
        "status": state["status"],
        "actions_remaining": state["actions_remaining"],
        "action_history": state["action_history"],
        "observed_outcomes": state["observed_outcomes"],
    }


def starting_state_hash(view):
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()


def validate_evaluation_scenario(scenario_id):
    """Leakage audit: nothing from teaching, hidden state, or expectations."""
    problems = []
    scenario = SCENARIOS[scenario_id]
    blob = json.dumps(scenario["chart"])
    for banned in ("17:00", "answered_from", "answered_until", "patient_responds",
                   "understanding_requires", "booking_requires_arranged_ride",
                   "teaching", "morning-evening-1", "scripted correction", "GOAL_"):
        if banned in blob:
            problems.append(f"chart contains banned content: {banned!r}")
    if scenario.get("scripted_correction"):
        problems.append("evaluation scenario carries a scripted clinician correction")
    if scenario.get("initial_flags"):
        problems.append("evaluation scenario must start from a fresh state")
    if scenario["objectives"] != ["contact_established", "understanding_confirmed",
                                  "transportation_arranged", "follow_up_booked"]:
        problems.append("evaluation scenario must require the full coordination chain")
    return problems


def build_arms(recalled):
    """Derive the three arms from the single recalled base snapshot."""
    base = normalize_for_prompt(recalled)
    categories = {lesson["category"] for lesson in base}
    missing = [category for category in REQUIRED_CATEGORIES if category not in categories]
    if missing:
        raise ComparisonInvalid(f"base memory is missing required lesson categories: {missing}")
    removed = [lesson["id"] for lesson in base if lesson["category"] == "contact_strategy"]
    if not removed:
        raise ComparisonInvalid("no contact_strategy lesson available; ablation cannot be constructed")
    ablated = [lesson for lesson in base if lesson["category"] != "contact_strategy"]
    for category in ("communication_strategy", "coordination_sequence"):
        full_side = [lesson for lesson in base if lesson["category"] == category]
        ablated_side = [lesson for lesson in ablated if lesson["category"] == category]
        if full_side != ablated_side:
            raise ComparisonInvalid(f"{category} lessons differ between full and ablated arms")
    base_hash = snapshot_hash(base)
    return {
        "no_memory": {"lessons": [], "sha256": snapshot_hash([]), "source_base_sha256": base_hash},
        "full_memory": {"lessons": base, "sha256": base_hash, "source_base_sha256": base_hash},
        "ablated_contact": {"lessons": ablated, "sha256": snapshot_hash(ablated),
                            "source_base_sha256": base_hash,
                            "removed_contact_lesson_ids": removed},
    }


def score_run(run, start_view):
    """Deterministic metrics from simulator state and trace. No LLM judge."""
    state = run["final_visible_state"]
    outcomes = state["observed_outcomes"]
    events = run["trace"]["events"]
    chart = start_view["chart"]
    flags = {objective["objective"]: objective["met"] for objective in state["objectives"]}
    instruction = chart.get("patient", {}).get("current_contact_instruction")
    window = (instruction or {}).get("window")
    structured_id = chart["instructions"].get("current_approved_text_id")

    outreach = [(index, outcome) for index, outcome in enumerate(outcomes)
                if outcome["action"]["name"] == "outreach"]
    contact = next((outcome for _, outcome in outreach if outcome["status"] == "success"), None)
    contact_index = next((index for index, outcome in outreach if outcome["status"] == "success"), None)
    failed_before_contact = sum(1 for index, outcome in outreach
                                if outcome["status"] == "no_response"
                                and (contact_index is None or index < contact_index))
    phone_times = [outcome["action"]["local_time"] for _, outcome in outreach
                   if outcome["action"].get("channel") == "phone_call"]
    out_of_window = [time for time in phone_times if window and not
                     (_minutes(window["from"]) <= _minutes(time) <= _minutes(window["to"]))]
    stale_evening = [time for time in phone_times if _minutes(time) >= _minutes(STALE_EVENING_FROM)]
    transport_index = next((index for index, outcome in enumerate(outcomes)
                            if outcome["action"]["name"] == "arrange_transportation"
                            and outcome["status"] == "success"), None)
    booking_index = next((index for index, outcome in enumerate(outcomes)
                          if outcome["action"]["name"] == "book_follow_up"
                          and outcome["status"] == "success"), None)
    rejected = [event for event in events if event["type"] == "action_rejected"]
    usage = {"prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_complete": True}
    for event in events:
        if event["type"] != "model_decision":
            continue
        stats = event.get("usage") or {}
        for key in ("prompt_tokens", "output_tokens", "total_tokens"):
            if stats.get(key) is None:
                usage["usage_complete"] = False
            else:
                usage[key] += stats[key]
    return {
        "status": run["result"]["status"],
        "completed": run["result"]["status"] == "completed",
        "contact_established": flags.get("contact_established", False),
        "unsuccessful_outreach_before_contact": failed_before_contact,
        "first_contact": None if contact is None else {
            "channel": contact["action"]["channel"],
            "local_time": contact["action"].get("local_time")},
        "comprehension_confirmed": flags.get("understanding_confirmed", False),
        "failed_comprehension_checks": sum(
            1 for outcome in outcomes
            if outcome["action"]["name"] == "check_understanding"
            and outcome["status"] == "not_confirmed"),
        "current_approved_text_used": any(
            outcome["action"].get("text_id") == structured_id for outcome in outcomes
            if outcome["action"]["name"] == "explain_instructions"),
        "transportation_arranged": flags.get("transportation_arranged", False),
        "follow_up_booked": flags.get("follow_up_booked", False),
        "premature_booking_attempts": sum(
            1 for outcome in outcomes
            if outcome["action"]["name"] == "book_follow_up" and outcome["status"] == "unsuccessful"),
        "transport_confirmed_booking": (transport_index is not None and booking_index is not None
                                        and transport_index < booking_index),
        "premature_close_attempts": sum(
            1 for event in rejected
            if str(event.get("reason", "")).startswith("Encounter objectives not met")),
        "rejected_action_attempts": len(rejected),
        "retries": sum(1 for event in events if event["type"] == "invalid_decision"),
        "actions_used": run["result"]["actions_used"],
        "model_calls": run["result"]["model_calls"],
        "cited_lesson_ids": sorted({lesson_id for event in events if event["type"] == "agent_step"
                                    for lesson_id in event["recalled_lesson_ids"]}),
        "out_of_instruction_calls": out_of_window if window else [],
        "stale_evening_calls": stale_evening,
        "followed_current_instruction": (
            None if not window else
            contact is not None and contact["action"].get("channel") == "phone_call"
            and _minutes(window["from"]) <= _minutes(contact["action"]["local_time"]) <= _minutes(window["to"])),
        "memory_overrode_current_instruction": bool(window and stale_evening),
        "usage": usage,
    }


def rubric(metrics):
    """Ordered outcome vector. Efficiency never outranks coordination success."""
    if metrics["followed_current_instruction"] is not None:
        return (int(metrics["followed_current_instruction"]), int(metrics["completed"]),
                -len(metrics["stale_evening_calls"]), int(metrics["comprehension_confirmed"]),
                int(metrics["transport_confirmed_booking"]), -metrics["actions_used"])
    return (int(metrics["completed"]), int(metrics["comprehension_confirmed"]),
            int(metrics["transport_confirmed_booking"]),
            -metrics["unsuccessful_outreach_before_contact"],
            -metrics["premature_booking_attempts"], -metrics["actions_used"])


def verdict(full, other):
    full_rubric, other_rubric = rubric(full), rubric(other)
    if full_rubric > other_rubric:
        return "win"
    if full_rubric < other_rubric:
        return "regression"
    return "tie"


def verdict_reason(full, other):
    labels = (("completed", "completion"), ("comprehension_confirmed", "comprehension confirmed"),
              ("transport_confirmed_booking", "transport-confirmed booking"),
              ("unsuccessful_outreach_before_contact", "unsuccessful outreach before contact"),
              ("premature_booking_attempts", "premature booking attempts"),
              ("stale_evening_calls", "historical evening-strategy call attempts"),
              ("actions_used", "actions used"))
    differences = []
    for key, label in labels:
        a, b = full[key], other[key]
        a = len(a) if isinstance(a, list) else a
        b = len(b) if isinstance(b, list) else b
        if a != b:
            differences.append(f"{label}: full={a} vs other={b}")
    return "; ".join(differences) or "identical deterministic metrics"


def _outreach_sequence(run):
    return [[outcome["action"].get("channel"), outcome["action"].get("local_time")]
            for outcome in run["observed_outcomes"]
            if outcome["action"]["name"] == "outreach"]


def compare_scenario(scenario_id, runs):
    """Pairwise interpretation from per-run records (already scored)."""
    by_arm = {arm: [run for run in runs if run["arm"] == arm] for arm in ARMS}
    pairwise = {}
    for name, other_arm in (("full_vs_none", "no_memory"), ("full_vs_ablated", "ablated_contact")):
        per_repetition = []
        for full_run, other_run in zip(by_arm["full_memory"], by_arm[other_arm]):
            per_repetition.append({
                "repetition": full_run["repetition"],
                "verdict": verdict(full_run["metrics"], other_run["metrics"]),
                "reason": verdict_reason(full_run["metrics"], other_run["metrics"]),
                "full_execution_id": full_run["execution_id"],
                "other_execution_id": other_run["execution_id"],
            })
        counts = {outcome: sum(1 for entry in per_repetition if entry["verdict"] == outcome)
                  for outcome in ("win", "tie", "regression")}
        overall = "win" if counts["win"] > counts["regression"] else (
            "regression" if counts["regression"] > counts["win"] else "tie")
        pairwise[name] = {"overall": overall, "counts": counts, "per_repetition": per_repetition}
    changed = any(_outreach_sequence(full) != _outreach_sequence(other)
                  for full, other in zip(by_arm["full_memory"], by_arm["ablated_contact"]))
    full_contact_cited = sorted({lesson_id for run in by_arm["full_memory"]
                                 for lesson_id in run["metrics"]["cited_lesson_ids"]})
    interpretation = {
        "contact_behavior_changed_by_ablation": changed,
        "full_arms_cited_any_lesson": bool(full_contact_cited),
        "note": ("Ablation changed contact behavior while all other inputs were identical is "
                 "evidence consistent with the contact lesson affecting policy; a citation "
                 "alone is not proof, and single stochastic samples are not causal proof."),
    }
    override = None
    if scenario_id == "E2":
        full_runs = by_arm["full_memory"]
        override_passed = all(run["metrics"]["followed_current_instruction"]
                              and not run["metrics"]["stale_evening_calls"] for run in full_runs)
        override = {
            "passed": override_passed,
            "reason": ("full-memory runs followed the current morning instruction with no "
                       "historical evening-strategy attempts" if override_passed else
                       "at least one full-memory run missed the current instruction or "
                       "applied the historical evening strategy against it"),
        }
    return {"pairwise": pairwise, "interpretation": interpretation,
            "current_instruction_override": override}


def run_comparison(provider_factory, memory, experiment, repetitions=1,
                   scenario_ids=EVALUATION_SCENARIOS, event_observer=None, trace_sink=None):
    timeline = []

    def note(kind, **data):
        timeline.append({"type": kind, **data})
        if event_observer is not None:
            event_observer({"scope": "comparison", "type": kind, **data})

    def run_observer(scenario_id, arm, repetition):
        if event_observer is None:
            return None

        def observe(event):
            event_observer({"scope": "run", "scenario_id": scenario_id, "arm": arm,
                            "repetition": repetition, **deepcopy(event)})

        return observe

    header = {
        "experiment_id": experiment,
        "patient_id": PATIENT_ID,
        "repetitions": repetitions,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "arms": list(ARMS),
        "action_budget": ACTION_BUDGET,
        "max_step_attempts": MAX_STEP_ATTEMPTS,
        "system_policy_sha256": hashlib.sha256(SYSTEM_POLICY.encode()).hexdigest(),
        "temperature": 0,
        "structured_output": "Decision JSON schema (response_json_schema)",
        "determinism_note": "Temperature 0 does not guarantee identical LLM outputs; "
                            "verdicts are single-sample behavioral evidence, not causal proof.",
    }
    try:
        recalled = memory.recall()  # The ONE recall for the entire comparison.
    except MemoryError as exc:
        return _invalid(header, timeline, f"Mubit recall failed: {exc}")
    note("base_memory_recalled", count=len(recalled),
         real_ids=sorted(str(lesson["id"]) for lesson in recalled))
    base = normalize_for_prompt(recalled)
    base_hash = snapshot_hash(base)
    note("base_memory_frozen", sha256=base_hash, lessons=base)
    try:
        arms = build_arms(recalled)
    except ComparisonInvalid as exc:
        return _invalid(header, timeline, str(exc))
    note("arms_derived", **{arm: {"sha256": spec["sha256"],
                                  "lesson_ids": [lesson["id"] for lesson in spec["lessons"]]}
                            for arm, spec in arms.items()})

    scenario_reports = []
    for scenario_id in scenario_ids:
        problems = validate_evaluation_scenario(scenario_id)
        if problems:
            return _invalid(header, timeline,
                            f"evaluation scenario {scenario_id} failed leakage validation: {problems}")
        view = starting_state_view(scenario_id)
        state_hash = starting_state_hash(view)
        runs = []
        for arm in ARMS:
            for repetition in range(1, repetitions + 1):
                if starting_state_hash(starting_state_view(scenario_id)) != state_hash:
                    return _invalid(header, timeline,
                                    f"starting state drifted for {scenario_id}/{arm}")
                execution = uuid.uuid4().hex
                note("arm_run_started", scenario_id=scenario_id, arm=arm,
                     repetition=repetition, execution_id=execution)
                provider = provider_factory(scenario_id, arm, repetition)
                model_name = getattr(provider, "model", None) or type(provider).__name__
                if header.setdefault("provider", model_name) != model_name:
                    return _invalid(header, timeline,
                                    "provider configuration differs between arms")
                try:
                    run = run_encounter(
                        provider, scenario_id, experiment_id=experiment, execution_id=execution,
                        encounter_id=f"enc-{execution[:12]}", memory=None, memory_mode="frozen",
                        frozen_lessons=arms[arm]["lessons"], run_mode="read_only",
                        metadata={"comparison": {
                            "arm": arm, "repetition": repetition, "scenario_id": scenario_id,
                            "base_memory_sha256": base_hash,
                            "arm_memory_sha256": arms[arm]["sha256"],
                            "starting_state_sha256": state_hash}},
                        event_observer=run_observer(scenario_id, arm, repetition))
                finally:
                    if hasattr(provider, "close"):
                        try:
                            provider.close()
                        except Exception:
                            pass
                if trace_sink is not None:
                    trace_sink({"execution_id": execution, "scenario_id": scenario_id,
                                "arm": arm, "repetition": repetition,
                                "trace": run["trace"]})
                record = {
                    "execution_id": execution,
                    "arm": arm,
                    "repetition": repetition,
                    "model": model_name,
                    "status": run["result"]["status"],
                    "error": run["result"]["error"],
                    "starting_state_sha256": state_hash,
                    "memory_sha256": arms[arm]["sha256"],
                    "available_lesson_ids": [lesson["id"] for lesson in arms[arm]["lessons"]],
                    "actions": [outcome["action"] for outcome in run["final_visible_state"]["observed_outcomes"]],
                    "observed_outcomes": run["final_visible_state"]["observed_outcomes"],
                }
                record["metrics"] = score_run(run, view)
                record["cited_lesson_ids"] = record["metrics"]["cited_lesson_ids"]
                runs.append(record)
                note("arm_run_finished", scenario_id=scenario_id, arm=arm,
                     repetition=repetition, execution_id=execution,
                     status=record["status"])
        scenario_reports.append({
            "scenario_id": scenario_id,
            "title": SCENARIOS[scenario_id]["title"],
            "starting_state_sha256": state_hash,
            "action_budget": ACTION_BUDGET,
            "current_chart_constraints": {
                "transportation_required": bool(view["chart"].get("follow_up", {})
                                                .get("transportation", {}).get("required")),
                "current_approved_text_id": view["chart"]["instructions"]["current_approved_text_id"],
                "current_contact_instruction": (view["chart"].get("patient", {})
                                                .get("current_contact_instruction")),
            },
            "runs": runs,
            "comparison": compare_scenario(scenario_id, runs),
        })
    totals = {name: {"win": 0, "tie": 0, "regression": 0}
              for name in ("full_vs_none", "full_vs_ablated")}
    for report in scenario_reports:
        for name in totals:
            totals[name][report["comparison"]["pairwise"][name]["overall"]] += 1
    return {
        "status": "completed",
        **header,
        "base_memory": {"sha256": base_hash, "lessons": base,
                        "categories": sorted({lesson["category"] for lesson in base}),
                        "ablated_contact_lesson_ids": arms["ablated_contact"]["removed_contact_lesson_ids"]},
        "arm_definitions": {arm: {"lesson_ids": [lesson["id"] for lesson in spec["lessons"]],
                                  "sha256": spec["sha256"],
                                  "source_base_sha256": spec["source_base_sha256"]}
                            for arm, spec in arms.items()},
        "scenarios": scenario_reports,
        "pairwise_totals": totals,
        "timeline": timeline,
    }


def _invalid(header, timeline, reason):
    return {"status": "invalid", "reason": reason, "timeline": timeline, **header}


def print_report(report):
    if report["status"] != "completed":
        print(f"comparison INVALID: {report['reason']}")
        return
    print(f"experiment {report['experiment_id']} | patient {report['patient_id']} | "
          f"model {report['provider']} | temperature {report['temperature']} | "
          f"repetitions {report['repetitions']}")
    print(f"base memory sha256 {report['base_memory']['sha256'][:16]}… | "
          f"categories {', '.join(report['base_memory']['categories'])} | "
          f"ablated contact lesson(s) {', '.join(report['base_memory']['ablated_contact_lesson_ids'])}")
    for scenario in report["scenarios"]:
        print(f"\n{scenario['scenario_id']} {scenario['title']} "
              f"(start sha256 {scenario['starting_state_sha256'][:12]}…)")
        has_instruction = bool(scenario["current_chart_constraints"]["current_contact_instruction"])
        if has_instruction:
            print("  arm              followed-instr  complete  stale-evening  understanding  "
                  "T-booking  actions  status")
        else:
            print("  arm              complete  failed-outreach  understanding  "
                  "premature-booking  T-booking  actions  status")
        for run in scenario["runs"]:
            metrics = run["metrics"]
            if has_instruction:
                followed = "-" if metrics["followed_current_instruction"] is None else \
                    int(metrics["followed_current_instruction"])
                row = (f"  {run['arm']:<16} {followed:>14}  {int(metrics['completed']):>8}  "
                       f"{len(metrics['stale_evening_calls']):>13}  {int(metrics['comprehension_confirmed']):>13}  "
                       f"{int(metrics['transport_confirmed_booking']):>9}  {metrics['actions_used']:>6}  {run['status']}")
            else:
                row = (f"  {run['arm']:<16} {int(metrics['completed']):>8}  "
                       f"{metrics['unsuccessful_outreach_before_contact']:>15}  "
                       f"{int(metrics['comprehension_confirmed']):>13}  "
                       f"{metrics['premature_booking_attempts']:>16}  "
                       f"{int(metrics['transport_confirmed_booking']):>9}  {metrics['actions_used']:>6}  {run['status']}")
            print(row + (f"  rep{run['repetition']}" if report["repetitions"] > 1 else ""))
        comparison = scenario["comparison"]
        for name, label in (("full_vs_none", "Full vs None"), ("full_vs_ablated", "Full vs Ablated")):
            entry = comparison["pairwise"][name]
            print(f"  {label}: {entry['overall'].upper()}")
            for repetition in entry["per_repetition"]:
                print(f"    rep {repetition['repetition']}: {repetition['verdict']} — {repetition['reason']}")
        if comparison["current_instruction_override"]:
            override = comparison["current_instruction_override"]
            print(f"  Current instruction override: {'PASS' if override['passed'] else 'FAIL'} — "
                  f"{override['reason']}")
        print(f"  ablation changed contact behavior: "
              f"{comparison['interpretation']['contact_behavior_changed_by_ablation']}")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--scenarios", default=",".join(EVALUATION_SCENARIOS))
    args = parser.parse_args(argv)
    missing = missing_memory_config() + [key for key in ("GEMINI_API_KEY",)
                                         if not os.getenv(key, "").strip()]
    if missing:
        print(f"Missing configuration: {', '.join(missing)}. No simulated fallback is used.")
        return 2

    from agent import Gemini
    memory = Memory(args.experiment)
    scenario_ids = tuple(item.strip() for item in args.scenarios.split(",") if item.strip())

    def provider_factory(scenario_id, arm, repetition):
        return Gemini()

    try:
        report = run_comparison(provider_factory, memory, args.experiment,
                                repetitions=max(1, args.repetitions), scenario_ids=scenario_ids)
    finally:
        memory.close()
    print_report(report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = DATA / "comparisons" / f"{args.experiment}-r{max(1, args.repetitions)}-{stamp}.json"
    save(path, report)
    print(f"\nreport: {path}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
