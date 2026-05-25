"""Temporal activity wrapping one full Claude Agent SDK iteration.

The activity:
  1. Sets AUTOFIX_WORKDIR_ID env var so SDK MCP tools can resolve workdir
  2. Builds ClaudeAgentOptions
  3. Starts a background heartbeat task (every 30s)
  4. Iterates `async for msg in query(prompt, options)`:
       - logs tool calls (best-effort, via AssistantMessage block inspect)
       - captures the final ResultMessage.result text
  5. Parses the trailing JSON line into a FixPlan
  6. Returns the FixPlan

NOTE: temporalio 1.27+ does not preserve `__wrapped__` on activities, so
the real logic lives in `_run_iteration_impl` and `run_agent_iteration`
is a thin pass-through. Unit tests call `_run_iteration_impl` directly.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    query,
)
from temporalio import activity

from src.agents.pr_fixer import build_options
from src.models import FixPlan, GitHubEvent, WorkflowState


logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL_S = 30


def _build_prompt(state: WorkflowState, events: list[GitHubEvent]) -> str:
    pr = state.pr
    lines = [
        f"PR: {pr.owner}/{pr.repo}#{pr.number} (head {pr.head_sha[:7]} on {pr.head_ref})",
        f"Iteration: {state.iterations}",
        "",
        "Pending events:",
    ]
    for e in events:
        lines.append(
            f"- [{e.kind}] delivery={e.delivery_id} "
            f"payload_keys={sorted(e.payload.keys())}"
        )
    return "\n".join(lines)


_JSON_TAIL_RE = re.compile(r"\{[^{}]*\"action\"[^{}]*\}", re.DOTALL)


def _parse_fix_plan(text: str) -> FixPlan:
    """Find the trailing JSON object in the agent's final text and parse
    it as FixPlan. Fall back to a blocked FixPlan if no JSON tail is
    findable or it doesn't validate."""
    if not text:
        return FixPlan(
            action="blocked",
            summary="Agent produced no final output.",
            blocking_reason="agent output not parseable: empty",
        )
    matches = list(_JSON_TAIL_RE.finditer(text))
    if not matches:
        return FixPlan(
            action="blocked",
            summary="Agent did not emit a FixPlan JSON tail.",
            blocking_reason=(
                "agent output not parseable: no JSON object containing 'action'"
            ),
        )
    candidate = matches[-1].group(0)
    try:
        return FixPlan.model_validate_json(candidate)
    except Exception as e:
        return FixPlan(
            action="blocked",
            summary="Agent FixPlan JSON did not validate.",
            blocking_reason=f"agent output not parseable: {type(e).__name__}",
        )


async def _heartbeat_loop(stop: asyncio.Event, counter: dict) -> None:
    """Background task: heartbeat every HEARTBEAT_INTERVAL_S until stopped."""
    while not stop.is_set():
        try:
            activity.heartbeat(counter)
        except RuntimeError:
            # Outside an activity (e.g., unit test) — nothing to heartbeat to.
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


async def _run_iteration_impl(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    """Bare async implementation. Exposed so tests can bypass the
    @activity.defn wrapper (temporalio 1.27+ does not preserve __wrapped__).
    """
    # Activity context (skipped in unit tests via monkeypatch)
    try:
        workflow_id = activity.info().workflow_id
    except Exception:
        workflow_id = "unit-test"
    from src.tools._workdir import set_workdir_id, reset_workdir_id
    workdir_token = set_workdir_id(workflow_id)
    try:
        prompt = _build_prompt(state, events)
        options = build_options()

        counter: dict = {"assistant_messages": 0, "tool_calls": 0}
        stop = asyncio.Event()
        hb_task = asyncio.create_task(_heartbeat_loop(stop, counter))

        final_text: str = ""
        try:
            async for msg in query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    counter["assistant_messages"] += 1
                    # Best-effort tool-call counter from the message's blocks
                    blocks = getattr(msg, "content", None) or []
                    for blk in blocks:
                        if getattr(blk, "type", None) == "tool_use":
                            counter["tool_calls"] += 1
                elif isinstance(msg, ResultMessage):
                    if getattr(msg, "subtype", None) == "success":
                        final_text = getattr(msg, "result", "") or ""
                    else:
                        return FixPlan(
                            action="blocked",
                            summary="Agent terminated abnormally.",
                            blocking_reason=f"ResultMessage.subtype={msg.subtype}",
                        )
        finally:
            stop.set()
            with contextlib.suppress(Exception):
                await hb_task

        return _parse_fix_plan(final_text)
    finally:
        reset_workdir_id(workdir_token)


@activity.defn
async def run_agent_iteration(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    """One full agent iteration. Black-box from Temporal's perspective.

    Thin pass-through to `_run_iteration_impl` so unit tests can call the
    bare async function (temporalio's @activity.defn does not preserve
    `__wrapped__` in current versions).
    """
    return await _run_iteration_impl(state, events)
