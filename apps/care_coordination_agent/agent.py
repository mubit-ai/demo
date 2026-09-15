"""One agent, one bounded loop, optional real Mubit memory.

Each step sends only agent-visible information (chart, visible encounter
state, action catalog, this encounter's outcomes, remaining budget, and —
only in memory=full mode — one frozen snapshot of recalled operational
lessons) to the provider and expects one structured decision. The decision is
schema- and argument-validated, then executed through the simulator's own
validation. Malformed decisions and simulator rejections are retried a
bounded number of times per step; provider failures fail the run explicitly.
Hidden simulator state is never read here and never enters prompts.

Memory lifecycle: read_only runs perform zero writes; teaching runs derive
lesson candidates deterministically from observed outcomes and the scripted
clinician correction, validate them, and persist them through the real Mubit
client in memory.py. Nothing fabricates memory IDs or successful outcomes.
"""
import json
import os
import time
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lessons import derive_lesson_candidates, validate_candidate
from memory import MemoryError, normalize_for_prompt, redact, snapshot_hash
from simulator import ACTION_BUDGET, ACTION_SPECS, ActionError, start_encounter

MAX_STEP_ATTEMPTS = 3
MEMORY_MODES = ("none", "full", "frozen")
RUN_MODES = ("teaching", "read_only")
ARGUMENT_NAMES = {spec["name"]: set(spec["params"]) for spec in ACTION_SPECS}
ACTION_NAMES = tuple(ARGUMENT_NAMES)

SYSTEM_POLICY = (
    "You coordinate care for one synthetic patient inside a deterministic training "
    "simulator. All chart data, clinicians, and clinical content are fictional; this is "
    "care coordination, not medical diagnosis, and you cannot modify, invent, or "
    "interpret treatment. You may explain only instruction texts already approved in "
    "the chart, referenced by text_id. Order of authority: the current chart and any "
    "current explicit instructions are authoritative and override conflicting "
    "historical lessons; current encounter observations come next; any supplied "
    "recalled_operational_lessons are conditional historical guidance from earlier "
    "encounters and rank last. Use a recalled lesson only when its applicability "
    "matches the current situation, never against newer chart facts, and cite in "
    "recalled_lesson_ids exactly the lesson IDs you actually relied upon; citing is a "
    "claim of influence, not proof, and inventing IDs is forbidden. Complete the "
    "encounter's coordination objectives safely, using each action's observed outcome "
    f"to decide the next step. You have at most {ACTION_BUDGET} executed actions per "
    "encounter; actions the simulator rejects do not consume budget. Return exactly "
    "one action with its exact chart-grounded arguments and a concise rationale. When "
    "no recalled_operational_lessons are supplied, recalled_lesson_ids must be empty."
)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "outreach", "explain_instructions", "check_understanding",
        "arrange_transportation", "book_follow_up", "close_encounter", "escalate"]
    arguments: dict[str, str]
    rationale: str = Field(min_length=1, max_length=2000)
    recalled_lesson_ids: list[str]


class ProviderError(ValueError):
    """Fatal provider/transport failure. The run stops and is reported failed."""


class StructuredOutputError(ValueError):
    """Retryable failure: the response is not valid structured output."""


class Gemini:
    """Same provider pattern as apps/supply_chain_agent: one generate_content call
    per decision, temperature 0, JSON schema response, redacted API errors."""

    def __init__(self):
        from google import genai
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                                   http_options={"timeout": 90000})

    def generate(self, system, payload, schema):
        started = time.monotonic()
        from google.genai.errors import APIError
        try:
            response = self.client.models.generate_content(
                model=self.model, contents=json.dumps(payload, sort_keys=True),
                config={"system_instruction": system, "temperature": 0,
                        "response_mime_type": "application/json",
                        "response_json_schema": schema.model_json_schema(),
                        "automatic_function_calling": {"disable": True},
                },
            )
        except APIError as exc:
            message = f"Gemini HTTP {exc.code}: {exc.message}"
            for key in ("GEMINI_API_KEY",):
                if os.getenv(key):
                    message = message.replace(os.environ[key], "[redacted]")
            raise ProviderError(message) from None
        raw = response.text or ""
        try:
            decision = schema.model_validate_json(raw)
        except ValueError as exc:
            raise StructuredOutputError(f"Response failed Decision schema validation: {exc}") from None
        usage = response.usage_metadata
        stats = dict(model=self.model, calls=1,
                     elapsed_seconds=round(time.monotonic() - started, 3),
                     prompt_tokens=getattr(usage, "prompt_token_count", None),
                     output_tokens=getattr(usage, "candidates_token_count", None),
                     total_tokens=getattr(usage, "total_token_count", None))
        return decision, stats, raw

    def close(self):
        self.client.close()


def decision_fault(decision, allowed_lesson_ids=None):
    """Decision checks beyond the schema. None when acceptable."""
    cited = decision.recalled_lesson_ids
    if allowed_lesson_ids is None:
        if cited != []:
            return ("recalled_lesson_ids must be empty in memory=none mode; no lessons "
                    "were supplied to the model")
    else:
        unknown = [lesson_id for lesson_id in cited if lesson_id not in allowed_lesson_ids]
        if unknown:
            return ("recalled_lesson_ids cites lessons that were not recalled in this "
                    f"execution: {unknown}")
    expected = ARGUMENT_NAMES.get(decision.action)
    if expected is None:
        return f"Unsupported action: {decision.action!r}. Supported: {', '.join(ACTION_NAMES)}"
    given = set(decision.arguments)
    if given != expected:
        return (f"Arguments for {decision.action} must be exactly {sorted(expected)}; "
                f"missing={sorted(expected - given)}, unexpected={sorted(given - expected)}")
    return None


def step_payload(encounter, step, attempt, attempt_failures, frozen_lessons=None):
    """The exact model input. Allowlist only; never hidden simulator state."""
    payload = {
        "chart": encounter.chart(),
        "visible_state": encounter.visible_state(),
        "available_actions": encounter.available_actions(),
        "step": step,
        "attempt": attempt,
        "attempt_failures": list(attempt_failures),
    }
    if frozen_lessons is not None:
        payload["recalled_operational_lessons"] = list(frozen_lessons)
    return payload


def run_encounter(provider, scenario_id, experiment_id=None, execution_id=None,
                  encounter_id=None, metadata=None, memory=None, memory_mode="none",
                  run_mode="read_only", frozen_lessons=None, event_observer=None):
    """Run one bounded agent loop. Returns the result, final state, and full trace.

    memory_mode="frozen" injects an externally prepared, already-recalled
    lesson snapshot (the comparison harness derives arms this way); it never
    touches Mubit itself. event_observer, when given, receives every trace
    event live so the demo server can stream progress.
    """
    if memory_mode not in MEMORY_MODES:
        raise ValueError(f"memory_mode must be one of {MEMORY_MODES}")
    if run_mode not in RUN_MODES:
        raise ValueError(f"run_mode must be one of {RUN_MODES}")
    if memory_mode == "full" and memory is None:
        raise ValueError("memory=full requires a Mubit memory client")
    if run_mode == "teaching" and memory is None:
        raise ValueError("teaching mode requires a Mubit memory client for lesson writes")
    if memory_mode == "frozen" and not isinstance(frozen_lessons, list):
        raise ValueError("memory=frozen requires an externally frozen lesson snapshot list")
    provider_name = getattr(provider, "model", None) or type(provider).__name__
    encounter = start_encounter(
        scenario_id, experiment_id=experiment_id, execution_id=execution_id,
        encounter_id=encounter_id,
        extra_metadata={"agent": {"provider": provider_name,
                                  "memory_mode": memory_mode, "run_mode": run_mode,
                                  "max_step_attempts": MAX_STEP_ATTEMPTS,
                                  "system_policy": SYSTEM_POLICY,
                                  "citation_semantics": "recalled_lesson_ids record "
                                  "claimed influence, not proven causality"},
                        **(metadata or {})},
        event_observer=event_observer)
    step = 0
    model_calls = 0
    error = None
    run_status = None
    frozen_digest = None
    allowed_lesson_ids = None

    if memory_mode == "full":
        run_id = getattr(memory, "run_id", None)
        encounter.record_event("memory_recall_started", memory_mode=memory_mode,
                               run_mode=run_mode, run_id=run_id,
                               patient_id=encounter.visible_state()["patient_id"],
                               query_scope=f"experiment/{memory.experiment} patient/{encounter.visible_state()['patient_id']}")
        try:
            recalled = memory.recall()
        except MemoryError as exc:
            encounter.record_event("memory_recall_failed", message=str(exc))
            error = str(exc)
            run_status = "failed"
        else:
            frozen_lessons = normalize_for_prompt(recalled)
    elif memory_mode == "frozen":
        frozen_lessons = normalize_for_prompt(frozen_lessons)
    if run_status != "failed" and memory_mode in ("full", "frozen"):
        frozen_digest = snapshot_hash(frozen_lessons)
        allowed_lesson_ids = {lesson["id"] for lesson in frozen_lessons}
        if memory_mode == "full":
            encounter.record_event("memory_recall_finished",
                                   returned_count=len(recalled), accepted_count=len(recalled),
                                   real_ids=sorted(allowed_lesson_ids))
        encounter.record_event("memory_snapshot_frozen", sha256=frozen_digest,
                               count=len(frozen_lessons), lessons=frozen_lessons,
                               source="mubit_recall" if memory_mode == "full" else "provided_snapshot",
                               note="Frozen for the whole execution; Mubit is not re-queried per action")

    if run_status != "failed":
        while True:
            state = encounter.visible_state()
            if state["status"] != "open":
                run_status = state["status"]
                break
            if state["actions_remaining"] == 0:
                encounter.finalize()
                run_status = "incomplete"
                break
            step += 1
            executed = False
            for attempt in range(1, MAX_STEP_ATTEMPTS + 1):
                payload = step_payload(encounter, step, attempt,
                                       _current_failures(encounter, step), frozen_lessons)
                encounter.record_event("model_call", step=step, attempt=attempt,
                                       provider=provider_name, prompt=payload,
                                       remaining_budget=state["actions_remaining"])
                model_calls += 1
                try:
                    decision, usage, raw = provider.generate(SYSTEM_POLICY, payload, Decision)
                except StructuredOutputError as exc:
                    _record_invalid(encounter, step, attempt, str(exc), None, None)
                    continue
                except Exception as exc:  # Provider/transport failure: fatal, never success.
                    error = str(exc) if isinstance(exc, ProviderError) else f"{type(exc).__name__}: {exc}"
                    encounter.record_event("provider_error", step=step, attempt=attempt, message=error)
                    run_status = "failed"
                    break
                encounter.record_event("model_decision", step=step, attempt=attempt,
                                       decision=decision.model_dump(), raw=raw, usage=usage)
                fault = decision_fault(decision, allowed_lesson_ids)
                if fault:
                    _record_invalid(encounter, step, attempt, fault, decision.model_dump(), None)
                    continue
                try:
                    outcome = encounter.act({"name": decision.action, **decision.arguments})
                except ActionError as exc:
                    _record_invalid(encounter, step, attempt,
                                    f"Simulator rejected the action: {exc}",
                                    decision.model_dump(), None)
                    continue
                encounter.record_event("agent_step", step=step, attempt=attempt,
                                       action=outcome["action"],
                                       rationale=decision.rationale,
                                       recalled_lesson_ids=decision.recalled_lesson_ids,
                                       outcome_status=outcome["status"],
                                       observation=outcome["observation"])
                executed = True
                break
            if run_status == "failed":
                break
            if not executed:
                last = _current_failures(encounter, step)
                error = (f"Structured-output failure: {MAX_STEP_ATTEMPTS} attempts at step "
                         f"{step} produced no valid action; last reason: {last[-1] if last else 'none'}")
                encounter.record_event("agent_error", step=step, message=error)
                run_status = "failed"
                break

    final = encounter.visible_state()
    if final["status"] == "open":
        final = encounter.finalize()

    lessons_derived = lessons_stored = 0
    reference_ids = []
    if run_mode == "teaching" and run_status != "failed":
        chart = encounter.chart()
        candidates = derive_lesson_candidates(final, chart)
        lessons_derived = len(candidates)
        for candidate in candidates:
            encounter.record_event("lesson_candidate", candidate=deepcopy(candidate))
            reason = validate_candidate(candidate, candidates)
            if reason is not None:
                encounter.record_event("lesson_validation_failed", reason=reason,
                                       category=candidate["lesson"]["category"])
                continue
            encounter.record_event("memory_write_started",
                                   category=candidate["lesson"]["category"],
                                   evidence_type=candidate["lesson"]["evidence_type"],
                                   evidence_summary=candidate["evidence_summary"],
                                   source_encounter_id=candidate["source_encounter_id"],
                                   source_execution_id=candidate["source_execution_id"])
            try:
                reference = memory.remember(candidate)
            except MemoryError as exc:
                encounter.record_event("memory_write_failed", message=str(exc))
                error = str(exc)
                run_status = "failed"
                break
            reference_ids.append(reference)
            lessons_stored += 1
            encounter.record_event("memory_write_finished", reference_id=reference,
                                   category=candidate["lesson"]["category"],
                                   upsert_key=f"{candidate['lesson']['patient_id']}:{candidate['lesson']['category']}")
        if run_status != "failed":
            cited = []
            for event in encounter.trace()["events"]:
                if event["type"] == "agent_step":
                    cited.extend(lesson_id for lesson_id in event["recalled_lesson_ids"]
                                 if lesson_id not in cited)
            for lesson_id in cited:
                if not allowed_lesson_ids or lesson_id not in allowed_lesson_ids:
                    continue
                try:
                    memory.record(lesson_id, run_status == "completed",
                                  f"Teaching execution {encounter.execution_id} ended "
                                  f"{run_status} with this lesson cited")
                    encounter.record_event("memory_outcome_recorded", reference_id=lesson_id,
                                           outcome="success" if run_status == "completed" else "failure")
                except Exception as exc:
                    message = redact(f"record_outcome failed: {type(exc).__name__}: {exc}")
                    encounter.record_event("memory_write_failed", message=message)
                    error = message
                    run_status = "failed"
                    break

    encounter.record_event("agent_run_finished", status=run_status,
                           actions_used=final["actions_used"], model_calls=model_calls,
                           error=error, completion_reason=final["completion_reason"],
                           memory_mode=memory_mode, run_mode=run_mode,
                           frozen_memory_sha256=frozen_digest,
                           lessons_derived=lessons_derived, lessons_stored=lessons_stored,
                           reference_ids=reference_ids)
    result = {
        "status": run_status,
        "scenario_id": final["scenario_id"],
        "experiment_id": encounter.experiment_id,
        "execution_id": encounter.execution_id,
        "encounter_id": encounter.encounter_id,
        "patient_id": final["patient_id"],
        "actions_used": final["actions_used"],
        "model_calls": model_calls,
        "error": error,
        "completion_reason": final["completion_reason"],
        "memory_mode": memory_mode,
        "run_mode": run_mode,
        "frozen_memory_sha256": frozen_digest,
        "lessons_derived": lessons_derived,
        "lessons_stored": lessons_stored,
        "reference_ids": reference_ids,
    }
    return {"result": result, "final_visible_state": final, "trace": encounter.trace()}


def _current_failures(encounter, step):
    """Recover this step's earlier failure reasons for the retry prompt."""
    failures = []
    for event in encounter.trace()["events"]:
        if event["type"] == "invalid_decision" and event["step"] == step:
            failures.append(event["reason"])
    return failures


def _record_invalid(encounter, step, attempt, reason, decision, raw):
    encounter.record_event("invalid_decision", step=step, attempt=attempt,
                           reason=reason, decision=decision, raw=raw)
