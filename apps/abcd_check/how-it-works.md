# ABCD check — How It Works

> Companion to [`README.md`](README.md). The committed run is in
> [`live-abcd-check.md`](live-abcd-check.md) (the per-arm and per-ticket tables) and
> [`live-abcd-check.json`](live-abcd-check.json) (the same, plus every state the provider read).

One **question**, `return_path`, is answered over fifty real return tickets twice. In the
first arm the **provider** (Jev, through Cloudflare Workers AI) reads a **state** holding
only the ticket text. In the second arm it reads the state Mubit built: the same text plus
the **facts** and **rules** recalled about the ticket's **subject**. The difference between
the arms is what Mubit adds. The per-ticket columns say whether a miss came from recall or
from the provider.

```
tickets.json  →  seed one run: 4 rules + 3 facts per ticket
              →  per ticket:  bare   provider({text})                         → chosen, probabilities
                              mubit  decide(question, text, subject=…)        → recall → state → provider → threshold → record
              →  report: correct, automated share, latency, cost per arm; expected, action, probability, recall hits per ticket
              →  verdict: Mubit strictly more correct than bare, and both hits in ≥ 90% of Mubit states
```

## The question

| Field | Value |
| --- | --- |
| Name, type | `return_path`, choice |
| **Options** and their **criteria** | `accept`: the customer's membership tier, with the order's purchase date or its original packaging, allows the return without a receipt. `ask_for_receipt`: the policy is not satisfied by tier, date or packaging, so the agent must ask for the receipt first. |
| Instructions | "Today is 2020-04-01; the policy's windows are measured back from today. Decide the return path from the customer's tier, the order's purchase date and its packaging under the policy." |
| **Act threshold** | 0.9 |

The reference date is the pinned "today" of the ticket set (two days after the latest
purchase in the dataset); the question states it so both arms measure windows from the
same day. The same `Question` object serves both arms.

## One run, seeded once

Facts, rules and decision records are recalled only from the run they were written to,
so the check keeps everything in one **run**, `abcd-check`:

| Entry | Content | Metadata |
| --- | --- | --- |
| **Rule**, one per tier | the guidelines' Membership Privileges line, verbatim: `Silver members: > Ask for the purchase date, return possible within the last 6 months <or> Ask if they have a receipt … <or> Ask if in original packaging …` | `tier`, `policy` |
| **Fact**, tier | `Customer albertsanders813 is a silver member.` | `entities: [albertsanders813, 5110435718]`, `subject: albertsanders813`, `fact: tier` |
| **Fact**, purchase date | `Order 5110435718 of customer albertsanders813 was purchased on 2019-08-22.` | `entities: […]`, `subject: 5110435718`, `fact: purchase_date` |
| **Fact**, packaging | `Order 5110435718 of customer albertsanders813 is not in its original packaging any more.` | `entities: […]`, `subject: 5110435718`, `fact: packaging` |

The `entities` and `subject` keys are the subject convention `decide()` reads by: the
same identifiers, keyed by role, are passed as the decision's subject, and the **lookup**
matches each of them against `subject` exactly (the server does not match inside the
`entities` list, so a fact is stored under its primary identifier). Nothing in a fact
says which option is right; the provider has to apply the rule to the facts.

## Arm one: bare text

```python
provider.decide({"text": ticket_text}, {"return_path": question.to_provider_question()})
```

The provider as sold: the same seam, the same question, and nothing about the customer.
The ticket text never names the tier, the purchase age or the packaging (tickets that do
were excluded when the set was built), so whatever the provider answers here is a prior.

## Arm two: Mubit state

```python
decide(question, ticket_text, subject={"customer": "albertsanders813", "order": "5110435718"},
       session_id="abcd-check", client=client, provider=provider)
```

`decide()` reads the subject's facts and earlier decisions and the run's rules with one
keyed **lookup** over the run (facts and decisions by an exact match on `subject`, one
clause per identifier; rules by their entry type; all in write order), recalls the lessons
with one context call whose query is the text and the subject identifiers, and builds the
state from both:

```json
{"text": "I ordered a shirt and the description said it was apple green, but … I'd like to return it.",
 "subject": {"customer": "albertsanders813", "order": "5110435718"},
 "facts": ["Customer albertsanders813 is a silver member.",
           "Order 5110435718 of customer albertsanders813 was purchased on 2019-08-22.",
           "Order 5110435718 of customer albertsanders813 is not in its original packaging any more.", "…"],
 "rules": ["Silver members: > Ask for the purchase date, return possible within the last 6 months <or> …", "…"],
 "lessons": ["…"],
 "decisions": ["return_path: escalate for customer albertsanders813, order 5110435718"]}
```

`facts` are exactly the subject's own entries, however many near-identical facts about
other customers the run holds: recall by meaning returns one representative per cluster
of near-identical entries, so with fifty same-form tier facts in the run the subject's own
almost never came back, which is why the facts are read by lookup and not by recall.
`rules` are every rule of the run, so the tier's own line is always there: asked by
meaning, the lane returned three of the four policy lines for a silver ticket and missed
gold's short line most often, because a ticket never names its tier, and mixed with the
lessons the rules were crowded out (37 of 50 states held the tier's rule). `lessons` are
whatever the instance has learned (in the live run, an instance with an LLM configured
had produced lessons of its own from the seeded rules by the second ticket); `decisions`
are the earlier decision records about this subject, the most recent twenty.
The provider's answer is then thresholded: the **action** is the chosen option when its
probability reaches 0.9,
otherwise `escalate`, an **escalation** the caller decides what to do with. Finally the
decision is remembered into the run as a Trace entry whose metadata names the subject,
and its record id is the **decision id**. The check does not report **outcomes**; the
SDK's live test covers `Decision.report()`.

## Reading the report

Per arm (`live-abcd-check.md`, first table):

- **Correct** counts tickets whose *chosen* option equals the expected option from the
  guidelines. The chosen option exists whether or not the decision was acted on.
- **Automated at threshold** counts actions other than `escalate`. **Automated and
  wrong** is the number an operations lead watches: decisions that would have been
  acted on and were wrong. Raising the act threshold trades automated share for it.
- **Mean provider latency** is the provider call. **Mean total latency** adds, for the
  Mubit arm, the recall and the wait for the decision record's ingest job; on an instance
  with an LLM configured those are model calls and dominate.
- **Cost** is input tokens times the Jev price (USD 0.042 per million input tokens,
  output free). The Mubit state is larger than the bare text, so it costs more per call.

Per ticket (second table): the expected option; each arm's action and the probability
of its chosen option; and the two recall columns, **tier fact** (a fact in the state names
the customer and the tier) and **rule** (the tier's own Membership Privileges line is in
the state). Read them together with the action:

| Tier fact | Rule | Mubit wrong? | Where to look |
| --- | --- | --- | --- |
| yes | yes | yes | the provider: it had what it needed (often the date arithmetic against the reference date) |
| no | any | yes | the lookup: the fact was not seeded with this customer under `subject`, or the state token budget cut it (oldest decisions go first, then oldest facts) |
| yes | no | yes | the lookup: the rule was not seeded as a rule entry in this run, or the state token budget cut it (rules go after the decisions and before the facts) |

The **verdict** applies the two pass criteria from the spec: the Mubit arm is strictly more
correct than bare text, and at least ninety percent of Mubit states contained both the tier
fact and the matching rule. The first says Mubit's state helps; the second says a miss is
not for lack of recall.

## Reproducing

`make abcd-check` reads the pinned ticket file (fifty tickets, seed 7, reference date
2020-04-01), seeds run `abcd-check` and writes the two report files. Against a fresh
instance the expected options and the recall columns reproduce; the provider's
probabilities can move a little between calls, so the counts may differ by a ticket or
two. Against an instance that already holds the run, `--skip-seed` decides without
seeding again (the earlier decision records are then in the state as well), and `--run`
names a fresh run to seed from scratch. Each arm call is retried on a provider outage,
a transport failure or a server error (three attempts, 2 s then 5 s apart, on top of the
SDK provider's own two retries); a persistent failure aborts the run naming the ticket. A
Mubit-arm retry after the provider answered but the decision record failed may leave a
duplicate decision record in the run.
