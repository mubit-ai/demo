# ABCD check — real return tickets for a typed decision

The typed-decision check runs one question, `return_path`, over fifty real support tickets
and compares two arms: the provider on the bare ticket text, and the provider on a state
that Mubit built from the customer's facts and the return policy. This folder holds the
ticket set. The two arms and the report are a separate ticket and land here next.

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

## Running

```sh
make abcd-tickets          # fetch the dataset (37 MB) and guidelines into data/abcd/, rebuild data/tickets.json
make abcd-tickets-check    # rebuild in memory and confirm the committed file is reproduced byte for byte
make test-abcd-check       # offline suite on a small ABCD-shaped fixture, no network
```

From this folder: `python tickets.py`, `python tickets.py --check`,
`python -m unittest test_demo -v`. The builder is standard library only. The raw dataset
stays in `data/abcd/` and is not committed; both source files are verified against the
SHA-256 values pinned in `tickets.py`, so a changed upstream file stops the build instead
of silently changing the set.

## Layout

```text
tickets.py            fetch, ticket extraction, ground truth, seeded selection, --check
test_demo.py          offline suite
.env.example          no keys yet; the two-arm check adds the Mubit and Cloudflare variables
data/tickets.json     the pinned fifty tickets, reference date, seed, rules and source hashes
data/abcd/            the raw ABCD data (ignored by git)
```
