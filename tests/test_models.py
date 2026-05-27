from src.models import (
    PRRef,
    GitHubEvent,
    AgentDeps,
    FixPlan,
    WorkflowState,
    OperationRequest,
    OperationResult,
    RunContext,
)


def test_pr_ref_round_trip():
    pr = PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="feature-x")
    assert PRRef.model_validate_json(pr.model_dump_json()) == pr


def test_github_event_round_trip():
    e = GitHubEvent(kind="pr_opened", delivery_id="d1", payload={"k": "v"})
    assert GitHubEvent.model_validate_json(e.model_dump_json()) == e


def test_agent_deps_serializes():
    pr = PRRef(owner="o", repo="r", number=1, head_sha="a", head_ref="b")
    d = AgentDeps(workdir_id="wf-1", pr=pr)
    assert AgentDeps.model_validate_json(d.model_dump_json()) == d


def test_fix_plan_minimal_default():
    plan = FixPlan(action="no_action_needed", summary="nothing to do")
    assert plan.addressed_comment_ids == []
    assert plan.commit_sha is None


def test_workflow_state_defaults():
    pr = PRRef(owner="o", repo="r", number=1, head_sha="a", head_ref="b")
    s = WorkflowState(pr=pr)
    assert s.iterations == 0
    assert s.pending_events == []
    assert s.processed_delivery_ids == set()
    assert s.processed_comment_ids == set()
    assert s.closed is False
    assert s.sandbox is None


def test_workflow_state_carries_sandbox_handle():
    from src.models import SandboxHandle

    pr = PRRef(owner="o", repo="r", number=1, head_sha="a", head_ref="b")
    s = WorkflowState(pr=pr, sandbox=SandboxHandle(container_id="cid", workdir="/work"))
    s2 = WorkflowState.model_validate_json(s.model_dump_json())
    assert s2.sandbox is not None
    assert s2.sandbox.container_id == "cid"


def test_generic_platform_contracts_serialize():
    context = RunContext(
        workflow_id="wf", workload_type="pr_autofix", workspace_ref="workspace/wf"
    )
    request = OperationRequest(summary="done", commit_message="fix")
    result = OperationResult(operation_key="key", status="pending")
    assert RunContext.model_validate_json(context.model_dump_json()) == context
    assert request.commit_message == "fix"
    assert result.status == "pending"
