import uuid

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import PRRef, GitHubEvent, WorkflowState, FixPlan, SandboxHandle
from src.workflows.pr_autofix import PRAutofixWorkflow


# Stub activities by name so the workflow picks these up instead of the real ones.
@activity.defn(name="prepare_workdir")
async def stub_prepare(pr: PRRef) -> None:
    return None


@activity.defn(name="cleanup_workdir")
async def stub_cleanup(pr: PRRef) -> None:
    return None


@activity.defn(name="provision_sandbox")
async def stub_provision_sandbox(pr: PRRef) -> SandboxHandle:
    return SandboxHandle(container_id="stub-cid", workdir="/tmp/stub/repo")


@activity.defn(name="teardown_sandbox")
async def stub_teardown_sandbox(handle: SandboxHandle) -> None:
    return None


@activity.defn(name="post_status")
async def stub_post_status(state: WorkflowState, plan: FixPlan) -> WorkflowState:
    state.posted_status_comment_id = state.posted_status_comment_id or 999
    state.last_check_run_id = 42
    return state


@activity.defn(name="run_agent_iteration")
async def stub_run_agent_iteration(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    return FixPlan(
        action="no_action_needed",
        summary="stubbed in test",
    )


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def test_workflow_processes_event_and_returns(env: WorkflowEnvironment):
    pr = PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="main")
    event = GitHubEvent(kind="pr_opened", delivery_id="d1", payload={})

    async with Worker(
        env.client,
        task_queue="test-q",
        workflows=[PRAutofixWorkflow],
        activities=[
            stub_prepare,
            stub_cleanup,
            stub_provision_sandbox,
            stub_teardown_sandbox,
            stub_post_status,
            stub_run_agent_iteration,
        ],
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
        assert state.sandbox is not None
        assert state.sandbox.container_id == "stub-cid"
