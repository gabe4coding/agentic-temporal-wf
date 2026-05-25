import uuid

import pytest
from pydantic_ai.models.test import TestModel
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin

from src.agents.pr_fixer import temporal_agent
from src.models import PRRef, GitHubEvent, WorkflowState, FixPlan
from src.workflows.pr_autofix import PRAutofixWorkflow


# Stub activities: same names as the real ones, so the worker picks these
# up. No need to monkey-patch the real activity functions.
@activity.defn(name="prepare_workdir")
async def stub_prepare(pr: PRRef) -> None:
    return None


@activity.defn(name="cleanup_workdir")
async def stub_cleanup(pr: PRRef) -> None:
    return None


@activity.defn(name="post_status")
async def stub_post_status(state: WorkflowState, plan: FixPlan) -> WorkflowState:
    state.posted_status_comment_id = state.posted_status_comment_id or 999
    state.last_check_run_id = 42
    return state


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def test_workflow_processes_event_and_returns(env: WorkflowEnvironment):
    test_model = TestModel(custom_output_args={
        "action": "no_action_needed",
        "summary": "no fix needed in test",
    })
    pr = PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="main")
    event = GitHubEvent(kind="pr_opened", delivery_id="d1", payload={})

    # Patch temporal_agent internals so the TestModel is used inside the workflow.
    # TemporalAgent._temporal_overrides() re-applies model=self._temporal_model and
    # toolsets=self._toolsets, which shadows any outer agent.override() call. We must
    # patch these directly so activities use TestModel and no tools fire.
    tm = temporal_agent._temporal_model
    orig_wrapped = tm.wrapped
    orig_default = tm._models_by_id.get("default")
    orig_toolsets = temporal_agent._toolsets

    tm.wrapped = test_model
    tm._models_by_id["default"] = test_model
    temporal_agent._toolsets = []
    try:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[PRAutofixWorkflow],
            activities=[stub_prepare, stub_cleanup, stub_post_status],
            plugins=[PydanticAIPlugin()],
        ):
            handle = await env.client.start_workflow(
                PRAutofixWorkflow.run,
                pr,
                id=f"test-{uuid.uuid4()}",
                task_queue="test-q",
            )
            await handle.signal(PRAutofixWorkflow.on_event, event)
            await handle.signal(PRAutofixWorkflow.close)
            result = await handle.result()
            assert "iteration" in result
            state = await handle.query(PRAutofixWorkflow.get_state)
            assert state.iterations == 1
            assert state.posted_status_comment_id == 999
    finally:
        tm.wrapped = orig_wrapped
        if orig_default is not None:
            tm._models_by_id["default"] = orig_default
        temporal_agent._toolsets = orig_toolsets
