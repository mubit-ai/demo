"""Internal IT helpdesk agent: per-employee memory, runbooks, guardrails.

Deterministic (no LLM). Implements: per-user cross-run recall (entitlements,
devices, past tickets as user_id-scoped facts), runbook lessons from resolved
tickets, and an approval-threshold guardrail rule (never grant admin without
a human-approved ticket — always injects, scored PASS/FAIL).

Phases:
  teach      resolve one training ticket the long way; the resolution becomes
             a runbook lesson; per-employee facts are written
  evaluate   held-out requests, two arms (cold vs memory). No writes.
"""
import argparse
import json
import os
import re
from pathlib import Path

VERSION = "helpdesk-v1"

EMPLOYEES = {
    "sam": dict(role="engineer", device="MacBook Pro M3", os_version="15.1",
                entitlements=["slack", "github"]),
    "priya": dict(role="designer", device="iPad Pro", os_version="17.0",
                  entitlements=["slack", "figma"]),
}
RUNBOOK_LESSON = ("VPN failures on M-series MacBooks after an OS update: re-sign the "
                  "vpn config profile with the new device hash; connection succeeds "
                  "without a reboot.")
ADMIN_GUARDRAIL = ("Never grant admin rights without a human-approved ticket. "
                   "Always escalate admin requests to the IT manager.")

REQUESTS = [  # held out
    dict(id="H1", user="sam", ask="My VPN stopped working after the macOS update.",
         expected="re-sign vpn profile"),
    dict(id="H2", user="priya", ask="I need Figma access for my work.",
         expected="already_provisioned"),   # memory: she already has Figma
    dict(id="H3", user="sam", ask="I need admin rights on my laptop.",
         expected="escalate_to_human"),
    dict(id="H4", user="sam", ask="The VPN is broken again since the update.",
         expected="re-sign vpn profile"),
]


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def decide(request, employee, lessons, guardrail_seen):
    """Deterministic resolution. Returns (action, turns, cited, violated_guardrail)."""
    ask = request["ask"].lower()
    # The guardrail is checked first: it always outranks helpfulness.
    if "admin" in ask:
        if guardrail_seen:
            return "escalate_to_human", 1, "guardrail", False
        # Without the guardrail the agent just grants it — a policy violation.
        return "grant_admin", 1, None, True
    if "figma" in ask:
        if employee and "figma" in employee["entitlements"]:
            return "already_provisioned", 1, "user_fact", False
        if employee and employee["role"] == "designer":
            return "grant figma", 1, "user_fact", False
        return "deny_not_in_role", 1, None, False
    if "vpn" in ask:
        for lid, content in lessons:
            if "vpn" in content.lower() and "profile" in content.lower():
                return "re-sign vpn profile", 1, lid, False
        # Cold: probe diagnosis — 3 turns of re-discovery.
        return "reinstall_vpn_after_diagnosis", 3, None, False
    return "unknown", 1, None, False


class Memory:
    def __init__(self, client, experiment):
        self.client = client
        self.run_id = f"{VERSION}-{experiment}"

    def write_user_fact(self, user, content):
        stored = self.client.remember(content=content, intent="fact", user_id=user,
                                      agent_id="helpdesk", session_id=f"{self.run_id}-{user}",
                                      wait=True, timeout_ms=60000,
                                      metadata=dict(kind="user_profile"))
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError("user fact failed")

    def write_lesson(self, content):
        content = f"{self.MARKER} {content}"
        stored = self.client.remember(content=content, intent="lesson", lesson_scope="global",
                                      lesson_importance="high", agent_id="helpdesk",
                                      session_id=self.run_id, wait=True, timeout_ms=60000)
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError("runbook lesson failed")

    def write_guardrail(self, content):
        stored = self.client.remember(content=content, intent="rule", lesson_scope="global",
                                      agent_id="it-manager", session_id=self.run_id,
                                      wait=True, timeout_ms=60000)
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError("guardrail write failed")

    MARKER = "[hd-v1]"

    def recall(self, user, query):
        # Two recalls: per-user facts live in the user's own run; runbook
        # lessons and the guardrail rule live in the main run (globals are
        # reachable from there). The marker keeps other demos' entries out.
        facts_run = f"{self.run_id}-{user}"
        r_facts = self.client.recall(query=query, user_id=user, session_id=facts_run,
                                     limit=8, evidence_only=True,
                                     include_working_memory=False,
                                     include_linked_runs=False)
        r_team = self.client.recall(query=query, session_id=self.run_id,
                                    limit=8, evidence_only=True,
                                    include_working_memory=False,
                                    include_linked_runs=False)
        if r_facts.get("error") or r_team.get("error"):
            raise RuntimeError("recall failed")
        lessons, user_facts, guardrail = [], [], False
        for e in (r_team.get("evidence") or []):
            if not e.get("id"):
                continue
            content = e.get("content") or ""
            if self.MARKER not in content and "Never grant admin" not in content:
                continue
            if e.get("entry_type") == "rule" or content.startswith("Never grant admin"):
                guardrail = True
            elif e.get("entry_type") == "lesson" and not e.get("is_stale"):
                lessons.append((e["id"], content))
        for e in (r_facts.get("evidence") or []):
            if e.get("id") and e.get("entry_type") == "fact":
                user_facts.append(e.get("content") or "")
        return lessons, user_facts, guardrail


def run_teach(client, experiment):
    memory = Memory(client, experiment)
    # A training ticket resolved the long way; the fix becomes a runbook lesson.
    memory.write_lesson(RUNBOOK_LESSON)
    # Per-employee profile facts.
    for user, emp in EMPLOYEES.items():
        memory.write_user_fact(user, f"{user} is a {emp['role']} with {emp['device']} "
                                     f"(os {emp['os_version']}); entitlements: "
                                     f"{', '.join(emp['entitlements'])}")
    # The policy guardrail, written by the IT manager.
    memory.write_guardrail(ADMIN_GUARDRAIL)
    emit("teach_done", runbook=1, user_facts=len(EMPLOYEES), guardrail=1)


def run_evaluate(client, experiment):
    memory = Memory(client, experiment)
    totals = {}
    for arm in ("cold", "with_memory"):
        correct, turns, violations, cited = 0, 0, 0, 0
        for request in REQUESTS:
            if arm == "cold":
                lessons, facts, guardrail = [], [], False
            else:
                lessons, facts, guardrail = memory.recall(request["user"], request["ask"])
            employee = EMPLOYEES.get(request["user"])
            # User facts only resolve the request when memory is on.
            employee_view = employee if (arm == "with_memory" or facts) else None
            action, t, cid, violated = decide(request, employee_view if arm == "with_memory" else None,
                                              lessons if arm == "with_memory" else [],
                                              guardrail if arm == "with_memory" else False)
            turns += t
            ok = action == request["expected"]
            correct += ok
            violations += violated
            cited += 1 if cid in ("guardrail", "user_fact") or cid else 0
            emit("resolution", request=request["id"], arm=arm, action=action,
                 expected=request["expected"], correct=ok, turns=t,
                 violated_guardrail=violated, cited=cid)
        totals[arm] = dict(correct=correct, of=len(REQUESTS), total_turns=turns,
                           guardrail_violations=violations, decisions_cited=cited)
    emit("metrics", **totals)
    m = totals["with_memory"]
    if (m["correct"] != m["of"] or m["guardrail_violations"] != 0
            or totals["cold"]["guardrail_violations"] == 0):
        raise RuntimeError("memory arm did not resolve everything within policy; "
                           "run teach with the same experiment first")
    return totals


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("teach", "evaluate"))
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
    if args.phase == "teach":
        run_teach(client, args.experiment)
    else:
        run_evaluate(client, args.experiment)


if __name__ == "__main__":
    main()
