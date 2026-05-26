"""In-sandbox entrypoint.

Reads a prompt from stdin, runs claude_agent_sdk.query() with the
options built locally (the SDK plugins point at /plugins/*), and writes
each message to stdout as one JSON line. The Activity host parses the
stream, heartbeats per message, and extracts the FixPlan from the
trailing ResultMessage."""
from __future__ import annotations

import asyncio
import sys

from claude_agent_sdk import query, AssistantMessage, ResultMessage

from src.agent_runner.stream_codec import encode_message
# IMPORTANT: build_options is imported lazily because it imports
# httpx etc. which fail-fast on missing env in some test harnesses.


def _serialise(msg) -> dict:
    if isinstance(msg, AssistantMessage):
        return {
            "type": "assistant",
            "content": [
                {
                    "type": getattr(b, "type", None),
                    "text": getattr(b, "text", None),
                    "id": getattr(b, "id", None),
                    "name": getattr(b, "name", None),
                    "input": getattr(b, "input", None),
                }
                for b in (getattr(msg, "content", None) or [])
            ],
        }
    if isinstance(msg, ResultMessage):
        return {
            "type": "result",
            "subtype": getattr(msg, "subtype", None),
            "result": getattr(msg, "result", None),
        }
    return {"type": "other"}


async def _amain() -> int:
    from src.agents.pr_fixer import build_options

    prompt = sys.stdin.read()
    options = build_options()
    async for msg in query(prompt=prompt, options=options):
        sys.stdout.write(encode_message(_serialise(msg)) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:  # exposed for the in-sandbox shell entrypoint
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
