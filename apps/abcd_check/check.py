"""Two-arm check: `return_path` over the pinned tickets, bare text against Mubit-built state.

One run, named `abcd-check`, is seeded with the return rules (one rule entry per
membership tier, the guideline line verbatim) and, per ticket, three fact entries about
the subject: the customer's tier, the order's purchase date and whether the original
packaging is at hand. Every fact carries the customer and order identifiers under
`entities` and the primary one under `subject`, the convention decide() recalls by.

Then every ticket is decided twice with the same Question (`return_path`, options
`accept` and `ask_for_receipt`, act threshold 0.9):

  bare   the provider on a state holding only the ticket text: the provider as sold
  mubit  decide() against the seeded run: the provider on the state Mubit built from
         the recalled facts and rules; the decision is recorded in the run

The report holds, per arm, the correct count, the automated share at the threshold,
mean latency and cost from token usage, and, per ticket, the expected option, each
arm's action and probabilities, and whether the Mubit state contained the tier fact
and the tier's rule. It passes when the Mubit arm is strictly more correct than bare
text and at least ninety percent of Mubit states contained both. `how-it-works.md`
explains how to read it.

Each arm call is retried on a provider outage, a transport failure or a server error
(three attempts, 2 s then 5 s apart); a persistent failure aborts the run naming the
ticket. A Mubit-arm retry after the provider answered but the record failed may leave
a duplicate decision record in the run.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from mubit import Client, ServerError, TransportError

try:
    from mubit.decisions import ESCALATE, CloudflareJevProvider, ProviderError, ProviderUnavailable, Question, decide
except ImportError as exc:
    raise SystemExit("check.py needs the mubit-sdk release that carries decide(); "
                     "until it ships set MUBIT_SDK to a checkout of sdk/python/mubit-sdk (see requirements.txt)") from exc

from tickets import PINNED_PATH, TIERS

RUN = "abcd-check"
THRESHOLD = 0.9
REQUIRED_RECALL_SHARE = 0.9
ARMS = ("bare", "mubit")
RETRY_ATTEMPTS = 3
RETRY_SLEEPS = (2, 5)
RETRIED_FAILURES = (ProviderUnavailable, TransportError, ServerError)
# USD per million input tokens; output is free. The credit judge's price table
# (apps/credit_judge/costs.py) carries the same figure and its source.
JEV_PRICE_PER_M_INPUT_TOKENS = 0.042
PRICE_SOURCE = "docs.typesafe.ai pricing (input only; output free)"
OPTION_CRITERIA = {
    "accept": ("The return can be accepted now: under the return policy the customer's membership "
               "tier, together with the order's purchase date or its original packaging, allows the "
               "return without a receipt."),
    "ask_for_receipt": ("The return policy is not satisfied by the membership tier, the purchase date or "
                        "the packaging, so the agent must ask the customer for the receipt before the "
                        "return can go ahead."),
}

HERE = Path(__file__).resolve().parent
REPORT_PATH = HERE / "live-abcd-check.json"
MARKDOWN_PATH = HERE / "live-abcd-check.md"


def question_for(pinned):
    reference_date = pinned["reference_date"]
    return Question(
        name=pinned["question"],
        act_threshold=THRESHOLD,
        instructions=(f"A customer wrote to support about returning an order. Today is {reference_date}; "
                      "the return policy's windows are measured back from today. Decide which return path "
                      "the agent must take, from the customer's membership tier, the order's purchase date "
                      "and its packaging under the policy."),
        options={option: OPTION_CRITERIA[option] for option in pinned["options"]},
    )


def seed_entries(pinned):
    """The rule and fact entries the run is seeded with, in seeding order."""
    entries = [dict(content=pinned["rules"][tier], intent="rule",
                    metadata={"tier": tier, "policy": "Membership Privileges"}) for tier in TIERS]
    for ticket in pinned["tickets"]:
        customer, order = ticket["subject"]["customer"], ticket["subject"]["order"]
        entities = [customer, order]
        packaging = ("is still in its original packaging" if ticket["packaging"]
                     else "is not in its original packaging any more")
        facts = (
            ("tier", customer, f"Customer {customer} is a {ticket['tier']} member."),
            ("purchase_date", order, f"Order {order} of customer {customer} was purchased on {ticket['purchase_date']}."),
            ("packaging", order, f"Order {order} of customer {customer} {packaging}."),
        )
        for kind, subject, content in facts:
            entries.append(dict(content=content, intent="fact",
                                metadata={"entities": entities, "subject": subject, "fact": kind,
                                          "convo_id": ticket["convo_id"]}))
    return entries


def seed(client, run, pinned, log=lambda message: None):
    """Remember every entry into the run, waiting for each ingest job; returns the count."""
    entries = seed_entries(pinned)
    for i, entry in enumerate(entries, 1):
        job = client.remember(content=entry["content"], session_id=run, intent=entry["intent"],
                              metadata=entry["metadata"], wait=True)
        error = job.get("error") if isinstance(job, dict) else None
        if error or (isinstance(job, dict) and job.get("status") == "failed"):
            raise RuntimeError(f"seeding failed on {entry['intent']} {entry['content']!r}: {error or job}")
        if i % 25 == 0 or i == len(entries):
            log(f"  seeded {i}/{len(entries)} entries")
    return len(entries)


class RecordingProvider:
    """The seam with every request and reply kept: the usage and latency decide() does not return."""

    def __init__(self, inner):
        self.inner = inner
        self.name = getattr(inner, "name", type(inner).__name__)
        self.model = getattr(inner, "model", "")
        self.calls = []
        self.responses = []

    def decide(self, state, questions):
        self.calls.append({"state": state, "questions": dict(questions)})
        response = self.inner.decide(state, questions)
        self.responses.append(response)
        return response

    @property
    def last(self):
        return self.responses[-1]


def _arm_result(answer, action, latency_ms, total_ms, usage):
    """One arm's row fields from an answer (a ProviderAnswer or a Decision: chosen, probabilities, confidence)."""
    return dict(chosen=answer.chosen, action=action, probabilities=dict(answer.probabilities),
                confidence=answer.confidence, latency_ms=latency_ms, total_ms=total_ms,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


def bare_arm(provider, question, ticket):
    """The provider on the ticket text alone, through the same seam decide() uses."""
    questions = {question.name: question.to_provider_question()}
    started = time.perf_counter()
    response = provider.decide({"text": ticket["text"]}, questions)
    total_ms = (time.perf_counter() - started) * 1000.0
    answer = response.answers[question.name]
    if sorted(answer.probabilities) != sorted(question.option_names):
        raise ProviderError(f"malformed provider answer for {question.name!r}: probabilities cover "
                            f"{sorted(answer.probabilities)!r} but the options are {list(question.option_names)!r}")
    action = answer.chosen if answer.probabilities[answer.chosen] >= question.act_threshold else ESCALATE
    return _arm_result(answer, action, response.latency_ms, total_ms, response.usage)


def mubit_arm(client, provider, question, ticket, run):
    """decide() against the seeded run; `provider` is a RecordingProvider so usage is kept."""
    started = time.perf_counter()
    decision = decide(question, ticket["text"], subject=ticket["subject"], session_id=run,
                      client=client, provider=provider)
    total_ms = (time.perf_counter() - started) * 1000.0
    result = _arm_result(decision, decision.action, decision.latency_ms, total_ms, provider.last.usage)
    result.update(decision_id=decision.id, state_refs=list(decision.state_refs), state=decision.state)
    result.update(state_hits(ticket, decision.state))
    return result


def with_retry(fn, *, what, attempts=RETRY_ATTEMPTS, sleep=time.sleep, log=print):
    """Call `fn` until it returns; a provider outage, a transport failure or a server error
    is retried up to `attempts` times, sleeping 2 s then 5 s between; any other failure
    propagates at once. After the last attempt a RuntimeError names `what`."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RETRIED_FAILURES as exc:
            if attempt == attempts:
                raise RuntimeError(f"{what} failed after {attempts} attempts: {exc}") from exc
            pause = RETRY_SLEEPS[min(attempt, len(RETRY_SLEEPS)) - 1]
            log(f"  {what}: attempt {attempt} failed ({type(exc).__name__}: {exc}); retrying in {pause} s")
            sleep(pause)


def state_hits(ticket, state):
    """Whether the state names the customer's tier and holds the tier's own rule."""
    customer, tier = ticket["subject"]["customer"].lower(), ticket["tier"].lower()
    facts = [str(fact).lower() for fact in state.get("facts") or []]
    rules = [str(rule).lower() for rule in state.get("rules") or []]
    return {"tier_fact_present": any(customer in fact and f"{tier} member" in fact for fact in facts),
            "matching_rule_present": any(f"{tier} members" in rule for rule in rules)}


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def summarise(rows):
    """Per arm: correct (chosen option), automated at the threshold, latency, tokens and cost."""
    n = len(rows)
    share = lambda count: count / n if n else 0.0
    summary = {}
    for arm in ARMS:
        results = [row[arm] for row in rows]
        correct = sum(r["chosen"] == row["expected"] for r, row in zip(results, rows))
        automated = sum(r["action"] != ESCALATE for r in results)
        automated_correct = sum(r["action"] == row["expected"] for r, row in zip(results, rows))
        input_tokens = sum(r["input_tokens"] for r in results)
        cost = input_tokens * JEV_PRICE_PER_M_INPUT_TOKENS / 1e6
        summary[arm] = dict(
            count=n, correct=correct, accuracy=share(correct),
            automated=automated, automated_share=share(automated),
            automated_correct=automated_correct, automated_wrong=automated - automated_correct,
            mean_latency_ms=_mean(r["latency_ms"] for r in results),
            mean_total_ms=_mean(r["total_ms"] for r in results),
            input_tokens=input_tokens, output_tokens=sum(r["output_tokens"] for r in results),
            cost_usd=cost, mean_cost_usd=share(cost),
        )
    mubit = [row["mubit"] for row in rows]
    both = sum(r["tier_fact_present"] and r["matching_rule_present"] for r in mubit)
    summary["mubit"].update(
        tier_fact_present=sum(r["tier_fact_present"] for r in mubit),
        matching_rule_present=sum(r["matching_rule_present"] for r in mubit),
        both_present=both, both_present_share=share(both),
    )
    return summary


def verdict(summary):
    bare, mubit = summary["bare"], summary["mubit"]
    more_correct = mubit["correct"] > bare["correct"]
    recall_ok = mubit["count"] > 0 and mubit["both_present_share"] >= REQUIRED_RECALL_SHARE
    return dict(passed=more_correct and recall_ok, mubit_more_correct=more_correct,
                recall_at_least_ninety_percent=recall_ok, bare_correct=bare["correct"],
                mubit_correct=mubit["correct"], both_present=mubit["both_present"], count=mubit["count"],
                both_present_share=mubit["both_present_share"], required_share=REQUIRED_RECALL_SHARE)


def build_report(pinned, rows, *, run, provider, model, endpoint, question=None):
    question = question or question_for(pinned)
    summary = summarise(rows)
    return {
        "check": "abcd two-arm check",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run": run,
        "endpoint": endpoint,
        "provider": {"name": provider, "model": model},
        "question": {"name": question.name, "type": question.type, "options": dict(question.options),
                     "instructions": question.instructions, "threshold": question.act_threshold},
        "reference_date": pinned["reference_date"],
        "seed": pinned["seed"],
        "source": pinned.get("source"),
        "ticket_count": len(rows),
        "pricing": {"input_usd_per_m_tokens": JEV_PRICE_PER_M_INPUT_TOKENS, "output_usd_per_m_tokens": 0.0,
                    "source": PRICE_SOURCE},
        "arms": summary,
        "verdict": verdict(summary),
        "tickets": rows,
    }


def _pct(share):
    return f"{100 * share:.0f}%"


def _money(usd):
    return f"${usd:.6f}".rstrip("0").rstrip(".") if usd else "$0"


def render_markdown(report):
    v, arms, n = report["verdict"], report["arms"], report["ticket_count"]
    status = "PASS" if v["passed"] else "FAIL"
    lines = [
        f"# ABCD two-arm check: {status}",
        "",
        f"Run `{report['run']}` on `{report['endpoint']}`, provider {report['provider']['name']} "
        f"({report['provider']['model']}), {n} tickets, reference date {report['reference_date']}, "
        f"seed {report['seed']}, act threshold {report['question']['threshold']}. "
        f"Generated {report['generated_at']}.",
        "",
        f"**{status}.** Mubit arm correct {v['mubit_correct']}/{n} against bare text {v['bare_correct']}/{n} "
        f"({'strictly more' if v['mubit_more_correct'] else 'not strictly more'}: criterion 1). "
        f"Both the tier fact and the matching rule were in the Mubit state for {v['both_present']}/{n} "
        f"= {_pct(v['both_present_share'])} of tickets "
        f"({'at least' if v['recall_at_least_ninety_percent'] else 'below'} {_pct(v['required_share'])}: criterion 2).",
        "",
        "## Per arm",
        "",
        "| Arm | Correct | Automated at threshold | Automated and correct | Automated and wrong "
        "| Mean provider latency | Mean total latency | Input tokens | Cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm in ARMS:
        a = arms[arm]
        lines.append(f"| {arm} | {a['correct']}/{n} ({_pct(a['accuracy'])}) | {a['automated']} ({_pct(a['automated_share'])}) "
                     f"| {a['automated_correct']} | {a['automated_wrong']} | {a['mean_latency_ms']:.0f} ms "
                     f"| {a['mean_total_ms']:.0f} ms | {a['input_tokens']} | {_money(a['cost_usd'])} |")
    m = arms["mubit"]
    lines += [
        "",
        f"Mubit state: tier fact present {m['tier_fact_present']}/{n}, matching rule present "
        f"{m['matching_rule_present']}/{n}, both {m['both_present']}/{n}. Correct counts the chosen option; "
        "automated counts actions other than escalate. The bare arm's total latency is the provider call; "
        "the Mubit arm's adds recall and the decision record.",
        "",
        "## Per ticket",
        "",
        "| Convo | Tier | Expected | Bare action | Bare p(chosen) | Mubit action | Mubit p(chosen) | Tier fact | Rule |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in report["tickets"]:
        b, mu = t["bare"], t["mubit"]
        lines.append(f"| {t['convo_id']} | {t['tier']} | {t['expected']} | {b['action']} | "
                     f"{b['probabilities'][b['chosen']]:.2f} ({b['chosen']}) | {mu['action']} | "
                     f"{mu['probabilities'][mu['chosen']]:.2f} ({mu['chosen']}) | "
                     f"{'yes' if mu['tier_fact_present'] else 'no'} | {'yes' if mu['matching_rule_present'] else 'no'} |")
    return "\n".join(lines) + "\n"


def run_check(pinned, client, provider, *, run=RUN, seed_run=True, limit=None, report_path=REPORT_PATH,
              markdown_path=MARKDOWN_PATH, endpoint="", log=print, sleep=time.sleep):
    tickets = pinned["tickets"][:limit] if limit else pinned["tickets"]
    subset = dict(pinned, tickets=tickets)
    question = question_for(subset)
    recorder = RecordingProvider(provider)
    if seed_run:
        log(f"seeding run {run!r} with the rules and the facts of {len(tickets)} tickets")
        seed(client, run, subset, log=log)
    rows = []
    for i, ticket in enumerate(tickets, 1):
        bare = with_retry(lambda: bare_arm(recorder, question, ticket),
                          what=f"convo {ticket['convo_id']} bare arm", sleep=sleep, log=log)
        mubit = with_retry(lambda: mubit_arm(client, recorder, question, ticket, run),
                           what=f"convo {ticket['convo_id']} mubit arm", sleep=sleep, log=log)
        rows.append(dict(convo_id=ticket["convo_id"], subflow=ticket["subflow"], tier=ticket["tier"],
                         purchase_date=ticket["purchase_date"], packaging=ticket["packaging"],
                         expected=ticket["expected"], text=ticket["text"], subject=dict(ticket["subject"]),
                         bare=bare, mubit=mubit))
        log(f"[{i:2d}/{len(tickets)}] convo {ticket['convo_id']} {ticket['tier']:6s} expected {ticket['expected']:15s} "
            f"bare {bare['action']} ({bare['probabilities'][bare['chosen']]:.2f} {bare['chosen']})  "
            f"mubit {mubit['action']} ({mubit['probabilities'][mubit['chosen']]:.2f} {mubit['chosen']})  "
            f"fact={'y' if mubit['tier_fact_present'] else 'n'} rule={'y' if mubit['matching_rule_present'] else 'n'}")
    model = recorder.last.model if recorder.responses else recorder.model
    report = build_report(subset, rows, run=run, provider=recorder.name, model=model, endpoint=endpoint,
                          question=question)
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown = render_markdown(report)
    Path(markdown_path).write_text(markdown, encoding="utf-8")
    log("")
    log(markdown.split("\n## Per ticket")[0].rstrip())
    log("")
    log(f"report: {report_path}  markdown: {markdown_path}")
    return report


def main(argv=None, env=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tickets", type=Path, default=PINNED_PATH,
                        help="the pinned ticket file (default: data/tickets.json)")
    parser.add_argument("--run", default=RUN, help=f"the run to seed and decide in (default: {RUN})")
    parser.add_argument("--skip-seed", action="store_true", help="the run is already seeded; only decide")
    parser.add_argument("--limit", type=int, help="only the first N tickets (a smoke run)")
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="the JSON report to write")
    parser.add_argument("--markdown", type=Path, default=MARKDOWN_PATH, help="the Markdown report to write")
    args = parser.parse_args(argv)
    if env is None:
        load_dotenv(HERE / ".env")
        env = os.environ
    endpoint, api_key = env.get("MUBIT_ENDPOINT", ""), env.get("MUBIT_API_KEY", "")
    if not endpoint or not api_key:
        parser.error("set MUBIT_ENDPOINT and MUBIT_API_KEY (in .env or the environment)")
    try:
        provider = CloudflareJevProvider.from_env(env)
    except ProviderError as exc:
        parser.error(str(exc))
    pinned = json.loads(args.tickets.read_text(encoding="utf-8"))
    client = Client(endpoint=endpoint, api_key=api_key, transport="http", run_id=args.run, timeout_ms=120000)
    report = run_check(pinned, client, provider, run=args.run, seed_run=not args.skip_seed, limit=args.limit,
                       report_path=args.report, markdown_path=args.markdown, endpoint=endpoint)
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
