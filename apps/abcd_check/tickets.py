"""Build the pinned ticket set for the ABCD check: fifty real return tickets with ground truth.

The Action-Based Conversations Dataset (ABCD, ASAPP Research, MIT) holds human-to-human
support chats with the scenario behind each one: the customer's membership tier, the
order's purchase date and packaging flag, and the guidelines the agent followed. This
script fetches the dataset and the guidelines into `data/abcd/` (not committed), takes the
three return subflows, turns each chat into a ticket the way a support inbox would see it
(the customer's opening lines, before the agent asks for any detail), computes the option
the guidelines require for the `return_path` question against a fixed reference date, and
writes fifty of them, chosen with a fixed seed, to `data/tickets.json`.

Ground truth: gold always accepts; silver accepts within six calendar months of purchase
or with the original packaging; bronze within ninety days or with the original packaging;
guest within thirty days; otherwise the agent must ask for a receipt. The windows are
measured back from REFERENCE_DATE, two days after the latest purchase in the dataset.
A ticket whose text states a label input (the tier, the purchase age, the packaging or a
receipt) is excluded, so the text alone never decides a case.

The build is reproducible: the sources are verified by SHA-256, the selection is seeded,
and `python tickets.py --check` confirms the committed file is reproduced byte for byte.
"""
import argparse
import calendar
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import random
import re
import shutil
import sys
import urllib.request
from pathlib import Path

QUESTION = "return_path"
OPTIONS = ("accept", "ask_for_receipt")
TIERS = ("gold", "silver", "bronze", "guest")
RETURN_SUBFLOWS = ("return_color", "return_size", "return_stain")
REFERENCE_DATE = dt.date(2020, 4, 1)
SEED = 7
TICKET_COUNT = 50
MAX_UTTERANCES = 3

DATASET_URL = "https://raw.githubusercontent.com/asappresearch/abcd/master/data/abcd_v1.1.json.gz"
GUIDELINES_URL = "https://raw.githubusercontent.com/asappresearch/abcd/master/data/guidelines.json"
DATASET_FILE = "abcd_v1.1.json.gz"
GUIDELINES_FILE = "guidelines.json"
DATASET_SHA256 = "2bdf53ac359543dcdc38d55bc6513e78df120363f8f44870716e909f4606de15"
GUIDELINES_SHA256 = "9264557941df24fe075138a632a2345172971573124a354ccb2ceee2a12e4c2a"
LICENSE = "MIT (Copyright (c) 2021 ASAPP Research)"

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data" / "abcd"
PINNED_PATH = HERE / "data" / "tickets.json"

# An agent line that asks for any of these is where the ticket ends: from here on the
# customer answers questions instead of describing the problem.
DETAILS_REQUEST = re.compile(
    r"\b(name|account|id|username|user name|email|e-mail|order|phone|member|membership|level"
    r"|purchase|purchased|date|receipt|packag\w*|address)\b", re.I)
TIER_STATEMENT = re.compile(
    r"\b(gold|silver|bronze|guest)\b|\b(no|not have an?|don'?t have an?|do not have an?|without an?)\s+account\b",
    re.I)
_NUMBER = r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple|few|several|half a)"
_PURCHASE_VERB = r"(?:bought|purchased|ordered|received|got|arrived|came|delivered|had)"
PURCHASE_AGE = re.compile(
    r"\bago\b|\byesterday\b|\bthe other day\b|\bjust now\b|\brecent(?:ly)?\b"
    r"|\b(?:last|this|earlier this|past)\s+(?:week|month|year|night|weekend|morning|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b" + _NUMBER + r"\s+(?:day|week|month|year)s?\b"
    r"|\bjust\s+(?:bought|purchased|ordered|received|got|arrived|came|delivered)\b"
    r"|\b" + _PURCHASE_VERB + r"\b[^.!?]{0,40}\btoday\b|\btoday\b[^.!?]{0,40}\b" + _PURCHASE_VERB + r"\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    re.I)
PACKAGING_STATEMENT = re.compile(r"\b(packag\w*|receipt)\b", re.I)


class SourceMismatch(Exception):
    """A downloaded source file does not hash to the pinned value."""


def fetch(data_dir=DATA_DIR, opener=urllib.request.urlopen):
    """Download the dataset and the guidelines into data_dir unless present; verify both hashes."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for url, name, expected in ((DATASET_URL, DATASET_FILE, DATASET_SHA256),
                                (GUIDELINES_URL, GUIDELINES_FILE, GUIDELINES_SHA256)):
        path = data_dir / name
        if not path.exists():
            print(f"fetching {url}", file=sys.stderr)
            partial = path.with_name(path.name + ".part")
            with opener(url, timeout=300) as response, open(partial, "wb") as out:
                shutil.copyfileobj(response, out)
            os.replace(partial, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise SourceMismatch(f"{name}: sha256 {digest} does not match the pinned {expected}; "
                                 f"delete {path} to fetch again or update the pin")
        paths.append(path)
    return tuple(paths)


def load_dataset(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_guidelines(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def return_conversations(dataset):
    convos = [c for split in dataset.values() for c in split
              if c["scenario"]["subflow"] in RETURN_SUBFLOWS]
    return sorted(convos, key=lambda c: c["convo_id"])


def opening_utterances(conversation):
    """The customer's lines before the first action or the first agent request for details."""
    lines = []
    for speaker, text in conversation["original"]:
        if speaker == "action":
            break
        if speaker == "agent" and DETAILS_REQUEST.search(text):
            break
        if speaker == "customer":
            lines.append(text.strip())
    return lines


def _leak_pattern(scenario):
    personal, order = scenario["personal"], scenario["order"]
    parts = [r"\b" + re.escape(token) + r"\b" for token in personal["customer_name"].split()
             if len(token) >= 2]
    phone = personal["phone"]
    for value in (personal["username"], personal["email"], personal.get("account_id"),
                  order["order_id"], order["zip_code"], order["street_address"], phone,
                  re.sub(r"\D", "", phone)):
        if value:
            parts.append(re.escape(value))
    return re.compile("|".join(parts), re.I)


def ticket_text(conversation):
    """The ticket as an inbox would see it, or None when the chat cannot be a fair ticket."""
    leak = _leak_pattern(conversation["scenario"])
    kept = [u for u in opening_utterances(conversation) if not leak.search(u)][:MAX_UTTERANCES]
    if not kept:
        return None
    text = "\n".join(kept)
    if TIER_STATEMENT.search(text) or PURCHASE_AGE.search(text) or PACKAGING_STATEMENT.search(text):
        return None
    return text


def _months_back(day, months):
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def expected_option(tier, purchase_date, packaging, reference_date=REFERENCE_DATE):
    if tier == "gold":
        return "accept"
    if tier == "silver":
        in_window = purchase_date >= _months_back(reference_date, 6)
        return "accept" if in_window or packaging else "ask_for_receipt"
    if tier == "bronze":
        in_window = purchase_date >= reference_date - dt.timedelta(days=90)
        return "accept" if in_window or packaging else "ask_for_receipt"
    if tier == "guest":
        in_window = purchase_date >= reference_date - dt.timedelta(days=30)
        return "accept" if in_window else "ask_for_receipt"
    raise ValueError(f"unknown membership tier {tier!r}")


def rules_by_tier(guidelines):
    return {tier: rule_text(guidelines, tier) for tier in TIERS}


def rule_text(guidelines, tier):
    """The guidelines' Membership Privileges line for the tier, from the return subflows."""
    prefix = f"{tier.title()} members:"
    for subflow in guidelines["Product Defect"]["subflows"].values():
        for action in subflow.get("actions", []):
            if action.get("button") != "Membership Privileges":
                continue
            for line in action.get("subtext", []):
                if line.startswith(prefix):
                    return line
    raise LookupError(f"no Membership Privileges rule for {tier} in the return subflows")


def agent_action(conversation):
    """What the human agent recorded: the tier they entered and whether the return went ahead.

    Agents sometimes typed the wrong thing into the membership field first, so the last
    entry that is a tier counts; anything else is recorded as no tier entered."""
    buttons, entered = set(), None
    for turn in conversation["delexed"]:
        if turn["speaker"] == "action":
            _intent, _step, button, values, _rank = turn["targets"]
            buttons.add(button)
            if button == "membership" and values and values[0] in TIERS:
                entered = values[0]
    return {"membership_entered": entered,
            "return_processed": "update-order" in buttons or "enter-details" in buttons}


def candidate_rows(dataset, guidelines, reference_date=REFERENCE_DATE):
    rules = rules_by_tier(guidelines)
    rows = []
    for convo in return_conversations(dataset):
        text = ticket_text(convo)
        if text is None:
            continue
        personal, order = convo["scenario"]["personal"], convo["scenario"]["order"]
        tier = personal["member_level"]
        purchase_date = dt.date.fromisoformat(order["purchase_date"])
        packaging = order["packaging"] == "yes"
        rows.append({
            "convo_id": convo["convo_id"],
            "subflow": convo["scenario"]["subflow"],
            "text": text,
            "subject": {"customer": personal["username"], "order": order["order_id"]},
            "tier": tier,
            "purchase_date": purchase_date.isoformat(),
            "packaging": packaging,
            "rule": rules[tier],
            "expected": expected_option(tier, purchase_date, packaging, reference_date),
            "agent_action": agent_action(convo),
        })
    return rows


def select_tickets(rows, seed=SEED, count=TICKET_COUNT):
    """Draw `count` rows round-robin over the tier-option cells, one ticket per customer."""
    cells = {}
    for row in sorted(rows, key=lambda r: r["convo_id"]):
        cells.setdefault((row["tier"], row["expected"]), []).append(row)
    if {tier for tier, _ in cells} != set(TIERS) or {option for _, option in cells} != set(OPTIONS):
        raise ValueError(f"the pool must cover every tier and both options; it has {sorted(cells)}")
    order = [(tier, option) for tier in TIERS for option in OPTIONS if (tier, option) in cells]
    rng = random.Random(seed)
    for key in order:
        rng.shuffle(cells[key])
    chosen, customers = [], set()
    while len(chosen) < count and any(cells[key] for key in order):
        for key in order:
            if len(chosen) >= count:
                break
            while cells[key]:
                row = cells[key].pop()
                if row["subject"]["customer"] not in customers:
                    customers.add(row["subject"]["customer"])
                    chosen.append(row)
                    break
    if len(chosen) < count:
        raise ValueError(f"only {len(chosen)} tickets with distinct customers; {count} were asked for")
    return sorted(chosen, key=lambda r: r["convo_id"])


def build(dataset, guidelines, reference_date=REFERENCE_DATE, seed=SEED, count=TICKET_COUNT):
    rows = candidate_rows(dataset, guidelines, reference_date)
    return {
        "question": QUESTION,
        "options": list(OPTIONS),
        "reference_date": reference_date.isoformat(),
        "seed": seed,
        "count": count,
        "subflows": list(RETURN_SUBFLOWS),
        "source": {
            "dataset": "Action-Based Conversations Dataset v1.1 (asappresearch/abcd)",
            "license": LICENSE,
            "dataset_url": DATASET_URL,
            "dataset_sha256": DATASET_SHA256,
            "guidelines_url": GUIDELINES_URL,
            "guidelines_sha256": GUIDELINES_SHA256,
        },
        "rules": rules_by_tier(guidelines),
        "tickets": select_tickets(rows, seed, count),
    }


def render(pinned):
    return (json.dumps(pinned, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR,
                        help="where the raw dataset and guidelines are kept (default: data/abcd)")
    parser.add_argument("--out", type=Path, default=PINNED_PATH,
                        help="the pinned ticket file to write (default: data/tickets.json)")
    parser.add_argument("--check", action="store_true",
                        help="rebuild in memory and report whether --out is reproduced byte for byte")
    args = parser.parse_args(argv)

    dataset_path, guidelines_path = fetch(args.data_dir)
    output = render(build(load_dataset(dataset_path), load_guidelines(guidelines_path)))
    if args.check:
        committed = args.out.read_bytes() if args.out.exists() else None
        if committed == output:
            print(f"{args.out} is reproduced byte for byte")
            return 0
        print(f"{args.out} {'is missing' if committed is None else 'differs from a fresh build'}")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(output)
    pinned = json.loads(output)
    cells = collections.Counter((t["tier"], t["expected"]) for t in pinned["tickets"])
    print(f"wrote {len(pinned['tickets'])} tickets to {args.out} "
          f"(reference date {pinned['reference_date']}, seed {pinned['seed']})")
    for (tier, option), n in sorted(cells.items()):
        print(f"  {tier:7s} {option:16s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
