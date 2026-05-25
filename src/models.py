from typing import Literal

from pydantic import BaseModel, Field


class SandboxHandle(BaseModel):
    """Identifies a per-workflow sandbox container. Lives in WorkflowState."""

    container_id: str
    workdir: str = "/work"


class ExecResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class PRRef(BaseModel):
    owner: str
    repo: str
    number: int
    head_sha: str
    head_ref: str


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
