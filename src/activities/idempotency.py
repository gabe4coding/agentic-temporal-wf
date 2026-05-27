"""Idempotency keys for agent tool side-effects.

Pattern-C rule 6: keys derive from (workflow_id, iteration_id, tool_use_id)
and never from anything the agent generates. A retried activity cannot
double-comment, double-commit, or double-charge."""
from __future__ import annotations

import hashlib


def tool_call_key(workflow_id: str, iteration: int, tool_use_id: str) -> str:
    raw = f"{workflow_id}|{iteration}|{tool_use_id}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def push_operation_key(workflow_id: str, iteration: int) -> str:
    """Deterministic identity for the only approved side effect in the PoC."""
    return tool_call_key(workflow_id, iteration, "push_changes")
