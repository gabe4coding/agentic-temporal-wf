# PoC: PR Autofix Agent — Temporal + Pydantic AI

- **Date**: 2026-05-25
- **Author**: gpavanello
- **Status**: Draft (PoC)
- **Repo**: `agent-temporal`

## 1. Problem statement

Build a small but complete proof-of-concept of an agentic workflow that, given a
GitHub Pull Request, autonomously attempts to fix issues raised by either:

- review comments left by humans on the PR
- failing CI signals: lint/format (ruff), tests (pytest), build errors

The agent must:

- connect to GitHub via the official GitHub MCP server (token auth)
- evaluate which review comments are valid/actionable, and apply fixes
- read CI failures, attempt fixes locally (clone + run lint/tests), then push commits
- post status feedback on the PR (running comment) and a GitHub Check Run

The PoC is intentionally small and readable: a single repo, one workflow type,
a single agent, and a docker-compose stack. It exists to validate the
**Temporal + Pydantic AI** integration shape for agentic workflows at TheFork,
not to ship a production autofix bot.

## 2. Goals / non-goals

### Goals

- Show how a long-lived, signal-driven Temporal workflow drives a Pydantic AI agent.
- Use `pydantic_ai.durable_exec.temporal` so model calls, tool calls, and MCP
  communication are offloaded to activities automatically.
- Have an end-to-end demo: open a PR on a Python "playground" repo, trigger
  webhooks → the agent fixes lint/tests, pushes commits, posts status.

### Non-goals

- Multi-tenant fairness, GitHub App auth (we use a fine-grained PAT).
- Production-grade observability, secret management, sandboxing.
- Auto-merging the PR.
- Languages other than Python in the target repo.
- A polished UI; status is conveyed only on the PR (comment + check run).

## 3. High-level architecture

```
  GitHub
    │  webhook events: pull_request, issue_comment,
    │  pull_request_review_comment, check_suite.completed
    ▼
  ┌────────────────────────┐
  │  Gateway (FastAPI)     │   - verify HMAC signature
  │  POST /webhook         │   - parse event → GitHubEvent
  └─────────┬──────────────┘
            │ Temporal client: signal_with_start
            ▼
  ┌──────────────────────────────────────────────────────────┐
  │  PRAutofixWorkflow(PydanticAIWorkflow)                   │
  │  workflow_id = pr-autofix-{owner}-{repo}-{number}        │
  │                                                          │
  │  State: pending_events, processed_comment_ids,           │
  │         iterations, posted_status_comment_id,            │
  │         last_check_run_id                                │
  │                                                          │
  │  Loop: wait_condition(events) → temporal_agent.run(ctx)  │
  │        → apply_result(state) → post status               │
  │        → continue_as_new if suggested                    │
  └─────────┬────────────────────────────────────────────────┘
            │  TemporalAgent auto-dispatches each step to:
            ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Activities (auto-registered by PydanticAIPlugin)       │
  │   - model call: anthropic:claude-sonnet-4-6             │
  │   - github MCP tool calls (issued via MCPServerStdio)   │
  │   - local repo toolset tool calls                       │
  │                                                         │
  │  Activities (custom, registered explicitly)             │
  │   - prepare_workdir / cleanup_workdir                   │
  │   - post_status (PR comment + check run shortcut)       │
  └─────────────────────────────────────────────────────────┘
```

## 4. Stack and deployment

- Python 3.12
- `temporalio`
- `pydantic-ai[temporal,mcp,anthropic]`
- `fastapi`, `uvicorn`, `httpx`
- LLM provider: Anthropic (`anthropic:claude-sonnet-4-6`)
- GitHub MCP server (`@github/github-mcp-server`) launched via `MCPServerStdio`
- Target repo language: Python only (linters: `ruff`, tests: `pytest`)

`docker-compose.yml` services:

- `temporal` — `temporalio/auto-setup` (in-memory DB, dev only)
- `temporal-ui` — for inspection at `http://localhost:8233`
- `worker` — runs the Python worker (workflows + activities, GitHub MCP server
  child process spawned inside)
- `gateway` — FastAPI HTTP receiver for GitHub webhooks (`POST /webhook`)

For exposing the gateway to GitHub during the PoC, document two options in
`README.md`: smee.io and cloudflared. We do not ship a tunnel container.

## 5. Code layout

```
agent-temporal/
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── README.md
├── docs/
│   └── superpowers/specs/2026-05-25-temporal-pydantic-pr-autofix-design.md
├── src/
│   ├── models.py
│   ├── gateway/
│   │   └── app.py
│   ├── workflows/
│   │   └── pr_autofix.py
│   ├── agents/
│   │   └── pr_fixer.py
│   ├── tools/
│   │   ├── local_repo.py
│   │   └── github_mcp.py
│   ├── activities/
│   │   └── lifecycle.py
│   └── worker.py
└── tests/
    ├── test_workflow.py
    └── test_tools.py
```

Workflow definitions and activities live in separate files (Temporal sandbox
reloads workflow files on every execution; keeping them lean matters).

## 6. Data model (`src/models.py`)

```python
from pydantic import BaseModel
from typing import Literal

class PRRef(BaseModel):
    owner: str
    repo: str
    number: int
    head_sha: str
    head_ref: str        # branch name on the head repo

class GitHubEvent(BaseModel):
    kind: Literal[
        "pr_opened",
        "pr_synchronize",
        "issue_comment",
        "review_comment",
        "check_suite_completed",
    ]
    delivery_id: str     # GitHub X-GitHub-Delivery, for idempotency
    payload: dict        # minimal fields needed; full payload not stored

class AgentDeps(BaseModel):
    """Passed as deps to TemporalAgent.run(). Must be Pydantic-serializable."""
    workdir_id: str      # = workflow_id; tools derive /tmp/autofix-{workdir_id}/repo
    pr: PRRef

class FixPlan(BaseModel):
    """Structured output of every agent iteration."""
    action: Literal["applied_fix", "no_action_needed", "blocked"]
    summary: str
    addressed_comment_ids: list[int] = []
    addressed_failures: list[str] = []
    commit_sha: str | None = None
    blocking_reason: str | None = None

class WorkflowState(BaseModel):
    pr: PRRef
    pending_events: list[GitHubEvent] = []
    processed_delivery_ids: set[str] = set()
    processed_comment_ids: set[int] = set()
    iterations: int = 0
    posted_status_comment_id: int | None = None
    last_check_run_id: int | None = None
    closed: bool = False
```

## 7. Workflow (`src/workflows/pr_autofix.py`)

Key responsibilities (deterministic only):

- Initialize state from `PRRef` on first start or from snapshot on
  `continue_as_new`.
- Signal handlers append events to the queue (no I/O in handlers).
- Main loop: wait for events → invoke agent → apply result → post status →
  check budget and continue-as-new.

Skeleton:

```python
import asyncio
from datetime import timedelta
from temporalio import workflow
from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow

with workflow.unsafe.imports_passed_through():
    from src.agents.pr_fixer import temporal_agent
    from src.models import PRRef, GitHubEvent, WorkflowState, AgentDeps, FixPlan
    from src.activities.lifecycle import prepare_workdir, cleanup_workdir, post_status

MAX_ITERATIONS = 5
IDLE_TIMEOUT = timedelta(minutes=30)

@workflow.defn(name="PRAutofixWorkflow")
class PRAutofixWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [temporal_agent]

    # Use @workflow.init so state exists before any signal lands. A signal
    # delivered before run() schedules would otherwise hit AttributeError.
    @workflow.init
    def __init__(self, init: PRRef | WorkflowState) -> None:
        self._state: WorkflowState = (
            init if isinstance(init, WorkflowState) else WorkflowState(pr=init)
        )

    @workflow.signal
    async def on_event(self, event: GitHubEvent) -> None:
        if event.delivery_id in self._state.processed_delivery_ids:
            return
        self._state.processed_delivery_ids.add(event.delivery_id)
        self._state.pending_events.append(event)

    @workflow.signal
    async def close(self) -> None:
        self._state.closed = True

    @workflow.query
    def get_state(self) -> WorkflowState:
        return self._state

    @workflow.run
    async def run(self, init: PRRef | WorkflowState) -> str:
        # prepare_workdir is idempotent (mkdir -p, clone-or-fetch) so it is
        # safe both on first start and after continue_as_new.
        await workflow.execute_activity(
            prepare_workdir,
            self._state.pr,
            start_to_close_timeout=timedelta(minutes=5),
        )
        do_cleanup = True
        try:
            while not self._state.closed and self._state.iterations < MAX_ITERATIONS:
                try:
                    await workflow.wait_condition(
                        lambda: bool(self._state.pending_events) or self._state.closed,
                        timeout=IDLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    # Idle timeout reached; close the workflow.
                    self._state.closed = True
                    break

                if self._state.closed:
                    break

                self._state.iterations += 1
                events_snapshot = list(self._state.pending_events)
                self._state.pending_events.clear()

                deps = AgentDeps(workdir_id=workflow.info().workflow_id, pr=self._state.pr)
                result = await temporal_agent.run(
                    self._build_prompt(events_snapshot),
                    deps=deps,
                )
                plan: FixPlan = result.output
                self._apply_plan(plan)

                await workflow.execute_activity(
                    post_status,
                    args=[self._state, plan],
                    start_to_close_timeout=timedelta(seconds=60),
                )

                if workflow.info().is_continue_as_new_suggested():
                    # Keep the workdir for the next execution; the new run
                    # will re-enter prepare_workdir (idempotent fetch).
                    do_cleanup = False
                    workflow.continue_as_new(self._state)
        finally:
            if do_cleanup:
                await workflow.execute_activity(
                    cleanup_workdir,
                    self._state.pr,
                    start_to_close_timeout=timedelta(minutes=2),
                )
        return f"done after {self._state.iterations} iterations"

    def _build_prompt(self, events: list[GitHubEvent]) -> str:
        # Deterministic. Returns a markdown brief composed of:
        #   - self._state.pr (owner/repo/number/head_sha)
        #   - self._state.iterations
        #   - a bullet list of the events snapshot, with stable IDs
        #     (delivery_id, comment id, check_run id) so the agent can
        #     correlate them with what it fetches via GitHub MCP.
        # The agent is responsible for fetching the full PR context itself.
        ...

    def _apply_plan(self, plan: FixPlan) -> None:
        # Deterministic. Updates state:
        #   - processed_comment_ids |= set(plan.addressed_comment_ids)
        # No I/O; no activity calls (signals/handlers stay pure).
        ...
```

Notes:

- Signal handlers are synchronous and side-effect free beyond mutating
  workflow state. No activity calls from inside signals.
- The agent prompt is built deterministically from state + the events
  snapshot. The agent itself fetches richer context via GitHub MCP tools.
- `prepare_workdir` / `cleanup_workdir` are explicit activities (not agent
  tools) because they bound the workdir lifecycle to the workflow lifecycle.
- `post_status` is also a plain activity because it must run reliably even
  after an agent run fails — we want users to see the failure on the PR.

## 8. Agent (`src/agents/pr_fixer.py`)

```python
from pydantic_ai import Agent
from pydantic_ai.durable_exec.temporal import TemporalAgent
from src.models import AgentDeps, FixPlan
from src.tools.github_mcp import github_mcp_server
from src.tools.local_repo import local_repo_toolset

agent = Agent(
    "anthropic:claude-sonnet-4-6",
    name="pr_fixer",                                  # stable identity (required by Temporal)
    deps_type=AgentDeps,
    output_type=FixPlan,
    toolsets=[github_mcp_server, local_repo_toolset], # both have stable IDs
    instructions=(
        "You are an autonomous code-review assistant. You receive a Pull "
        "Request and a list of pending events (new comments, failed CI). "
        "For each event, decide whether it is a valid, actionable request, "
        "then apply the smallest possible fix. After every edit, run ruff "
        "and pytest. Only commit & push if local verification passes. "
        "If a comment is opinion-only, unclear, or out of scope, do not "
        "apply it and explain why in the FixPlan. Always return a FixPlan."
    ),
)

# Disable HTTP retries inside the model client; Temporal owns retries.
# (Wire this through provider settings when constructing the Anthropic client
# or via env: ANTHROPIC_MAX_RETRIES=0)

temporal_agent = TemporalAgent(agent)
```

Agent and toolset names/IDs are stable strings (they become part of activity
names in workflow history); we will not rename them.

HTTP retries inside the model client are disabled (the exact knob —
`AnthropicProvider(...)`, or the underlying `anthropic.AsyncAnthropic`
constructor's `max_retries=0` — is settled at implementation time). The
goal is one retry-policy source of truth: Temporal's activity retry policy.
We avoid double-retry storms across the LLM client + Temporal.

## 9. Tools

### 9.1 GitHub MCP (`src/tools/github_mcp.py`)

```python
import os
from pydantic_ai.mcp import MCPServerStdio

github_mcp_server = MCPServerStdio(
    "npx",
    args=["-y", "@github/github-mcp-server"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_TOKEN"]},
    id="github",
    timeout=15,
)
```

The agent uses (at minimum): `get_pull_request`, `list_review_comments`,
`list_issue_comments`, `list_check_runs`, `create_issue_comment`,
`update_issue_comment`.

### 9.2 Local repo toolset (`src/tools/local_repo.py`)

A `FunctionToolset(id="repo")` exposing:

- `read_file(path: str) -> str`
- `list_files(glob: str = "**/*.py") -> list[str]`
- `apply_edit(path: str, new_content: str) -> str` — writes the full file
  content; returns the new file SHA-1 for confirmation
- `run_ruff() -> RuffResult` — runs `ruff check . --output-format=json`
- `run_pytest(target: str | None = None) -> PytestResult` — runs `pytest -q`
  in the workdir
- `git_status() -> GitStatus` — porcelain status, current branch, ahead/behind
- `git_commit_and_push(message: str) -> CommitResult` — stages all, commits,
  fetches origin, fast-forward checks, pushes. Refuses to push if remote is
  ahead (returns `pushed=False, reason="remote_advanced"`).

Every tool reads `ctx.deps.workdir_id` to locate `/tmp/autofix-{workdir_id}/repo`.
We intentionally use `apply_edit(path, new_content)` instead of unified diff
application because models are more reliable at writing whole files for the
small PoC repo, and verification is delegated to ruff/pytest anyway.

## 10. Lifecycle activities (`src/activities/lifecycle.py`)

```python
@activity.defn
def prepare_workdir(pr: PRRef) -> None:
    # Idempotent. Safe to re-enter after continue_as_new (workdir kept) or
    # after an activity retry mid-execution.
    # workflow_id available via activity.info().workflow_id
    # 1. mkdir -p /tmp/autofix-{workflow_id}/repo
    # 2. if not a git repo:
    #        git clone --depth=50 https://x-access-token:$GITHUB_TOKEN@github.com/{owner}/{repo}
    # 3. git fetch origin pull/{number}/head:autofix
    # 4. git checkout autofix && git reset --hard FETCH_HEAD
    ...

@activity.defn
def cleanup_workdir(pr: PRRef) -> None:
    # rm -rf the workdir for this workflow_id
    ...

@activity.defn
async def post_status(state: WorkflowState, plan: FixPlan) -> WorkflowState:
    # 1. compose status markdown from state.iterations + plan
    # 2. if state.posted_status_comment_id is None:
    #       create issue comment via GitHub REST; store id back into state
    #    else:
    #       update that issue comment in place
    # 3. create/update check run with conclusion derived from plan.action
    # 4. return mutated state (workflow assigns it back)
    ...
```

`post_status` returning the updated state lets the workflow keep
`posted_status_comment_id` and `last_check_run_id` durable.

## 11. Worker (`src/worker.py`)

```python
import asyncio
from temporalio.client import Client
from temporalio.worker import Worker
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin

from src.workflows.pr_autofix import PRAutofixWorkflow
from src.activities.lifecycle import prepare_workdir, cleanup_workdir, post_status

async def main() -> None:
    # PydanticAIPlugin installs a Pydantic-aware data converter on the
    # client; we do NOT pass `data_converter=pydantic_data_converter`
    # separately to avoid double-wrapping. Verify at implementation time.
    client = await Client.connect(
        "temporal:7233",
        plugins=[PydanticAIPlugin()],
    )
    async with Worker(
        client,
        task_queue="pr-autofix",
        workflows=[PRAutofixWorkflow],
        activities=[prepare_workdir, cleanup_workdir, post_status],
    ):
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
```

## 12. Gateway (`src/gateway/app.py`)

`POST /webhook` flow:

1. Read raw body, verify `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`.
2. Parse `X-GitHub-Event` and the payload; project it to a `GitHubEvent`.
3. Derive `PRRef` from the payload.
4. Call:
   ```python
   await client.start_workflow(
       PRAutofixWorkflow.run,
       PRRef(...),
       id=f"pr-autofix-{owner}-{repo}-{number}",
       task_queue="pr-autofix",
       start_signal="on_event",
       start_signal_args=[event],
       id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
   )
   ```
   (`signal_with_start` semantics: start if absent, signal if present.)
5. Return `202 Accepted`.

`id_reuse_policy=ALLOW_DUPLICATE` means a new webhook delivery arriving
after a previous execution has *closed* (e.g., long after the idle timeout)
starts a fresh execution. Within a single execution, duplicate deliveries
are deduped via `processed_delivery_ids` in workflow state — that set is
NOT shared across executions; new executions start with it empty, which is
fine because GitHub doesn't replay deliveries across long gaps.

Webhook event → `GitHubEvent.kind` mapping:

| GitHub event                        | Kind                    |
|-------------------------------------|-------------------------|
| `pull_request.opened`               | `pr_opened`             |
| `pull_request.synchronize`          | `pr_synchronize`        |
| `issue_comment.created` (on PR)     | `issue_comment`         |
| `pull_request_review_comment.created` | `review_comment`      |
| `check_suite.completed`             | `check_suite_completed` |

All other events are dropped with `204 No Content`.

## 13. Error handling and guardrails

- Pydantic AI / Anthropic client: `max_retries=0`. Temporal owns retries.
- LLM activity errors classified by the integration; `AuthenticationError`
  and `ContentPolicyError` are non-retryable and bubble up.
- Tool activities have default retry policies; `git_commit_and_push`
  returns a structured failure (no exception) for "remote advanced" so
  the agent can decide what to do.
- **Iteration budget**: `MAX_ITERATIONS = 5` per logical PR (persisted in
  `WorkflowState.iterations`, preserved across `continue_as_new`). With this
  ceiling, continue-as-new is unlikely to actually fire for a PoC — the
  code path exists to make the design honest about long-lived workflows
  but is rarely exercised at the configured budget.
- **Idle timeout**: 30 minutes since last event closes the workflow.
- **Concurrency on the same PR**: workflow_id is keyed by PR, so two
  concurrent webhook deliveries result in one execution with two signals.
- **Webhook signature**: HMAC SHA-256 verified before any Temporal call.
- **Push race**: `git_commit_and_push` fetches origin and refuses if
  `head_ref` advanced behind us; the agent then re-plans.

## 14. Testing

- `tests/test_workflow.py` — `WorkflowEnvironment.start_time_skipping()`,
  agent overridden with `TestModel` (or `FunctionModel` for branchy cases),
  send synthetic `GitHubEvent` signals, assert that `post_status` is called
  with the expected `FixPlan.action`. Mock `prepare_workdir`/`cleanup_workdir`/
  `post_status` activities.
- `tests/test_tools.py` — initialize a tmp git repo with a deliberate ruff
  violation and a failing pytest; assert that `apply_edit` + `run_ruff` +
  `run_pytest` + `git_commit_and_push` produce a passing state and a
  pushed commit (against a local bare repo as origin).
- No end-to-end test in CI; the README documents how to run a manual
  smoke test against a real GitHub playground repo.

## 15. Out of scope (explicit)

- Multi-tenant fairness, Task Queue priorities.
- GitHub App authentication, fine-grained permissions per repo.
- Sandboxed test execution (we run `pytest` in the worker container — fine for
  a PoC playground repo, **not** for arbitrary user repos).
- Streaming agent events to a UI.
- Logfire / advanced observability — to be added in a follow-up.
- Auto-merge or label-driven gating.
- Languages other than Python.

## 16. Open questions

- Should `post_status` write a single rolling comment (current design) or
  one comment per iteration? Current default: rolling — less PR noise.
- Should we add a hard ceiling on tokens per workflow execution? Probably yes
  before any non-playground use; out of scope for the PoC milestone.
- The GitHub MCP server is launched per-worker as an `MCPServerStdio` child
  process — we assume it can be shared across concurrent agent runs in the
  same worker. Verify in implementation; fall back to one process per agent
  invocation if needed.
