"""Pattern-C run_agent_iteration: dispatch + observe.

The activity:
  1. Resolves the workflow's SandboxHandle from state.
  2. Builds the prompt (deterministic from state + events).
  3. Calls dispatch_into_sandbox(handle, prompt) which spawns
     `python -m src.agent_runner.main` inside the sandbox and yields
     JSON-lines from its stdout.
  4. For each line: counts it (for heartbeat detail), keeps a rolling
     reference to the last result message.
  5. Parses the FixPlan out of the result message's trailing JSON.

No in-process claude_agent_sdk.query() call lives here anymore. The
Activity host is a control plane that never executes LLM-generated code.

Caveat: dispatch_into_sandbox uses docker-py's low-level exec_start
socket API. That interface has shifted across docker-py releases; we
pin docker>=7.0 in pyproject. If the attribute name (`sock._sock`)
breaks on a future release, replace with a direct aiohttp call to
/exec/{id}/start (see plan Open Question #4)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import AsyncIterator

from temporalio import activity

from src.models import FixPlan, GitHubEvent, SandboxHandle, WorkflowState


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
    try:
        return FixPlan.model_validate_json(matches[-1].group(0))
    except Exception as e:
        return FixPlan(
            action="blocked",
            summary="Agent FixPlan JSON did not validate.",
            blocking_reason=f"agent output not parseable: {type(e).__name__}",
        )


async def dispatch_into_sandbox(
    handle: SandboxHandle, prompt: str
) -> AsyncIterator[str]:
    """Spawn `python -m src.agent_runner.main` inside the sandbox via the
    Docker exec API and yield stdout lines."""
    import docker

    client = docker.from_env()
    container = client.containers.get(handle.container_id)
    exec_id = client.api.exec_create(
        container.id,
        cmd=["python", "-m", "src.agent_runner.main"],
        stdin=True,
        stdout=True,
        stderr=False,
        workdir=handle.workdir,
    )["Id"]
    sock = client.api.exec_start(
        exec_id, detach=False, tty=False, stream=False, socket=True
    )
    try:
        # docker-py 7.x: the socket attribute is `_sock`. If this breaks
        # on a newer release, switch to aiohttp + /exec/{id}/start (see
        # module docstring + plan Open Question #4).
        sock._sock.sendall(prompt.encode())
        sock._sock.shutdown(1)  # SHUT_WR
        buffer = b""
        while True:
            chunk = sock._sock.recv(65536)
            if not chunk:
                if buffer:
                    yield buffer.decode("utf-8", errors="replace")
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield line.decode("utf-8", errors="replace")
    finally:
        with contextlib.suppress(Exception):
            sock.close()


async def _heartbeat_loop(stop: asyncio.Event, counter: dict) -> None:
    while not stop.is_set():
        with contextlib.suppress(RuntimeError):
            activity.heartbeat(counter)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_S)


async def _run_iteration_impl(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    handle = state.sandbox
    if handle is None:
        return FixPlan(
            action="blocked",
            summary="No sandbox provisioned.",
            blocking_reason="state.sandbox is None — provision_sandbox not run",
        )
    prompt = _build_prompt(state, events)
    counter: dict = {"messages": 0, "tool_calls": 0}
    stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(stop, counter))

    final_text: str = ""
    final_subtype: str | None = None
    try:
        async for raw in dispatch_into_sandbox(handle, prompt):
            counter["messages"] += 1
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "assistant":
                for blk in msg.get("content") or []:
                    if blk.get("type") == "tool_use":
                        counter["tool_calls"] += 1
            elif msg.get("type") == "result":
                final_subtype = msg.get("subtype")
                final_text = msg.get("result") or ""
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await hb_task

    if final_subtype and final_subtype != "success":
        return FixPlan(
            action="blocked",
            summary="Agent terminated abnormally.",
            blocking_reason=f"ResultMessage.subtype={final_subtype}",
        )
    return _parse_fix_plan(final_text)


@activity.defn
async def run_agent_iteration(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    return await _run_iteration_impl(state, events)
