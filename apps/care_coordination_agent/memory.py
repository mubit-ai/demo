"""Real Mubit persistence for validated operational lessons. No local fallback.

Mirrors apps/supply_chain_agent conventions: one Mubit run per experiment
(namespaced by a memory version), remember(wait=True) with an upsert key,
recall restricted to lesson evidence, and cross-experiment isolation enforced
by a content marker plus strict re-validation of everything parsed. Lessons
are experience-derived operational guidance; chart facts are never written
here. Failures raise MemoryError and are surfaced by the caller — there is no
JSON/file fallback and no manufactured reference ID.
"""
import hashlib
import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scenarios import PATIENT_ID

MEMORY_VERSION = "cc-memory-1"
AGENT_ID = "care-coordination-agent"
CATEGORIES = ("contact_strategy", "communication_strategy", "coordination_sequence")
EVIDENCE_TYPES = ("observed_outcome", "clinician_correction")
PROMPT_FIELDS = ("id", "category", "applicability", "guidance", "evidence_summary")


class MemoryError(ValueError):
    """Real Mubit operation failed. Surfaced explicitly; never a silent fallback."""


class Lesson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["contact_strategy", "communication_strategy", "coordination_sequence"]
    patient_id: str = Field(min_length=1, max_length=40)
    applicability: str = Field(min_length=1, max_length=400)
    guidance: str = Field(min_length=1, max_length=800)
    evidence_type: Literal["observed_outcome", "clinician_correction"]
    evidence_summary: str = Field(min_length=1, max_length=800)


def missing_memory_config():
    return [key for key in ("MUBIT_ENDPOINT", "MUBIT_API_KEY") if not os.getenv(key, "").strip()]


def normalize_for_prompt(lessons):
    """Prompt-safe allowlist: provenance and internals stay out of model input."""
    visible = [{key: lesson[key] for key in PROMPT_FIELDS} for lesson in lessons]
    return sorted(visible, key=lambda lesson: (lesson["category"], lesson["id"]))


def snapshot_hash(normalized):
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


def redact(message):
    for key in ("MUBIT_API_KEY", "GEMINI_API_KEY"):
        value = os.getenv(key, "")
        if value and len(value) >= 8:
            message = message.replace(value, "[redacted]")
    return message


class Memory:
    def __init__(self, experiment):
        from mubit import Client
        self.experiment = experiment
        self.run_id = f"{experiment}-{MEMORY_VERSION}"
        self.client = Client(endpoint=os.environ["MUBIT_ENDPOINT"],
                             api_key=os.environ["MUBIT_API_KEY"], transport="http",
                             run_id=self.run_id, timeout_ms=60000)

    def close(self):
        if hasattr(self.client, "close"):
            try:
                self.client.close()
            except Exception:
                pass

    def _marker(self):
        return f"[experiment:{self.experiment}] [demo:{MEMORY_VERSION}]\n"

    def recall(self, patient_id=PATIENT_ID):
        result = self.client.recall(
            query=f"care coordination operational lessons for synthetic patient {patient_id}",
            limit=10, entry_types=["lesson"], evidence_only=True,
            include_working_memory=False, include_linked_runs=False,
            prefer_current_run=True)
        if result.get("error"):
            raise MemoryError(redact(f"Mubit recall failed: {result['error']}"))
        marker = self._marker()
        lessons = []
        for entry in result.get("evidence") or []:
            content = entry.get("content") or entry.get("text") or ""
            if not content.startswith(marker) or not entry.get("id"):
                continue
            try:
                record = json.loads(content[len(marker):])
                lesson = Lesson.model_validate(record["lesson"])
            except (ValueError, KeyError, TypeError):
                continue
            if record.get("experiment_id") != self.experiment:
                continue
            if record.get("patient_id") != patient_id or lesson.patient_id != patient_id:
                continue
            if lesson.category not in CATEGORIES or lesson.evidence_type not in EVIDENCE_TYPES:
                continue
            lessons.append({"id": str(entry["id"]), **lesson.model_dump(),
                            "experiment_id": record["experiment_id"],
                            "source_encounter_id": record.get("source_encounter_id"),
                            "source_execution_id": record.get("source_execution_id"),
                            "upsert_key": record.get("upsert_key")})
        return lessons

    def remember(self, candidate):
        """Persist one validated candidate. Returns the real Mubit reference ID."""
        lesson = Lesson.model_validate(candidate["lesson"])
        if candidate["experiment_id"] != self.experiment:
            raise MemoryError("Candidate experiment does not match the memory scope")
        if lesson.patient_id != candidate.get("patient_id"):
            raise MemoryError("Candidate patient does not match its lesson")
        upsert_key = f"{MEMORY_VERSION}:{self.experiment}:{lesson.patient_id}:{lesson.category}"
        content = (self._marker() + json.dumps(
            dict(experiment_id=self.experiment, patient_id=lesson.patient_id,
                 source_encounter_id=candidate["source_encounter_id"],
                 source_execution_id=candidate["source_execution_id"],
                 upsert_key=upsert_key, lesson=lesson.model_dump()), sort_keys=True))
        try:
            stored = self.client.remember(
                content=content, intent="lesson",
                lesson_type="success" if lesson.evidence_type == "clinician_correction" else "failure",
                lesson_scope="run", lesson_importance="high", agent_id=AGENT_ID,
                upsert_key=upsert_key,
                item_id=f"{candidate['source_execution_id']}-{candidate['source_encounter_id']}",
                wait=True, timeout_ms=60000,
                metadata=dict(experiment=self.experiment, patient_id=lesson.patient_id,
                              category=lesson.category, evidence_type=lesson.evidence_type,
                              source_encounter_id=candidate["source_encounter_id"],
                              source_execution_id=candidate["source_execution_id"],
                              demo_version=MEMORY_VERSION, upsert_key=upsert_key))
        except Exception as exc:
            raise MemoryError(redact(f"Mubit remember failed: {type(exc).__name__}: {exc}")) from None
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise MemoryError(redact(f"Mubit did not finish ingesting the lesson: {stored}"))
        return self._reference_id_for(lesson)

    def _reference_id_for(self, lesson):
        """The real entry ID comes from Mubit itself, via recall, never fabricated."""
        expected = f"{MEMORY_VERSION}:{self.experiment}:{lesson.patient_id}:{lesson.category}"
        for found in self.recall(lesson.patient_id):
            if found.get("upsert_key") == expected:
                return found["id"]
        raise MemoryError("Stored lesson is not yet retrievable from Mubit; no reference ID is fabricated")

    def record(self, reference_id, succeeded, rationale):
        self.client.record_outcome(
            reference_id=reference_id,
            outcome="success" if succeeded else "failure",
            signal=1.0 if succeeded else 0.0, rationale=rationale,
            verified_in_production=False)
