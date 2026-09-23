# ABCD check — real return tickets for a typed decision

The typed-decision check runs one question, `return_path`, over fifty real support tickets
and compares two arms: the provider on the bare ticket text, and the provider on a state
that Mubit built from the customer's facts and the return policy. `tickets.py` builds the
pinned ticket set; `check.py` seeds a run, decides every ticket in both arms and writes the
report with its pass verdict. [`how-it-works.md`](how-it-works.md) walks the loop and says
how to read the report; [`live-abcd-check.md`](live-abcd-check.md) is the committed run.

## The data

The tickets come from the [Action-Based Conversations Dataset](https://github.com/asappresearch/abcd)
(ABCD, ASAPP Research, MIT). Each chat carries the scenario it was collected under: the
customer's membership tier, the order's purchase date, whether the original packaging is
at hand, and the guideline flow the agent followed. The three return subflows of the
Product Defect flow (`return_color`, `return_size`, `return_stain`) give 538 chats.

A chat becomes a ticket the way a support inbox would see it: the customer's opening lines
before the first agent question about a name, account, order, membership, purchase date,
receipt, packaging or address, capped at three. A line that carries the customer's name,
username, email, order id, phone or address is dropped. A ticket that names a tier, says
how long ago the purchase was, or mentions the packaging or a receipt is excluded, so the
text alone never decides the case. 463 chats survive.

## Ground truth

The guidelines' Membership Privileges rules, one per tier, give the option a correct
agent must take. The purchase windows are measured back from a fixed reference date,
**2020-04-01**, two days after the latest purchase in the dataset (2020-03-30):

| Tier | `accept` when | Otherwise |
| --- | --- | --- |
| gold | always | |
| silver | purchased within the last six calendar months, or the original packaging is at hand | `ask_for_receipt` |
| bronze | purchased within the last 90 days, or the original packaging is at hand | `ask_for_receipt` |
| guest | purchased within the last 30 days | `ask_for_receipt` |

Fifty tickets are drawn with seed **7**, round-robin over the seven feasible tier-option
cells (gold never asks for a receipt), one ticket per customer, so every tier and both
options appear in near-equal shares: 8 gold and 7 in each other cell.

Each row of `data/tickets.json` carries the conversation id and subflow, the ticket text,
the subject identifiers (`customer` is the ABCD username, `order` the order id), the tier,
purchase date and packaging flag, the guideline rule that applies, the expected option,
and, for reference, what the human agent recorded: the tier they entered and whether the
return went ahead. The agent's record is not the label: an agent who asked for a receipt
and got one processed the return, and their window arithmetic ran against the day of the
chat, not the reference date.

## The check

`check.py` seeds one run, `abcd-check`, with the four Membership Privileges lines as rule
entries and, per ticket, three fact entries about the subject (the customer's tier, the
order's purchase date, the packaging flag), each carrying the customer and order
identifiers under `entities` and the primary one under `subject`. Then, per ticket, the
same `Question` (`return_path`, options `accept` and `ask_for_receipt`, act threshold 0.9)
is answered twice through the SDK's provider seam:

| Arm | State the provider reads | How |
| --- | --- | --- |
| **bare** | the ticket text only | `provider.decide({"text": …}, {…})`: the provider as sold |
| **mubit** | the text, the subject, the facts and earlier decisions looked up by `subject`, every rule of the run, and the lessons recalled by meaning | `mubit.decide(question, text, subject=…, session_id="abcd-check")`; the decision is recorded in the run |

The report (`live-abcd-check.json`, rendered as `live-abcd-check.md`) holds, per arm, the
correct count, the automated share at the threshold, mean latency and cost from token
usage, and, per ticket, the expected option, each arm's action and probabilities, and
whether the Mubit state contained the tier fact and the tier's rule. It passes when the
Mubit arm is strictly more correct than bare text and at least ninety percent of Mubit
states contained both. The exit code is 0 on pass and 1 otherwise. Each arm call is
retried on a provider outage, a transport failure or a server error (three attempts, 2 s
then 5 s apart); a persistent failure aborts the run naming the ticket.

## Running

```sh
make abcd-tickets          # fetch the dataset (37 MB) and guidelines into data/abcd/, rebuild data/tickets.json
make abcd-tickets-check    # rebuild in memory and confirm the committed file is reproduced byte for byte
make abcd-check            # seed run abcd-check, decide all fifty tickets in both arms, write live-abcd-check.json/.md
make test-abcd-check       # offline suites, no network: the ticket set, and the check on fakes
```

The check needs `MUBIT_ENDPOINT`, `MUBIT_API_KEY`, `CLOUDFLARE_ACCOUNT_ID` and
`CLOUDFLARE_API_TOKEN` in `.env` (this folder or the repository root; see `.env.example`).
Until the mubit-sdk release that carries `decide()` ships, `make` also needs `MUBIT_SDK`, a
checkout of `sdk/python/mubit-sdk` (branch `feat/jev-external`), in the environment or the
repository-root `.env`, the file `make` reads: `MUBIT_SDK=../sdk/python/mubit-sdk make abcd-check`.
It installs the checkout over the pinned SDK for the check and its tests; without it the check
stops with a message and its tests are skipped. `uv` caches the checkout's build by the
modification time of its `pyproject.toml`, so after editing the checkout run
`touch $MUBIT_SDK/pyproject.toml`, or the check and the tests keep the earlier build.

From this folder: `python tickets.py`, `python tickets.py --check`,
`python check.py [--run NAME] [--skip-seed] [--limit N] [--report PATH] [--markdown PATH]`,
`python -m unittest test_demo test_check -v`. Against an instance that already holds the
run, `--skip-seed` decides without seeding again (the earlier decision records are then in
the state as well) and `--run` names a fresh run to seed from scratch; `--limit 2` is a
quick smoke. The ticket builder is standard library only. The raw dataset stays in
`data/abcd/` and is not committed; both source files are verified against the SHA-256
values pinned in `tickets.py`, so a changed upstream file stops the build instead of
silently changing the set.

## Layout

```text
tickets.py            fetch, ticket extraction, ground truth, seeded selection, --check
check.py              seeding, the two arms, the report and the verdict
test_demo.py          offline suite for the ticket set
test_check.py         offline suite for the check (fake client and provider under the real decide())
how-it-works.md       the two arms, the state, and how to read the report
live-abcd-check.json  the committed run: per-arm totals, per-ticket rows with every state
live-abcd-check.md    the same run as tables
.env.example          the Mubit and Cloudflare variables
data/tickets.json     the pinned fifty tickets, reference date, seed, rules and source hashes
data/abcd/            the raw ABCD data (ignored by git)
```
