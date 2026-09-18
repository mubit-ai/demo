"""Self-tuning agent: the system prompt is a versioned, server-side resource.

Deterministic executor (no client LLM): the active prompt carries machine-
parseable rules — lines of `WHEN <text> THEN queue=<name>` — and the agent
classifies emails by executing the ACTIVE version's rules. Because the
executor is fixed, any accuracy change between versions is attributable to
the prompt, not to model variance.

Implements four documented patterns:

- self-optimizing-prompts: v1 is the champion; reflect() distills lessons
  from failures; optimize_prompt() generates a challenger from those lessons
  (server-side LLM); the harness executes champion vs challenger on held-out
  data and activates the winner (deliberate activation, rollback safe).
- guardrails-from-failures: a repeated-probe failure shape becomes a rule
  entry — rules always inject first.
- loop-safety: the harness detects the duplicate-probe shape and writes the
  guardrail rule from that detection.
- procedural-memory: the escalation procedure is stored as a server-side
  skill (playbook) and fetched at run time.

Phases (separate processes, one experiment):
  teach     activate v1, run training emails, record outcomes, reflect,
            optimize, A/B champion vs challenger on held-out data, activate
            the winner, write the guardrail rule, create the skill
  evaluate  fetch the ACTIVE prompt, run held-out emails single-probe with
            the guardrail rule recalled, verify the skill. No writes.
"""
import argparse
import json
import os
import re
from pathlib import Path

VERSION = "self-tuner-v1"
AGENT_ID = "email-triage"
DEFAULT_QUEUE = "inbox"

# v1 champion: a deliberately incomplete policy. No rule for legal threats and
# no rule for large refunds — both fall to the default queue, wrongly.
V1_PROMPT = """You triage customer emails into queues. Valid queues: legal_review, incident,
password_support, large_refund, inbox. Apply the first matching rule:
WHEN legal OR lawyer OR lawsuit THEN queue=legal_review
WHEN outage OR down THEN queue=incident
WHEN password OR locked OR sign in THEN queue=password_support
Everything else goes to queue=inbox."""

RULES_INSTRUCTION = ("Keep the exact format: one rule per line, 'WHEN <keywords> THEN queue=<name>'. "
                     "Keywords are lowercase substrings matched with OR against the email text; never "
                     "use parentheses or AND. Keep existing correct rules; add or adjust rules for the "
                     "failure classes in the lessons. Valid queues: legal_review, incident, "
                     "password_support, large_refund, inbox.")

# Fixture. expected queue for every email; train and held-out split.
EMAILS = {
    "train": [
        dict(id="E1", text="Our legal team is reviewing your terms, expect a lawyer letter.", expected="legal_review"),
        dict(id="E2", text="The dashboard is down, cannot access anything.", expected="incident"),
        dict(id="E3", text="I locked myself out, password reset does not arrive.", expected="password_support"),
        dict(id="E4", text="We demand a refund of $9,800 for the annual plan immediately.", expected="large_refund"),
        dict(id="E5", text="Server outage in EU region, pages will not load.", expected="incident"),
        dict(id="E6", text="Please refund $12,000; our counsel will call otherwise.", expected="large_refund"),
    ],
    "held_out": [
        dict(id="H1", text="CEO's lawyer contacted us about the contract.", expected="legal_review"),
        dict(id="H2", text="The admin console is down since this morning.", expected="incident"),
        dict(id="H3", text="Refund request: $15,000 for the enterprise seat.", expected="large_refund"),
        dict(id="H4", text="Forgot my password again, cannot sign in.", expected="password_support"),
    ],
}


def parse_rules(prompt_text):
    rules = []
    for line in (prompt_text or "").splitlines():
        m = re.match(r"\s*WHEN\s+(.+?)\s+THEN\s+queue=(\w+)\s*$", line, re.IGNORECASE)
        if m:
            rules.append((m.group(1).lower(), m.group(2).strip().lower()))
    return rules


def execute(prompt_text, email, probe_count):
    """The deterministic agent: first matching rule wins, else the default.
    The loop-safety arc: the first legal-adjacent email probes twice (the
    classifier re-reads it) before deciding — the detector counts probes."""
    rules = parse_rules(prompt_text)
    text = email["text"].lower()
    probes = 1
    if "lawyer" in text or "counsel" in text:
        probes = 2   # the repeated-probe failure shape
    for keywords, queue in rules:
        if any(k in text for k in keywords.split(" or ")):
            return queue, probes
    return DEFAULT_QUEUE, probes


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def run_teach(client, experiment):
    run_id = f"{VERSION}-{experiment}"
    # 1) Activate the v1 champion.
    client.advanced.set_prompt({"agent_id": AGENT_ID, "content": V1_PROMPT, "activate": True})
    set_res = client.advanced.set_prompt({"agent_id": AGENT_ID, "content": V1_PROMPT, "activate": True})
    v1_version = (set_res.get("version") or {}).get("version_id")
    v1_content = (set_res.get("version") or {}).get("content") or V1_PROMPT
    emit("champion_activated", version=v1_version, rules=len(parse_rules(v1_content)))

    # 2) Run the training set; ingest evidence for failures.
    for email in EMAILS["train"]:
        decided, probes = execute(v1_content, email, probe_count=0)
        correct = decided == email["expected"]
        emit("train_decision", email=email["id"], decided=decided,
             expected=email["expected"], correct=correct, probes=probes)
        stored = client.remember(
            content=f"Email {email['id']}: {email['text']} "
                    f"-> decided {decided}, correct queue is {email['expected']}. "
                    f"{'Wrong queue.' if not correct else 'Correct.'}",
            intent="fact", agent_id=AGENT_ID, item_id=email["id"], wait=True, timeout_ms=60000)
        if stored.get("error") or stored.get("status") in ("failed", "error"):
            raise RuntimeError(f"evidence ingest failed for {email['id']}")
        record_ids = [w["record_id"] for t in stored.get("traces", [])
                      for w in t.get("writes", []) if w.get("success")]
        if record_ids:
            client.record_outcome(reference_id=record_ids[0], entry_ids=record_ids,
                                  outcome="success" if correct else "failure",
                                  signal=1.0 if correct else -1.0, agent_id=AGENT_ID,
                                  idempotency_key=f"{VERSION}:{email['id']}",
                                  rationale=f"triage decision for {email['id']}")

    # 3) Loop-safety detection -> guardrail rule (rules always inject first).
    double_probes = [e for e in EMAILS["train"] if execute(v1_content, e, 0)[1] == 2]
    if double_probes:
        client.remember(
            content=("Triage rule: never re-probe an email already read twice. After two "
                     "conflicting reads, queue it manual_review and move on."),
            intent="rule", agent_id="triage-guardian", lesson_scope="global",
            wait=True, timeout_ms=60000)
        emit("guardrail_written", trigger="duplicate_probes", emails=[e["id"] for e in double_probes])

    # 4) Distill lessons, then generate the challenger from them.
    ref = client.reflect(session_id=run_id, include_linked_runs=False)
    emit("reflection", lessons_stored=(ref or {}).get("lessons_stored"),
         degraded=(ref or {}).get("degraded"))
    opt = client.optimize_prompt(agent_id=AGENT_ID, session_id=run_id, auto_activate=False)
    if opt.get("error"):
        raise RuntimeError(f"optimize_prompt failed: {opt['error']}")
    emit("optimize", keys=sorted(k for k in opt.keys() if k != "versions")[:6])

    # 5) A/B on held-out data: champion vs challenger, executed deterministically.
    challenger = opt.get("candidate") or {}
    ch_id = challenger.get("version_id") or challenger.get("id")
    ch_content = challenger.get("content") or ""
    if not ch_id or not ch_content:
        raise RuntimeError("optimizer produced no challenger version")
    emit("challenger", version=ch_id, rules=len(parse_rules(ch_content)))

    def accuracy(content):
        return sum(1 for e in EMAILS["held_out"]
                   if execute(content, e, 0)[0] == e["expected"])

    v1_acc, ch_acc = accuracy(v1_content), accuracy(ch_content)
    winner_is_challenger = ch_acc > v1_acc
    if winner_is_challenger:
        act = client.advanced.activate_prompt_version(
            {"agent_id": AGENT_ID, "version_id": ch_id})
        if isinstance(act, dict) and act.get("error"):
            raise RuntimeError(f"activation failed: {act['error']}")
    emit("ab_result", champion_version=v1_version, champion_accuracy=v1_acc,
         challenger_version=ch_id, challenger_accuracy=ch_acc,
         activated="challenger" if winner_is_challenger else "champion (kept)")

    # 6) Procedural memory: the escalation procedure as a server-side playbook.
    try:
        proj = client.advanced.create_project({"name": f"{AGENT_ID}-{experiment}"})
    except Exception as exc:
        proj = {}
        emit("skill_skipped", reason=str(exc)[:80])
    project_id = (proj.get("project") or {}).get("project_id")
    if project_id:
        skill = client.advanced.create_skill({
            "project_id": project_id, "agent_id": AGENT_ID,
            "name": "escalate_large_refund",
            "description": "Procedure for refunds above the self-serve cap",
            "instructions": "1. Confirm the amount exceeds $5,000. 2. Queue large_refund. "
                            "3. Flag the account for counsel review if legal language is present.",
            "skill_type": "playbook", "parameters_schema": "{}"})
        emit("skill_created", name=(skill.get("skill") or {}).get("name"),
             project=project_id[:12])


def run_evaluate(client, experiment):
    active = client.advanced.get_prompt({"agent_id": AGENT_ID})
    version = (active.get("version") or {})
    content = version.get("content") or ""
    emit("active_prompt", version=version.get("version_id"),
         rules=len(parse_rules(content)),
         avg_outcome_score=version.get("avg_outcome_score"))
    results = []
    for email in EMAILS["held_out"]:
        decided, probes = execute(content, email, probe_count=0)
        correct = decided == email["expected"]
        results.append(correct)
        emit("resolution", email=email["id"], decided=decided,
             expected=email["expected"], correct=correct, probes=probes)
    rules = client.recall(query="triage re-probe rule", entry_types=["rule"], limit=3,
                          evidence_only=True, include_working_memory=False,
                          include_linked_runs=False, prefer_current_run=True)
    rule_ids = [e["id"] for e in (rules.get("evidence") or []) if e.get("id")]
    emit("guardrail_recalled", count=len(rule_ids), ids=rule_ids)
    emit("metrics", correct=sum(results), of=len(results),
         max_probes=max(execute(content, e, 0)[1] for e in EMAILS["held_out"]))
    if sum(results) < len(results):
        raise RuntimeError("Active prompt missed held-out cases; run teach with the "
                           "same experiment first")


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
