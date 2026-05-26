import concurrent.futures

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.activities.approval import notify_human_for_approval
from src.models import ApprovalDecision, ApprovalRequest, PRRef
from src.workflows.pr_autofix import PRAutofixWorkflow


@pytest.mark.asyncio
async def test_workflow_update_blocks_until_signal(monkeypatch):
    # Stub the notification activity so the test doesn't hit GitHub.
    from temporalio import activity

    @activity.defn(name="notify_human_for_approval")
    async def fake_notify(pr_owner, pr_repo, pr_number, req):
        return None

    # Also stub the other activities the workflow imports so the worker
    # can be constructed even if their dependencies (docker, etc.) are
    # absent in the test env.
    from src.activities import (
        lifecycle as lifecycle_mod,
        sandbox as sandbox_mod,
        agent_iteration as agent_iter_mod,
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            async with Worker(
                env.client,
                task_queue="t",
                workflows=[PRAutofixWorkflow],
                activities=[
                    fake_notify,
                    lifecycle_mod.prepare_workdir,
                    lifecycle_mod.cleanup_workdir,
                    lifecycle_mod.post_status,
                    sandbox_mod.provision_sandbox,
                    sandbox_mod.teardown_sandbox,
                    agent_iter_mod.run_agent_iteration,
                ],
                activity_executor=executor,
            ):
                handle = await env.client.start_workflow(
                    PRAutofixWorkflow.run,
                    PRRef(owner="o", repo="r", number=1, head_sha="x", head_ref="f"),
                    id="wf-approval-test",
                    task_queue="t",
                )
                update_task = handle.execute_update(
                    "request_tool_approval",
                    ApprovalRequest(
                        approval_id="a1",
                        tool_name="Bash",
                        tool_input={"command": "git push origin main"},
                        iteration=1,
                    ),
                )
                await handle.signal(
                    "submit_approval_decision",
                    {"approval_id": "a1", "allowed": True, "reason": "ok"},
                )
                decision: ApprovalDecision = await update_task
                assert decision.allowed is True
                assert decision.reason == "ok"

                # Cleanup
                await handle.signal("close")
                await handle.result()
