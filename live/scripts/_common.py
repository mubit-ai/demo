"""Shared helpers for the conference demo."""

import json
import os

from mubit import Client

SESSION = "demo:devteam:sprint-42"

ENDPOINT = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
TRANSPORT = os.environ.get("MUBIT_TRANSPORT", "http")
API_KEY = os.environ["MUBIT_API_KEY"]


def make_client(run_id: str = SESSION) -> Client:
    client = Client(
        endpoint=ENDPOINT,
        api_key=API_KEY,
        run_id=run_id,
    )
    client.set_transport(TRANSPORT)
    return client


def pp(obj):
    """Pretty-print a dict/list as indented JSON."""
    print(json.dumps(obj, indent=2, default=str))
