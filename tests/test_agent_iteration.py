import json

import pytest

from src.activities.agent_iteration import _run_iteration_impl
from src.models import FixPlan, GitHubEvent, PRRef, SandboxHandle, WorkflowState


@pytest.mark.asyncio
async def test_dispatch_parses_result_line(monkeypatch):
    state = WorkflowState(
        pr=PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="f"),
        sandbox=SandboxHandle(container_id="cid"),
    )
    events: list[GitHubEvent] = []

    fake_stream = [
        json.dumps({"type": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "result": (
                'Done.\n{"action":"no_action_needed","summary":"nothing to do",'
                '"addressed_comment_ids":[],"addressed_failures":[],'
                '"commit_sha":null,"blocking_reason":null}'
            ),
        }),
    ]

    async def fake_dispatch(handle, prompt):
        for line in fake_stream:
            yield line

    monkeypatch.setattr(
        "src.activities.agent_iteration.dispatch_into_sandbox", fake_dispatch
    )

    plan = await _run_iteration_impl(state, events)
    assert isinstance(plan, FixPlan)
    assert plan.action == "no_action_needed"


@pytest.mark.asyncio
async def test_blocked_when_no_sandbox():
    state = WorkflowState(
        pr=PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="f"),
    )
    plan = await _run_iteration_impl(state, [])
    assert plan.action == "blocked"
    assert "sandbox" in (plan.blocking_reason or "").lower()


@pytest.mark.asyncio
async def test_abnormal_subtype_blocks(monkeypatch):
    state = WorkflowState(
        pr=PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="f"),
        sandbox=SandboxHandle(container_id="cid"),
    )

    async def fake_dispatch(handle, prompt):
        yield json.dumps({"type": "result", "subtype": "error_max_turns", "result": ""})

    monkeypatch.setattr(
        "src.activities.agent_iteration.dispatch_into_sandbox", fake_dispatch
    )

    plan = await _run_iteration_impl(state, [])
    assert plan.action == "blocked"
    assert "error_max_turns" in (plan.blocking_reason or "")
