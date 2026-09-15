"""Play the intended teaching paths against the three teaching encounters.

This is a smoke script for the deterministic simulator, not the agent loop or
comparison harness. T2 and T3 start with contact already established (visible
chart state), so each encounter teaches exactly one behavior. All content is
synthetic.
"""
import json
import sys

from simulator import ACTION_BUDGET, start_encounter

INTENDED_PATHS = {
    "T1": [
        {"name": "outreach", "channel": "sms", "local_time": "10:00"},
        {"name": "outreach", "channel": "phone_call", "local_time": "12:00"},
        {"name": "outreach", "channel": "phone_call", "local_time": "18:00"},
        {"name": "close_encounter"},
    ],
    "T2": [
        {"name": "explain_instructions", "text_id": "generic-1"},
        {"name": "check_understanding"},
        {"name": "explain_instructions", "text_id": "morning-evening-1"},
        {"name": "check_understanding"},
        {"name": "close_encounter"},
    ],
    "T3": [
        {"name": "book_follow_up", "slot": "2026-10-21T11:30"},
        {"name": "arrange_transportation"},
        {"name": "book_follow_up", "slot": "2026-10-21T11:30"},
        {"name": "close_encounter"},
    ],
}


def main(argv):
    show_trace = "--trace" in argv
    for scenario_id, script in INTENDED_PATHS.items():
        encounter = start_encounter(scenario_id, experiment_id="cc-smoke-run")
        for action in script:
            outcome = encounter.act(action)
            print(f"  {scenario_id} {outcome['action']['name']:>22} -> {outcome['status']}")
        state = encounter.visible_state()
        print(f"{scenario_id}: {state['status']} ({state['actions_used']}/{ACTION_BUDGET} actions) "
              f"- {state['completion_reason']}")
        if show_trace:
            print(json.dumps(encounter.trace(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
