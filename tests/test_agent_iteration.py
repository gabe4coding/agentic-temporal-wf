"""Tests for the run_agent_iteration activity.

We mock claude_agent_sdk.query so we don't hit Anthropic. The mock yields
a stream of fake messages culminating in a ResultMessage carrying a JSON
tail that parses into a FixPlan.

NOTE: temporalio 1.27.2's @activity.defn does NOT preserve `.__wrapped__`,
so we test the bare async impl `_run_iteration_impl` directly.
"""
import pytest

from src.activities.agent_iteration import (
    _parse_fix_plan,
    _run_iteration_impl,
)
from src.models import FixPlan, GitHubEvent, PRRef, WorkflowState


def test_parse_fix_plan_from_clean_json_tail():
    text = (
        "Some narrative text.\n"
        "More narrative.\n"
        '{"action":"applied_fix","summary":"fixed lint","addressed_comment_ids":[],'
        '"addressed_failures":["ruff"],"commit_sha":"abc1234","blocking_reason":null}'
    )
    plan = _parse_fix_plan(text)
    assert plan.action == "applied_fix"
    assert plan.commit_sha == "abc1234"
    assert plan.addressed_failures == ["ruff"]


def test_parse_fix_plan_falls_back_when_no_json():
    text = "The agent wandered off without emitting JSON."
    plan = _parse_fix_plan(text)
    assert plan.action == "blocked"
    assert "not parseable" in plan.blocking_reason.lower()


def test_parse_fix_plan_falls_back_on_malformed_json():
    text = "narrative\n{not really json}"
    plan = _parse_fix_plan(text)
    assert plan.action == "blocked"


@pytest.mark.asyncio
async def test_run_agent_iteration_invokes_query_and_returns_plan(monkeypatch):
    """End-to-end (but mocked): activity sets env, calls query, parses plan."""

    # Fake message stream
    class _FakeResultMessage:
        subtype = "success"
        result = (
            '{"action":"no_action_needed","summary":"nothing",'
            '"addressed_comment_ids":[],"addressed_failures":[],'
            '"commit_sha":null,"blocking_reason":null}'
        )

    async def fake_query(prompt, options):
        yield _FakeResultMessage()

    # Make isinstance(msg, ResultMessage) succeed against our fake by
    # patching ResultMessage to the fake's class. Same trick for
    # AssistantMessage so we don't accidentally match.
    monkeypatch.setattr(
        "src.activities.agent_iteration.ResultMessage", _FakeResultMessage
    )

    # Patch the activity's own reference to query so monkeypatching works
    monkeypatch.setattr("src.activities.agent_iteration.query", fake_query)
    # Patch build_options so we don't need GITHUB_TOKEN
    monkeypatch.setattr(
        "src.activities.agent_iteration.build_options", lambda: None
    )

    # Patch activity.info so workdir_id resolves without a real Temporal context
    class _FakeInfo:
        workflow_id = "test-wf"

    monkeypatch.setattr(
        "src.activities.agent_iteration.activity.info", lambda: _FakeInfo()
    )

    pr = PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="main")
    event = GitHubEvent(kind="pr_opened", delivery_id="d1", payload={})
    state = WorkflowState(pr=pr)

    plan = await _run_iteration_impl(state, [event])
    assert isinstance(plan, FixPlan)
    assert plan.action == "no_action_needed"
