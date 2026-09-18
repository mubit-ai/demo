"""Structured trace foundation. Events are agent-visible audit records only.

The audit stream is shared: simulator events (chart_snapshot, initial_state,
action_*, outcome, state_transition, clinician_correction, encounter_*) and
agent-loop events (model_call, model_decision, invalid_decision,
provider_error, agent_step, agent_error, agent_run_finished) interleave in
order. An optional observer lets the demo server stream these events live
without duplicating any logic. Hidden simulator state is never written here.
Offline tests that need ground truth use Encounter.debug_hidden_state(),
which stays separate from anything that could later be inserted into model
prompts.
"""
from copy import deepcopy


class Trace:
    def __init__(self, execution_metadata, observer=None):
        self.execution = deepcopy(execution_metadata)
        self.events = []
        self._observer = observer

    def emit(self, kind, **data):
        event = {"seq": len(self.events) + 1, "type": kind, **deepcopy(data)}
        self.events.append(event)
        if self._observer is not None:
            self._observer(event)
        return event

    def to_dict(self):
        return {"execution": deepcopy(self.execution), "events": deepcopy(self.events)}
