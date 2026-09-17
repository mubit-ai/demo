"""Synthetic on-call triage; Mubit is the only runtime lesson store.

Implements the documented outcome-attribution loop end to end
(docs.mubit.ai: patterns/outcome-attribution-loop, concepts/learning-loop,
recipes/step-level-outcomes):

  recall() -> capture entry IDs -> act (probes + fix) -> objective verdict
  -> record_outcome(entry_ids=..., idempotency_key=...)
  -> record_step_outcome() per probe and per fix -> advanced.reflect(
     include_step_outcomes=True)

Teaching runs the loop twice: first pass observes and reflects (Mubit decides
the lessons), second pass applies them and attributes real verdicts to the
exact entries used. No preferred fix, rule, or hindsight directive is ever
authored by this app.
"""
import argparse
import json
import os
import re
import uuid
from pathlib import Path

SERVICES = ("checkout", "search", "payments", "email")
PROBES = ("recent_deploys", "dependency_health", "infra_capacity")
FIXES = ("rollback", "restart_dependency", "scale_out")
VERSION = "oncall-agent-v1"

# Ground truth belongs to the synthetic environment, never the agent input.
# The cold keyword baseline below is wrong for the three teach classes and
# right for the controls, so any lift must come from memory.
TRUTH = {
    ("checkout", "deploy_error"): dict(fix="restart_dependency", probe="dependency_health"),
    ("search", "latency"):        dict(fix="rollback",            probe="recent_deploys"),
    ("payments", "backlog"):      dict(fix="scale_out",           probe="infra_capacity"),
    ("email", "latency"):         dict(fix="scale_out",           probe="infra_capacity"),
    ("checkout", "timeout"):      dict(fix="restart_dependency",  probe="dependency_health"),
    ("payments", "deploy_error"): dict(fix="rollback",            probe="recent_deploys"),
}
BASELINE_PROBES = ["dependency_health", "infra_capacity"]  # alphabetical; misses deploy-first causes
BASELINE_FIX = {"deploy_error": "rollback", "latency": "scale_out",
                "backlog": "restart_dependency", "timeout": "restart_dependency"}

TRAIN = [
    ("T1", "checkout", "Error rate spiked on checkout right after this morning's deploy."),
    ("T2", "search", "Search latency is terrible since the last release went out."),
    ("T3", "payments", "Payments queue backlog is growing and batches are not draining."),
]
HELD_OUT = [
    ("H1", "checkout", "Checkout failures jumped when we shipped this morning's change."),
    ("H2", "search", "Search is painfully slow after yesterday's rollout."),
    ("H3", "payments", "The payments pipeline is backed up; overnight batches never drained."),
    ("C1", "email", "Email delivery latency is high tonight."),
    ("C2", "checkout", "Checkout requests keep timing out for some customers."),
    ("C3", "payments", "Payments 5xx errors climbed after the afternoon deploy."),
]


def symptom_class(text):
    t = text.lower()
    if "time out" in t or "timing out" in t or "timeout" in t:
        return "timeout"
    if "backlog" in t or "queue" in t or "drain" in t or "batch" in t:
        return "backlog"
    if "slow" in t or "latency" in t:
        return "latency"
    return "deploy_error"


def baseline_plan(description):
    return dict(probes=list(BASELINE_PROBES), fix=BASELINE_FIX[symptom_class(description)])


def decide(case, lessons, model=None):
    """The same cold policy in both arms; only recalled guidance differs."""
    baseline = baseline_plan(case[2])
    if not lessons:
        return dict(probes=baseline["probes"], fix=baseline["fix"], lesson_ids=[],
                    reason="baseline: no lessons", baseline_fix=baseline["fix"])
    from google.genai import types
    result = model.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
        contents=json.dumps(dict(incident=dict(service=case[1], description=case[2]),
                                 baseline=baseline, lessons=lessons)),
        config=types.GenerateContentConfig(
            system_instruction=(
                "You triage fictional production incidents for four services. Keep the supplied "
                "baseline plan unless a recalled lesson clearly supports different probes or a "
                "different fix for this incident. Interpret lessons across paraphrases; respect "
                "every limiting condition and do not broaden a lesson to incidents missing its "
                "distinguishing characteristics. Return probes (at most two, in order), fix (one "
                "action), lesson_ids (only supplied IDs actually used), and reason (brief). A fix "
                "that differs from the baseline must cite lessons. Do not generate new lessons."),
            temperature=0, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json",
            response_json_schema={"type": "object", "properties": {
                "probes": {"type": "array", "items": {"type": "string", "enum": list(PROBES)}, "maxItems": 2},
                "fix": {"type": "string", "enum": list(FIXES)},
                "lesson_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"}},
                "required": ["probes", "fix", "lesson_ids", "reason"], "additionalProperties": False}))
    decision = json.loads(result.text)
    if (not isinstance(decision.get("probes"), list) or len(decision["probes"]) > 2
            or not set(decision["probes"]) <= set(PROBES)
            or decision.get("fix") not in FIXES
            or not isinstance(decision.get("lesson_ids"), list)
            or not set(decision["lesson_ids"]) <= {l["id"] for l in lessons}
            or (decision["fix"] != baseline["fix"] and not decision["lesson_ids"])):
        raise ValueError("Triage plan invalid or an ungrounded fix override")
    return dict(**decision, baseline_fix=baseline["fix"])


def handle(case, decision):
    """Deterministic environment: probes reveal only what they would reveal."""
    truth = TRUTH[(case[1], symptom_class(case[2]))]
    steps = []
    for index, probe in enumerate(decision["probes"]):
        informative = probe == truth["probe"]
        steps.append(dict(
            step=f"probe/{index}", name=probe, ok=informative, inconclusive=not informative,
            directive_hint=None if informative else
            "this probe was uninformative for this incident; prefer probes that were "
            "informative in past incidents of this kind"))
    resolved = decision["fix"] == truth["fix"]
    steps.append(dict(
        step="fix", name=decision["fix"], ok=resolved,
        directive_hint=None if resolved else
        f"{decision['fix']} did not resolve this incident; reconsider the fixes that past "
        "incidents of this service used"))
    return dict(resolved=resolved,
                wasted_probes=sum(1 for s in steps[:-1] if s["inconclusive"]),
                total_probes=len(decision["probes"]), steps=steps)


class Memory:
    def __init__(self, client, experiment):
        self.client = client
        self.run_id = f"{VERSION}-{experiment}"

    def recall(self, description):
        result = self.client.recall(
            query=f"On-call triage lessons for: {description}",
            entry_types=["lesson"], limit=5, evidence_only=True,
            include_working_memory=False, include_linked_runs=False, prefer_current_run=True)
        if result.get("error"):
            raise RuntimeError("Mubit recall failed")
        lessons = []
        for entry in result.get("evidence") or []:
            content = entry.get("content") or entry.get("text") or ""
            if (not content or not entry.get("id") or entry.get("is_stale")
                    or entry.get("entry_type") != "lesson"
                    or entry.get("run_id", "").split("::")[-1] != self.run_id):
                continue
            metadata = json.loads(entry.get("metadata_json") or "{}")
            conditions = metadata.get("conditions", [])
            lessons.append(dict(id=str(entry["id"]), content=content, conditions=conditions))
        return lessons

    def attribute(self, decision, outcome, source):
        """Outcome attribution per the docs: primary reference + every contributing entry."""
        ids = decision["lesson_ids"]
        if not ids:
            return None
        ok = outcome["resolved"]
        return self.client.record_outcome(
            reference_id=ids[0],
            outcome="success" if ok else "failure",
            signal=1.0 if ok else -1.0,
            rationale=f"Synthetic {source}: fix {decision['fix']} "
                      f"{'resolved' if ok else 'did not resolve'} the incident",
            entry_ids=ids,                            # multi-entry attribution
            idempotency_key=f"{VERSION}:{source}",    # a retried call never double-counts
            verified_in_production=False)              # synthetic environment, not production

    def learn(self, case, decision, outcome, source):
        # Instrument observed facts. No preferred fix, rule, or hindsight directive is authored here.
        evidence = dict(source=source, incident=dict(case_id=case[0], service=case[1], description=case[2]),
                        decision=decision, outcome=outcome)
        result = self.client.remember(
            content="Observed fictional on-call triage attempt: " + json.dumps(evidence),
            intent="fact", agent_id="oncall", item_id=case[0], wait=True,
            metadata={"source": source, "service": case[1]}, timeout_ms=60000)
        if result.get("error") or result.get("status") in ("failed", "error"):
            raise RuntimeError("Mubit evidence ingestion failed")
        emit("evidence_stored", source=source, job_id=result.get("job_id"),
             record_ids=[w["record_id"] for t in result.get("traces", [])
                         for w in t.get("writes", []) if w.get("success")])
        # Dense per-step process rewards (docs: recipes/step-level-outcomes).
        for step in outcome["steps"]:
            recorded = self.client.record_step_outcome(
                step_id=f"{source}/{step['step']}", step_name=step["name"],
                outcome="success" if step["ok"] else ("neutral" if step.get("inconclusive") else "failure"),
                signal=1.0 if step["ok"] else (0.0 if step.get("inconclusive") else -1.0),
                rationale=json.dumps(dict(incident=case[2], observed_step=step)),
                directive_hint=step.get("directive_hint"),
                agent_id="oncall", metadata={"source": source, "service": case[1]})
            if not recorded.get("accepted"):
                raise RuntimeError("Mubit did not accept step outcome")
            emit("step_recorded", source=source, step=step["step"], result=recorded)

    def reflect(self):
        # SDK passthrough is required to include step outcomes (typed helper lacks the flag).
        result = self.client.advanced.reflect(dict(
            run_id=self.run_id, include_linked_runs=False, include_step_outcomes=True))
        emit("reflection", result=result)
        if (result.get("error") or result.get("degraded") or not result.get("lessons_stored")
                or not any(l.get("lesson_id") for l in result.get("lessons", []))):
            raise RuntimeError("Mubit produced no lessons; the learning loop did not close")
        return result


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def run_case(case, memory=None, learn=False, execution="evaluation", arm="ON", model=None):
    description = case[2]
    lessons = memory.recall(description) if memory else []
    decision = decide(case, lessons, model)
    emit("decision", case=case[0], arm=arm, service=case[1], request=description,
         lessons=lessons, **decision)
    # Only after the decision does the environment disclose what worked.
    outcome = handle(case, decision)
    emit("outcome", case=case[0], arm=arm, **{k: v for k, v in outcome.items() if k != "steps"},
         steps=outcome["steps"])
    if learn:
        memory.learn(case, decision, outcome, f"{execution}/{case[0]}")
        memory.attribute(decision, outcome, f"{execution}/{case[0]}")
    return decision, outcome


def compare(memory, model=None):
    # No writes, outcome reinforcement, or evaluation-label access in retrieval/triage.
    totals = {}
    for arm in ("OFF", "ON"):
        outcomes = [run_case(c, memory if arm == "ON" else None, arm=arm, model=model)[1] for c in HELD_OUT]
        totals[arm] = dict(
            resolved=sum(o["resolved"] for o in outcomes),
            wasted_probes=sum(o["wasted_probes"] for o in outcomes),
            total_probes=sum(o["total_probes"] for o in outcomes))
    emit("metrics", cases=len(HELD_OUT), **totals)
    if totals["ON"]["resolved"] <= totals["OFF"]["resolved"]:
        raise RuntimeError("No resolution improvement measured; run teach with the same experiment first")
    return totals


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("teach", "evaluate"))
    parser.add_argument("--experiment", required=True,
                        help="Reuse this ID across processes; use a new ID for a clean experiment")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.experiment):
        parser.error("experiment must be 1–80 letters, digits, underscores or hyphens")
    if any(not os.getenv(k) for k in ("MUBIT_ENDPOINT", "MUBIT_API_KEY", "GEMINI_API_KEY")):
        parser.error("Set MUBIT_ENDPOINT, MUBIT_API_KEY and GEMINI_API_KEY in .env. No local memory fallback.")

    from mubit import Client
    # A stable Mubit run scopes one experiment; execution IDs track process runs.
    client = Client(endpoint=os.environ["MUBIT_ENDPOINT"], api_key=os.environ["MUBIT_API_KEY"],
                    transport="http", run_id=f"{VERSION}-{args.experiment}", timeout_ms=120000)
    from google import genai
    model = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"timeout": 90000})
    memory = Memory(client, args.experiment)
    execution = uuid.uuid4().hex
    emit("start", backend="Mubit", experiment=args.experiment, execution=execution, phase=args.phase)
    if args.phase == "teach":
        # Pass 1: observe with the cold policy; Mubit reflects lessons from evidence.
        for case in TRAIN:
            run_case(case, memory, learn=True, execution=f"{execution}-observe", model=model)
        memory.reflect()
        # Pass 2: apply the lessons and attribute real verdicts to the entries used.
        for case in TRAIN:
            run_case(case, memory, learn=True, execution=f"{execution}-apply", model=model)
    else:
        compare(memory, model)


if __name__ == "__main__":
    main()
