"""JSON-lines codec for SDK messages crossing the sandbox boundary.

Each line on the sandbox's stdout is one JSON object. The Activity host
parses these one at a time so the heartbeat can fire on each tick."""
from __future__ import annotations
import json
from typing import Iterable, Iterator


def encode_message(msg: dict) -> str:
    return json.dumps(msg, default=str)


def decode_messages(lines: Iterable[str]) -> Iterator[dict]:
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue
