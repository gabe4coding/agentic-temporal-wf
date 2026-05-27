import asyncio
import uuid

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import (
    FixPlan,
    CommitResult,
    OperationRequest,
    OperationResult,
    PRRef,
    SandboxHandle,
    WorkflowState,
)
from src.workflows.pr_autofix import PRAutofixWorkflow


@activity.defn(name="prepare_workdir")
async def _prepare(_pr: PRRef) -> None:
    return None


@activity.defn(name="cleanup_workdir")
async def _cleanup(_pr: PRRef) -> None:
    return None


@activity.defn(name="provision_sandbox")
async def _provision(_pr: PRRef) -> SandboxHandle:
    return SandboxHandle(container_id="cid")


@activity.defn(name="teardown_sandbox")
async def _teardown(_handle: SandboxHandle) -> None:
    return None


@activity.defn(name="post_status")
async def _post(state: WorkflowState, _plan: FixPlan) -> WorkflowState:
    return state


@activity.defn(name="run_agent_iteration")
async def _iteration(_state, _events) -> FixPlan:
    return FixPlan(action="no_action_needed", summary="none")


@activity.defn(name="notify_human_for_approval")
async def _notify(_owner, _repo, _number, _request) -> None:
    return None


@activity.defn(name="push_changes")
async def _push(_pr, _workflow_id, _message, _key) -> CommitResult:
    return CommitResult(pushed=True, commit_sha="abc123")


async def test_push_update_blocks_for_approval_and_records_publish():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="approval-q",
            workflows=[PRAutofixWorkflow],
            activities=[_prepare, _cleanup, _provision, _teardown, _post, _iteration, _notify, _push],
        ):
            handle = await env.client.start_workflow(
                PRAutofixWorkflow.run,
                PRRef(owner="o", repo="r", number=1, head_sha="x", head_ref="f"),
                id=f"approval-{uuid.uuid4()}",
                task_queue="approval-q",
            )
            request = OperationRequest(summary="tests pass", commit_message="autofix: repair")
            update = asyncio.create_task(handle.execute_update("request_push_changes", request))
            state = await handle.query(PRAutofixWorkflow.get_state)
            while not state.operations:
                state = await handle.query(PRAutofixWorkflow.get_state)
            approval_id = next(iter(state.operations.values())).approval_id
            await handle.signal(
                "submit_approval_decision",
                {"approval_id": approval_id, "allowed": True, "reason": "reviewed"},
            )
            result = OperationResult.model_validate(await update)
            assert result.status == "pushed"
            assert result.external_result_id == "abc123"
            repeated = OperationResult.model_validate(
                await handle.execute_update("request_push_changes", request)
            )
            assert repeated.operation_key == result.operation_key
            await handle.signal("close")
            await handle.result()
