from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SandboxHandle(BaseModel):
    """Non-secret identity and mount projection for an untrusted sandbox."""

    container_id: str
    workdir: str = "/work"


class SnapshotRef(BaseModel):
    """Reference to a sandbox snapshot.

    For the PoC the snapshot is a local Docker image tag. Production
    target stores the layered image in S3 (`s3_bucket` / `s3_key`
    populated by Phase 9.2 follow-up); see Open Question #3 for cost."""

    image_tag: str
    iteration: int
    s3_bucket: str | None = None
    s3_key: str | None = None


class ExecResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class CommitResult(BaseModel):
    pushed: bool
    commit_sha: str | None = None
    reason: str | None = None


class PRRef(BaseModel):
    owner: str
    repo: str
    number: int
    head_sha: str
    head_ref: str


class RunContext(BaseModel):
    """Generic durable identity for one workload execution."""

    workflow_id: str
    run_id: str | None = None
    workload_type: str
    subject: dict = Field(default_factory=dict)
    workspace_ref: str


class CapabilityBinding(BaseModel):
    """Broker-side authorization record. The opaque token is its lookup key."""

    workflow_id: str
    repository: str
    pr_number: int
    workspace_path: str
    capabilities: set[str] = Field(default_factory=set)
    expires_at: datetime
    iteration: int = 0


class GitHubEvent(BaseModel):
    kind: Literal[
        "pr_opened",
        "pr_synchronize",
        "issue_comment",
        "review_comment",
        "check_suite_completed",
    ]
    delivery_id: str
    payload: dict


class AgentDeps(BaseModel):
    workdir_id: str
    pr: PRRef


class FixPlan(BaseModel):
    action: Literal["applied_fix", "no_action_needed", "blocked"]
    summary: str
    addressed_comment_ids: list[int] = Field(default_factory=list)
    addressed_failures: list[str] = Field(default_factory=list)
    commit_sha: str | None = None
    blocking_reason: str | None = None


class OperationRequest(BaseModel):
    summary: str
    commit_message: str


class OperationResult(BaseModel):
    operation_key: str
    status: Literal["pending", "approved", "denied", "pushed", "failed"]
    approval_id: str | None = None
    approval_decision: bool | None = None
    external_result_id: str | None = None
    reason: str = ""


class WorkflowState(BaseModel):
    pr: PRRef
    pending_events: list[GitHubEvent] = Field(default_factory=list)
    processed_delivery_ids: set[str] = Field(default_factory=set)
    processed_comment_ids: set[int] = Field(default_factory=set)
    iterations: int = 0
    posted_status_comment_id: int | None = None
    last_check_run_id: int | None = None
    closed: bool = False
    sandbox: SandboxHandle | None = None
    operations: dict[str, OperationResult] = Field(default_factory=dict)


# ---------- HITL approval (Pattern-C rule 7) ----------


class ApprovalRequest(BaseModel):
    approval_id: str
    tool_name: str
    tool_input: dict
    iteration: int


class ApprovalDecision(BaseModel):
    allowed: bool
    reason: str = ""
    modified_input: dict | None = None


class ApprovalState(BaseModel):
    approval_id: str
    pending: bool = True
    allowed: bool = False
    reason: str = ""

    @property
    def decided(self) -> bool:
        return not self.pending

    def to_decision(self) -> ApprovalDecision:
        return ApprovalDecision(allowed=self.allowed, reason=self.reason)
