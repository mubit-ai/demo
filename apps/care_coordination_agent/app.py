"""Local single-user demo server. Run with one uvicorn worker.

Orchestrates the existing teaching and comparison code over real Mubit and
Gemini; the server never duplicates agent, memory, scoring, or comparison
logic and never substitutes fallback memory. One execution may be active at
a time. The only durable memory source is Mubit; .demo files are audit
artifacts (traces/reports) that remain downloadable, including partial ones
after failures.
"""
import asyncio
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent import Gemini, run_encounter
from compare import EVALUATION_SCENARIOS, run_comparison
from memory import Memory, MemoryError, missing_memory_config, normalize_for_prompt
from scenarios import SCENARIOS

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DATA = ROOT / ".demo"
DEMO_VERSION = "cc-web-1"
TEACHING_SEQUENCE = ("T1", "T2", "T3")

app = FastAPI(title="Care Coordination / Operational memory")
app.add_middleware(TrustedHostMiddleware,
                   allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
lock = threading.Lock()
active = None

UI_EVENT_ALLOWLIST = {
    "agent_step": ("step", "action", "outcome_status", "observation", "recalled_lesson_ids"),
    "clinician_correction": ("source", "verdict", "text"),
    "lesson_candidate": None,
    "lesson_validation_failed": ("reason", "category"),
    "memory_write_started": ("category", "evidence_type", "source_encounter_id"),
    "memory_write_finished": ("reference_id", "category"),
    "memory_write_failed": ("message",),
    "memory_outcome_recorded": ("reference_id", "outcome"),
    "memory_recall_failed": ("message",),
    "memory_snapshot_frozen": ("sha256", "count"),
    "invalid_decision": ("step", "attempt", "reason"),
    "provider_error": ("step", "message"),
    "agent_error": ("step", "message"),
    "encounter_completed": ("status", "reason"),
    "encounter_incomplete": ("status", "reason"),
    "encounter_escalated": ("status", "reason"),
    "agent_run_finished": ("status", "actions_used", "model_calls", "error",
                           "lessons_stored", "reference_ids"),
}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)


def close_quietly(client):
    if client is not None and hasattr(client, "close"):
        try:
            client.close()
        except Exception:
            pass


def sanitized_endpoint():
    raw = os.getenv("MUBIT_ENDPOINT", "").strip()
    if not raw:
        return None
    parts = urlsplit(raw if "//" in raw else "//" + raw, scheme="http")
    if not parts.hostname:
        return None
    netloc = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    return f"{parts.scheme}://{netloc}"


def missing_config():
    return missing_memory_config() + [key for key in ("GEMINI_API_KEY",)
                                      if not os.getenv(key, "").strip()]


def new_experiment_id():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"cc-demo-{stamp}-{uuid.uuid4().hex[:6]}"


def state():
    path = DATA / "state.json"
    if not path.exists():
        experiment = os.getenv("DEMO_EXPERIMENT") or new_experiment_id()
        ExperimentRequest.model_validate({"experiment": experiment})
        save(path, dict(experiment=experiment, demo_version=DEMO_VERSION))
    current = json.loads(path.read_text())
    if current.get("demo_version") != DEMO_VERSION:
        current["demo_version"] = DEMO_VERSION
        current.pop("last_teaching", None)
        current.pop("last_comparison", None)
        save(path, current)
    return current


def memory_status(experiment):
    """Live Mubit health and lesson summary. Never fabricated."""
    endpoint = sanitized_endpoint()
    if missing_memory_config():
        return {"endpoint": endpoint, "status": "unconfigured"}
    try:
        memory = Memory(experiment)
        try:
            lessons = memory.recall()
        finally:
            close_quietly(memory)
    except Exception as exc:
        message = str(exc)
        for key in ("MUBIT_API_KEY", "GEMINI_API_KEY"):
            value = os.getenv(key, "")
            if value and len(value) >= 8:
                message = message.replace(value, "[redacted]")
        return {"endpoint": endpoint, "status": "unavailable", "detail": message[:300]}
    normalized = normalize_for_prompt(lessons)
    return {"endpoint": endpoint, "status": "connected", "lesson_count": len(normalized),
            "categories": sorted({lesson["category"] for lesson in normalized})}


def trace_path(run_id):
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise HTTPException(404, "Run not found")
    return DATA / "runs" / (run_id + ".json")


def read_trace(run_id):
    path = trace_path(run_id)
    if not path.exists():
        raise HTTPException(404, "Run not found")
    trace = json.loads(path.read_text())
    if trace["status"] == "running" and active != run_id:
        trace["status"] = "interrupted"
    return trace


class ExperimentRequest(BaseModel):
    experiment: str | None = Field(default=None, pattern=r"^cc-[a-zA-Z0-9_-]{1,60}$")


class RunRequest(BaseModel):
    phase: Literal["teach", "compare"]
    repetitions: int = 1


@app.middleware("http")
async def same_origin(request: Request, call_next):
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/api/status")
def status():
    with lock:
        current = state()
    return dict(**current, active=active,
                model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
                memory=memory_status(current["experiment"]),
                missing=missing_config())


@app.post("/api/experiment")
def experiment(body: ExperimentRequest):
    with lock:
        if active:
            raise HTTPException(409, "Wait for the active run to finish")
        current = dict(experiment=body.experiment or new_experiment_id(),
                       demo_version=DEMO_VERSION)
        save(DATA / "state.json", current)
        return current


@app.get("/api/lessons")
def lessons():
    current = state()
    if missing_memory_config():
        raise HTTPException(503, "Mubit memory is not configured. No fallback memory is used.")
    try:
        memory = Memory(current["experiment"])
        try:
            recalled = memory.recall()
        finally:
            close_quietly(memory)
    except MemoryError as exc:
        raise HTTPException(503, f"Mubit memory unavailable — {exc}. No fallback memory was used.")
    normalized = normalize_for_prompt(recalled)
    return {"experiment": current["experiment"], "count": len(normalized), "lessons": normalized}


def emit(trace, kind, **data):
    event = {"id": len(trace["events"]) + 1, "type": kind, **data}
    trace["events"].append(event)
    save(trace_path(trace["id"]), trace)
    return event


def ui_observer(trace, context):
    """Translate raw encounter/comparison events into compact UI events.

    Prompts, raw responses, and chart dumps stay out of the live stream;
    they remain in the downloadable per-execution traces.
    """
    def observe(event):
        kind = event.get("type")
        scope = event.get("scope")
        if scope == "comparison":
            payload = _slim(event, ("sha256", "count", "real_ids", "scenario_id", "arm",
                                    "repetition", "execution_id", "status"))
            payload["event"] = kind
            emit(trace, "comparison_event", **payload)
            return
        fields = UI_EVENT_ALLOWLIST.get(kind)
        if fields is None and kind != "lesson_candidate":
            return
        payload = {"scenario_id": context.get("scenario_id"), "arm": context.get("arm"),
                   "repetition": context.get("repetition")}
        payload.update({key: event[key] for key in (fields or ()) if key in event})
        if kind == "lesson_candidate":
            candidate = event.get("candidate") or {}
            payload["category"] = candidate.get("lesson", {}).get("category")
            payload["evidence_summary"] = candidate.get("evidence_summary")
        emit(trace, kind, **payload)
    return observe


def _slim(event, keys):
    return {key: event[key] for key in keys if key in event}


def run_teaching(trace):
    experiment = trace["experiment"]
    memory = Memory(experiment)
    trace["encounters"] = []
    try:
        for scenario_id in TEACHING_SEQUENCE:
            execution = uuid.uuid4().hex
            emit(trace, "encounter_started", scenario_id=scenario_id,
                 title=SCENARIOS[scenario_id]["title"], execution_id=execution)
            provider = Gemini()
            try:
                run = run_encounter(
                    provider, scenario_id, experiment_id=experiment, execution_id=execution,
                    encounter_id=f"enc-{execution[:12]}", memory=memory,
                    memory_mode="none", run_mode="teaching",
                    metadata={"launcher": "app.py teaching"},
                    event_observer=ui_observer(trace, {"scenario_id": scenario_id}))
            finally:
                provider.close()
            result = run["result"]
            trace["encounters"].append({
                "scenario_id": scenario_id, "execution_id": execution,
                "status": result["status"], "actions_used": result["actions_used"],
                "lessons_stored": result["lessons_stored"],
                "reference_ids": result["reference_ids"],
                "completion_reason": result["completion_reason"],
                "error": result["error"],
                "trace": run["trace"]})
            emit(trace, "encounter_finished", scenario_id=scenario_id,
                 status=result["status"], actions_used=result["actions_used"],
                 lessons_stored=result["lessons_stored"],
                 reference_ids=result["reference_ids"],
                 completion_reason=result["completion_reason"], error=result["error"])
            save(trace_path(trace["id"]), trace)
            save(trace_path(execution), {"kind": "encounter",
                                         "experiment": experiment,
                                         "status": result["status"],
                                         "execution": run["trace"]["execution"],
                                         "events": run["trace"]["events"]})
            if result["status"] == "failed":
                raise ValueError(result["error"] or "execution failed")
            try:
                recalled = normalize_for_prompt(memory.recall())
            except MemoryError as exc:
                raise ValueError(f"Mubit recall after teaching failed: {exc}") from None
            emit(trace, "lessons_refreshed", count=len(recalled), lessons=recalled)
    finally:
        close_quietly(memory)


def run_compare(trace, repetitions):
    experiment = trace["experiment"]
    memory = Memory(experiment)

    def trace_sink(record):
        save(trace_path(record["execution_id"]),
             {"kind": "evaluation_run", "experiment": experiment,
              "scenario_id": record["scenario_id"], "arm": record["arm"],
              "repetition": record["repetition"], "trace": record["trace"]})

    try:
        report = run_comparison(lambda *args: Gemini(), memory, experiment,
                                repetitions=repetitions, scenario_ids=EVALUATION_SCENARIOS,
                                event_observer=ui_observer(trace, {}),
                                trace_sink=trace_sink)
    finally:
        close_quietly(memory)
    trace["report"] = report
    emit(trace, "comparison_finished", status=report["status"],
         reason=report.get("reason"),
         pairwise_totals=report.get("pairwise_totals"))
    if report["status"] != "completed":
        raise ValueError(report.get("reason", "comparison invalid"))


def execute(trace):
    global active
    try:
        if trace["phase"] == "teach":
            run_teaching(trace)
        else:
            run_compare(trace, trace.get("repetitions", 1))
        trace["status"] = "completed"
        emit(trace, "run_finished", phase=trace["phase"], status="completed")
    except Exception as exc:
        trace["status"] = "failed"
        message = str(exc) if isinstance(exc, ValueError) else (
            f"{type(exc).__name__}: execution stopped. Check credentials, model "
            "availability, and endpoint connectivity; no simulated fallback was used.")
        for key in ("GEMINI_API_KEY", "MUBIT_API_KEY"):
            value = os.getenv(key, "")
            if value and len(value) >= 8:
                message = message.replace(value, "[redacted]")
        emit(trace, "run_error", phase=trace["phase"], message=message[:500])
    finally:
        with lock:
            active = None


@app.post("/api/runs", status_code=202)
def start(body: RunRequest):
    global active
    with lock:
        if active:
            raise HTTPException(409, "A run is already active")
        missing = missing_config()
        if missing:
            raise HTTPException(503, "Missing configuration: " + ", ".join(missing))
        run_id = uuid.uuid4().hex
        current = state()
        trace = dict(id=run_id, kind=body.phase, phase=body.phase,
                     experiment=current["experiment"], repetitions=max(1, body.repetitions),
                     status="running", events=[],
                     created_at=datetime.now(timezone.utc).isoformat())
        save(trace_path(run_id), trace)
        key = "last_teaching" if body.phase == "teach" else "last_comparison"
        save(DATA / "state.json", {**current, key: run_id})
        active = run_id
        threading.Thread(target=execute, args=(trace,), daemon=True).start()
        return dict(id=run_id)


@app.get("/api/runs/{run_id}")
def get_trace(run_id: str):
    trace = read_trace(run_id)
    return JSONResponse(trace, headers={
        "Content-Disposition": f'attachment; filename="care-coordination-{run_id}.json"'})


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str, request: Request):
    read_trace(run_id)
    try:
        cursor = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        raise HTTPException(400, "Invalid event cursor")
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            trace = read_trace(run_id)
            for event in trace["events"][cursor:]:
                cursor = event["id"]
                yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
            if trace["status"] != "running":
                yield f"event: end\ndata: {json.dumps({'status': trace['status']})}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.3)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/scenarios")
def scenarios():
    return {"teaching": [{"id": s, "title": SCENARIOS[s]["title"]} for s in TEACHING_SEQUENCE],
            "evaluation": [{"id": s, "title": SCENARIOS[s]["title"]} for s in EVALUATION_SCENARIOS]}
