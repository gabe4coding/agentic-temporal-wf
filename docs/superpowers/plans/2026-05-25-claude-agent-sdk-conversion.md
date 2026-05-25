# Claude Agent SDK Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Pydantic AI agent layer with the Claude Agent SDK while preserving the entire Temporal workflow/gateway/lifecycle scaffolding. The agent loop becomes a single long-running Temporal activity per iteration; tools include the SDK's builtin Read/Edit/Grep/Glob plus our custom repo MCP plus the external GitHub MCP.

**Architecture:** New activity `run_agent_iteration(state, events) -> FixPlan` invokes `claude_agent_sdk.query(prompt, options)` with `allowed_tools=["Read","Edit","Grep","Glob","mcp__github__*","mcp__repo__*"]` and `permission_mode="bypassPermissions"`. The custom repo MCP is built via `create_sdk_mcp_server`. Workdir is communicated to tools via `AUTOFIX_WORKDIR_ID` env var set by the activity. Heartbeating handled by a background asyncio task. `FixPlan` is parsed from a JSON tail the agent is prompted to emit.

**Tech Stack:** Python 3.12, `temporalio`, `claude-agent-sdk` (replaces `pydantic-ai`), `fastapi` (unchanged), `httpx` (unchanged), `pytest` + `pytest-asyncio`. Same Anthropic model under the hood (`claude-sonnet-4-6`).

**Spec:** `docs/superpowers/specs/2026-05-25-claude-agent-sdk-conversion-design.md`
**Branch:** `impl/pr-autofix-claude-sdk` (branches from `impl/pr-autofix-poc`)

---

## File layout produced by this plan

```
agent-temporal/
├── pyproject.toml                       # Task 1 (deps swap)
├── src/
│   ├── models.py                        # unchanged
│   ├── tools/
│   │   ├── _workdir.py                  # Task 4 (add env-var helper)
│   │   ├── _local_repo_impl.py          # unchanged
│   │   ├── local_repo.py                # Task 5 (rewrite as SDK MCP)
│   │   └── github_mcp.py                # Task 3 (simplified to a config dict)
│   ├── activities/
│   │   ├── lifecycle.py                 # unchanged
│   │   └── agent_iteration.py           # Task 7 (NEW)
│   ├── agents/
│   │   └── pr_fixer.py                  # Task 6 (rewrite: options builder)
│   ├── workflows/
│   │   └── pr_autofix.py                # Task 8 (drop PydanticAIWorkflow)
│   ├── gateway/
│   │   └── app.py                       # unchanged
│   └── worker.py                        # Task 9 (drop plugin, add activity)
└── tests/
    ├── test_models.py                   # unchanged
    ├── test_local_repo_impl.py          # unchanged
    ├── test_lifecycle.py                # unchanged
    ├── test_gateway.py                  # unchanged
    ├── test_local_repo_mcp.py           # Task 5 (NEW — SDK MCP wrapper)
    ├── test_agent_iteration.py          # Task 7 (NEW — replaces test_pr_fixer_agent)
    └── test_workflow.py                 # Task 8 (rewrite stubs)
```

`test_pr_fixer_agent.py` is replaced by `test_agent_iteration.py` (the activity is the meaningful test surface now, not the bare agent).

---

## Task 1: Branch, swap dependencies

**Goal:** New branch off `impl/pr-autofix-poc`. `pyproject.toml` swaps `pydantic-ai[mcp,anthropic]` for `claude-agent-sdk`. `uv sync` succeeds. Existing tests that don't reference Pydantic AI keep passing; tests that DO reference Pydantic AI are temporarily expected to fail (we'll fix them in later tasks).

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Create branch**

```bash
git checkout -b impl/pr-autofix-claude-sdk
git status   # should report clean on the new branch
```

- [ ] **Step 2: Swap the dependency in `pyproject.toml`**

Edit the `dependencies` list:

```toml
dependencies = [
    "temporalio>=1.7.0",
    "claude-agent-sdk>=0.1.0",
    "pydantic>=2.7",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "pytest>=8.0",
    "ruff>=0.5",
]
```

(Drop `"pydantic-ai[mcp,anthropic]>=1.0.0"`. Everything else stays.)

- [ ] **Step 3: Regenerate the lockfile and install**

Run: `uv lock && uv sync --extra dev`
Expected: no errors. `claude-agent-sdk` appears in `uv.lock`.

- [ ] **Step 4: Run the test suite — expect partial failures**

Run: `uv run pytest -q --no-header 2>&1 | tail -10`
Expected: imports from `pydantic_ai` in `src/agents/pr_fixer.py`, `src/workflows/pr_autofix.py`, `src/tools/local_repo.py`, `src/tools/github_mcp.py`, `tests/test_pr_fixer_agent.py`, `tests/test_workflow.py` will fail with `ModuleNotFoundError: No module named 'pydantic_ai'`. The pure tests (`test_models.py`, `test_local_repo_impl.py`, `test_lifecycle.py`, `test_gateway.py`) still pass.

Record the baseline: `26 tests should still pass`. (5 models + 14 local_repo_impl + 3 lifecycle + 4 gateway = 26.)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): swap pydantic-ai for claude-agent-sdk"
```

---

## Task 2: Verify SDK importability + sketch the API we'll use

**Goal:** Confirm `claude_agent_sdk` imports cleanly and the symbols we plan to use (`query`, `ClaudeAgentOptions`, `tool`, `create_sdk_mcp_server`, `AssistantMessage`, `ResultMessage`) exist. This is a probe, not a real feature — but committing a `tests/test_sdk_probe.py` documents the API surface we depend on.

**Files:**
- Create: `tests/test_sdk_probe.py`

- [ ] **Step 1: Write the probe test**

```python
"""Probe test: documents the claude_agent_sdk symbols we depend on.
If the SDK renames or removes any of these, this test fails loudly so we
know to update our wrappers."""

def test_sdk_exposes_expected_symbols():
    import claude_agent_sdk

    # Core surfaces
    assert hasattr(claude_agent_sdk, "query"), "query() is the one-shot entry point we use"
    assert hasattr(claude_agent_sdk, "ClaudeAgentOptions"), "options builder"
    assert hasattr(claude_agent_sdk, "tool"), "tool decorator for custom tools"
    assert hasattr(claude_agent_sdk, "create_sdk_mcp_server"), "SDK-MCP factory"

    # Message types we destructure on
    assert hasattr(claude_agent_sdk, "AssistantMessage"), "to log tool calls"
    assert hasattr(claude_agent_sdk, "ResultMessage"), "final result extraction"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_sdk_probe.py -v`
Expected: 1 passed.

If anything fails, **STOP and report**. We need accurate symbol names before writing the wrappers. Likely fixes: the symbol may have moved (e.g., `claude_agent_sdk.types.ResultMessage`) — discover via `uv run python -c "import claude_agent_sdk; print([x for x in dir(claude_agent_sdk) if not x.startswith('_')])"` and adjust this test to point at the canonical location.

- [ ] **Step 3: Commit**

```bash
git add tests/test_sdk_probe.py
git commit -m "test: probe documenting claude_agent_sdk symbols we depend on"
```

---

## Task 3: GitHub MCP config — collapse to a dict

**Goal:** Drop the Pydantic AI `MCPServerStdio` object. The new shape is a plain config dict that the activity inlines into `ClaudeAgentOptions.mcp_servers["github"]`.

**Files:**
- Rewrite: `src/tools/github_mcp.py`
- Modify: no test changes needed (`tests/test_lifecycle.py` only uses `_prepare_workdir_at`, not the MCP module)

- [ ] **Step 1: Replace `src/tools/github_mcp.py` entirely**

```python
import os
from typing import Any


def build_github_mcp_config() -> dict[str, Any]:
    """Build the mcp_servers["github"] config for ClaudeAgentOptions.

    Wraps the official github/github-mcp-server Go binary (installed in
    the worker image, see Dockerfile). Reads GITHUB_TOKEN at call time so
    importing this module without a token does not crash unit tests of
    other modules.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return {
        "command": "github-mcp-server",
        "args": ["stdio"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
```

- [ ] **Step 2: Sanity-check import**

Run: `uv run python -c "from src.tools.github_mcp import build_github_mcp_config; print(build_github_mcp_config.__doc__[:60])"`
Expected: prints the first 60 chars of the docstring; no `ModuleNotFoundError`.

(You won't be able to actually CALL `build_github_mcp_config()` without `GITHUB_TOKEN` in env — that's intentional. Tests should mock it.)

- [ ] **Step 3: Commit**

```bash
git add src/tools/github_mcp.py
git commit -m "refactor(github_mcp): collapse to config-dict builder (no MCPServerStdio)"
```

---

## Task 4: Workdir resolution via env var

**Goal:** Add a helper `workdir_root_from_env()` in `_workdir.py` so SDK MCP tools (which don't receive a `RunContext`) can still resolve the per-workflow workdir. The activity will set `AUTOFIX_WORKDIR_ID` env var before invoking `query()`.

**Files:**
- Modify: `src/tools/_workdir.py`
- Modify: `tests/test_local_repo_impl.py` (append two tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_local_repo_impl.py`:

```python
import os
import pytest as _pytest_for_workdir_env  # local alias to avoid collisions
from src.tools._workdir import workdir_root_from_env


def test_workdir_root_from_env_resolves(monkeypatch):
    monkeypatch.setenv("AUTOFIX_WORKDIR_ID", "abc123")
    p = workdir_root_from_env()
    assert str(p) == "/tmp/autofix-abc123/repo"


def test_workdir_root_from_env_raises_when_unset(monkeypatch):
    monkeypatch.delenv("AUTOFIX_WORKDIR_ID", raising=False)
    with _pytest_for_workdir_env.raises(RuntimeError, match="AUTOFIX_WORKDIR_ID"):
        workdir_root_from_env()
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_local_repo_impl.py::test_workdir_root_from_env_resolves tests/test_local_repo_impl.py::test_workdir_root_from_env_raises_when_unset -v`
Expected: ImportError on `workdir_root_from_env`.

- [ ] **Step 3: Implement the helper**

Append to `src/tools/_workdir.py`:

```python
import os


def workdir_root_from_env() -> Path:
    """Resolve workdir using the AUTOFIX_WORKDIR_ID env var.

    Set by the Temporal activity (run_agent_iteration) so SDK MCP tools,
    which receive a plain dict of args and have no RunContext equivalent,
    can still locate the per-workflow workdir.
    """
    wid = os.environ.get("AUTOFIX_WORKDIR_ID")
    if not wid:
        raise RuntimeError("AUTOFIX_WORKDIR_ID env var is not set")
    return workdir_root(wid)
```

- [ ] **Step 4: Confirm tests pass**

Run: `uv run pytest tests/test_local_repo_impl.py -v`
Expected: all `test_local_repo_impl` tests still pass, plus the 2 new ones (16 total).

- [ ] **Step 5: Commit**

```bash
git add src/tools/_workdir.py tests/test_local_repo_impl.py
git commit -m "feat(workdir): add env-var-driven helper for SDK MCP tools"
```

---

## Task 5: Rewrite `local_repo.py` as an SDK MCP server

**Goal:** Replace `FunctionToolset` (Pydantic AI) with `create_sdk_mcp_server` (Claude Agent SDK). Each tool is async, accepts a dict of args, returns `{"content": [{"type": "text", "text": "..."}]}`. The pure functions in `_local_repo_impl.py` stay identical and are wrapped.

**Files:**
- Rewrite: `src/tools/local_repo.py`
- Create: `tests/test_local_repo_mcp.py`

- [ ] **Step 1: Write failing tests for the MCP wrapper**

Create `tests/test_local_repo_mcp.py`:

```python
"""Smoke tests for the SDK-MCP wrapper around _local_repo_impl.

We don't test the SDK plumbing end-to-end — that's covered by the agent
activity tests. Here we just verify each tool can be called as a plain
async function with the args-dict shape the SDK uses, that it returns
the documented content shape, and that AUTOFIX_WORKDIR_ID is honored.
"""
from pathlib import Path

import pytest

from src.tools.local_repo import (
    read_file_tool,
    list_files_tool,
    apply_edit_tool,
    run_ruff_tool,
    run_pytest_tool,
    git_status_tool,
    git_commit_and_push_tool,
    local_repo_mcp_server,
)


async def test_read_file_tool_returns_sdk_content_shape(tmp_repo: Path, monkeypatch):
    monkeypatch.setenv("AUTOFIX_WORKDIR_ID", "irrelevant")
    monkeypatch.setattr("src.tools.local_repo.workdir_root_from_env", lambda: tmp_repo)
    out = await read_file_tool({"path": "hello.py"})
    assert "content" in out
    assert out["content"][0]["type"] == "text"
    assert out["content"][0]["text"].startswith("def hello()")


async def test_apply_edit_tool_writes_file(tmp_repo: Path, monkeypatch):
    monkeypatch.setenv("AUTOFIX_WORKDIR_ID", "irrelevant")
    monkeypatch.setattr("src.tools.local_repo.workdir_root_from_env", lambda: tmp_repo)
    out = await apply_edit_tool({"path": "hello.py", "new_content": "x = 1\n"})
    assert (tmp_repo / "hello.py").read_text() == "x = 1\n"
    # Returned text contains the sha-1
    assert len(out["content"][0]["text"]) >= 40


async def test_run_ruff_tool_returns_json_text(tmp_repo: Path, monkeypatch):
    monkeypatch.setenv("AUTOFIX_WORKDIR_ID", "irrelevant")
    monkeypatch.setattr("src.tools.local_repo.workdir_root_from_env", lambda: tmp_repo)
    out = await run_ruff_tool({})
    # The returned text is a JSON-serialized RuffResult
    import json
    parsed = json.loads(out["content"][0]["text"])
    assert "exit_code" in parsed and "violations" in parsed


def test_local_repo_mcp_server_is_an_mcp_server():
    # Smoke: the server object is constructed and exposes some 'name' or similar
    # attribute — exact attribute is documented by claude_agent_sdk; we just
    # confirm we got *something* back.
    assert local_repo_mcp_server is not None
```

- [ ] **Step 2: Run them — expect ImportError**

Run: `uv run pytest tests/test_local_repo_mcp.py -v`
Expected: ImportError on `read_file_tool`, etc.

- [ ] **Step 3: Implement `src/tools/local_repo.py`**

Replace the file entirely with:

```python
"""SDK-MCP wrapper around _local_repo_impl pure functions.

Each tool is decorated with claude_agent_sdk.tool(). Tools resolve the
workdir from AUTOFIX_WORKDIR_ID env var (set by run_agent_iteration
before calling query()) — there is no RunContext equivalent in SDK MCP.
"""
import json

from claude_agent_sdk import tool, create_sdk_mcp_server

from src.tools import _local_repo_impl as impl
from src.tools._workdir import workdir_root_from_env


def _text(payload: str | dict) -> dict:
    """Return the SDK content shape from a string or JSON-able dict."""
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload)
    return {"content": [{"type": "text", "text": body}]}


@tool("read_file", "Read a file inside the PR working copy.", {"path": str})
async def read_file_tool(args: dict) -> dict:
    return _text(impl.read_file(workdir_root_from_env(), args["path"]))


@tool(
    "list_files",
    "List files in the working copy matching a glob (default '**/*.py').",
    {"glob": str},
)
async def list_files_tool(args: dict) -> dict:
    glob = args.get("glob", "**/*.py")
    return _text(json.dumps(impl.list_files(workdir_root_from_env(), glob)))


@tool(
    "apply_edit",
    "Overwrite a file with new content. Returns the SHA-1 of the new content.",
    {"path": str, "new_content": str},
)
async def apply_edit_tool(args: dict) -> dict:
    sha = impl.apply_edit(
        workdir_root_from_env(), args["path"], args["new_content"]
    )
    return _text(sha)


@tool(
    "run_ruff",
    "Run ruff check on the working copy. Returns a JSON RuffResult.",
    {},
)
async def run_ruff_tool(args: dict) -> dict:
    result = impl.run_ruff(workdir_root_from_env())
    return _text(result.model_dump())


@tool(
    "run_pytest",
    "Run pytest in the working copy. Optional target (file::test). Returns a JSON PytestResult.",
    {"target": str},
)
async def run_pytest_tool(args: dict) -> dict:
    target = args.get("target") or None
    result = impl.run_pytest(workdir_root_from_env(), target)
    return _text(result.model_dump())


@tool(
    "git_status",
    "Return the git status of the working copy as a JSON GitStatus.",
    {},
)
async def git_status_tool(args: dict) -> dict:
    return _text(impl.git_status(workdir_root_from_env()).model_dump())


@tool(
    "git_commit_and_push",
    "Stage all changes, commit with the given message, fetch, refuse if remote advanced, push. Returns a JSON CommitResult. The commit message is automatically tagged with the [autofix-bot] trailer.",
    {"message": str},
)
async def git_commit_and_push_tool(args: dict) -> dict:
    result = impl.git_commit_and_push(workdir_root_from_env(), args["message"])
    return _text(result.model_dump())


local_repo_mcp_server = create_sdk_mcp_server(
    name="repo",
    version="1.0.0",
    tools=[
        read_file_tool,
        list_files_tool,
        apply_edit_tool,
        run_ruff_tool,
        run_pytest_tool,
        git_status_tool,
        git_commit_and_push_tool,
    ],
)
```

Note: the `tool()` decorator's exact return shape varies slightly by SDK version. The functions above are async and accept a single dict arg — matches the SDK's documented contract. If the decorator wraps the function in a way that breaks calling it directly in tests (e.g., the decorated symbol becomes a ToolDefinition object), the test fixture-call pattern in Step 1 above will fail. In that case, the workaround is: rename the bare async functions to `_read_file_impl_async`, etc., and have the decorator wrap them — the tests call the bare versions, the SDK uses the decorated versions.

- [ ] **Step 4: Run tests — expect them to pass**

Run: `uv run pytest tests/test_local_repo_mcp.py -v`
Expected: 4 passed.

If they fail because the decorator hides the bare async function, apply the workaround described in the note above.

- [ ] **Step 5: Sanity-check full local-repo test suite**

Run: `uv run pytest tests/test_local_repo_impl.py tests/test_local_repo_mcp.py -v`
Expected: 16 + 4 = 20 passed.

- [ ] **Step 6: Commit**

```bash
git add src/tools/local_repo.py tests/test_local_repo_mcp.py
git commit -m "feat(tools): rewrite local_repo as claude-agent-sdk MCP server"
```

---

## Task 6: Rewrite `src/agents/pr_fixer.py` — options builder

**Goal:** The module now just exports `INSTRUCTIONS` (string) and `build_options(workdir_id: str) -> ClaudeAgentOptions`. No more `Agent`, no more `TemporalAgent`.

**Files:**
- Rewrite: `src/agents/pr_fixer.py`
- Delete: `tests/test_pr_fixer_agent.py` (replaced in Task 7)

- [ ] **Step 1: Delete the old test**

```bash
git rm tests/test_pr_fixer_agent.py
```

(We'll add `tests/test_agent_iteration.py` in Task 7.)

- [ ] **Step 2: Rewrite `src/agents/pr_fixer.py`**

```python
"""Claude Agent SDK options builder for the PR autofix agent.

This module exports the system prompt (INSTRUCTIONS) and a factory
build_options() that produces ClaudeAgentOptions ready to pass to
claude_agent_sdk.query(). The actual loop is in
src/activities/agent_iteration.py.
"""
from claude_agent_sdk import ClaudeAgentOptions

from src.tools.github_mcp import build_github_mcp_config
from src.tools.local_repo import local_repo_mcp_server


INSTRUCTIONS = """\
You are an autonomous code-review assistant working on one GitHub Pull Request.

You receive: a short brief listing pending events (new review comments, CI \
results) and the PR identifier. For each event:

1. Use the `github` MCP toolset to fetch full context (PR diff, comment \
   bodies, check run details).
2. Decide whether the event is a valid, actionable engineering request.
3. If yes, use the `repo` MCP toolset OR the builtin Read/Edit/Grep/Glob \
   tools to inspect the code, apply the smallest possible edit, and \
   verify locally with `mcp__repo__run_ruff` and `mcp__repo__run_pytest`.
4. Only call `mcp__repo__git_commit_and_push` if local verification \
   passes. If the push is refused (`remote_advanced` etc.), do NOT retry \
   blindly; report it as `blocking_reason`.
5. If a comment is opinion-only, unclear, or out of scope, do not apply \
   it. Explain in `summary` why you skipped it.

At the very end of your final message, you MUST emit a JSON object on its \
own line (the last non-empty line) with exactly these keys:

```
{"action": "applied_fix" | "no_action_needed" | "blocked",
 "summary": "1-3 sentences",
 "addressed_comment_ids": [<int>, ...],
 "addressed_failures": ["ruff", "pytest::test_x", ...],
 "commit_sha": "<sha or null>",
 "blocking_reason": "<text or null>"}
```

Do NOT wrap that JSON in fenced code blocks. The orchestrator parses the \
last JSON-shaped line of your output.
"""


def build_options() -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions for one agent iteration.

    Reads GITHUB_TOKEN and AUTOFIX_WORKDIR_ID from the process env. Both
    must be set by the activity before calling this.
    """
    return ClaudeAgentOptions(
        system_prompt=INSTRUCTIONS,
        mcp_servers={
            "github": build_github_mcp_config(),
            "repo": local_repo_mcp_server,
        },
        allowed_tools=[
            "Read",
            "Edit",
            "Grep",
            "Glob",
            "mcp__github__*",
            "mcp__repo__*",
        ],
        permission_mode="bypassPermissions",
        env={"CLAUDE_CODE_MAX_RETRIES": "0"},
    )
```

- [ ] **Step 3: Confirm the module imports cleanly**

Set the dummy envs the tests use and import:

```bash
GITHUB_TOKEN=dummy AUTOFIX_WORKDIR_ID=test uv run python -c "from src.agents.pr_fixer import build_options, INSTRUCTIONS; opt = build_options(); print(type(opt).__name__, len(INSTRUCTIONS))"
```

Expected: `ClaudeAgentOptions <some-len>`. If `ClaudeAgentOptions(env=...)` rejects the kwarg, drop it from the constructor and document that `CLAUDE_CODE_MAX_RETRIES=0` must be set in the worker process env instead (already in `.env`, propagated by docker-compose).

- [ ] **Step 4: Commit**

```bash
git add src/agents/pr_fixer.py tests/test_pr_fixer_agent.py
git commit -m "refactor(agent): replace Agent/TemporalAgent with build_options() factory"
```

(The `git rm` from Step 1 is already staged.)

---

## Task 7: The new activity `run_agent_iteration`

**Goal:** A single Temporal activity that wraps one full `query()` call, heartbeats every 30s in the background, and returns a parsed `FixPlan`.

**Files:**
- Create: `src/activities/agent_iteration.py`
- Create: `tests/test_agent_iteration.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_iteration.py`:

```python
"""Tests for the run_agent_iteration activity.

We mock claude_agent_sdk.query so we don't hit Anthropic. The mock yields
a stream of fake messages culminating in a ResultMessage carrying a JSON
tail that parses into a FixPlan.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.activities.agent_iteration import (
    run_agent_iteration,
    _parse_fix_plan,
)
from src.models import PRRef, GitHubEvent, WorkflowState, FixPlan


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


async def test_run_agent_iteration_invokes_query_and_returns_plan(monkeypatch):
    """End-to-end (but mocked): activity sets env, calls query, parses plan."""

    # Fake message stream
    class _FakeResultMessage:
        subtype = "success"
        result = '{"action":"no_action_needed","summary":"nothing","addressed_comment_ids":[],"addressed_failures":[],"commit_sha":null,"blocking_reason":null}'

    async def fake_query(prompt, options):
        yield _FakeResultMessage()

    # Patch the activity's own reference to query so monkeypatching works
    monkeypatch.setattr(
        "src.activities.agent_iteration.query", fake_query
    )
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

    plan = await run_agent_iteration.__wrapped__(state, [event])
    assert isinstance(plan, FixPlan)
    assert plan.action == "no_action_needed"
```

Notes:
- `run_agent_iteration.__wrapped__` is the underlying coroutine (Temporal's `@activity.defn` wraps the original; the `.__wrapped__` attribute exposes it for direct unit testing). If the SDK version doesn't expose `__wrapped__`, an alternative is to expose the bare async function under a different name and have `@activity.defn` decorate a thin wrapper. The plan below uses `__wrapped__`; adjust if needed at impl time.
- We monkeypatch `activity.info` to avoid needing a Temporal context.

- [ ] **Step 2: Run them — expect ImportError**

Run: `uv run pytest tests/test_agent_iteration.py -v`
Expected: ImportError on `src.activities.agent_iteration`.

- [ ] **Step 3: Implement `src/activities/agent_iteration.py`**

```python
"""Temporal activity wrapping one full Claude Agent SDK iteration.

The activity:
  1. Sets AUTOFIX_WORKDIR_ID env var so SDK MCP tools can resolve workdir
  2. Builds ClaudeAgentOptions
  3. Starts a background heartbeat task (every 30s)
  4. Iterates `async for msg in query(prompt, options)`:
       - logs tool calls (best-effort, via AssistantMessage block inspect)
       - captures the final ResultMessage.result text
  5. Parses the trailing JSON line into a FixPlan
  6. Returns the FixPlan
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re

from claude_agent_sdk import (
    query,
    AssistantMessage,
    ResultMessage,
)
from temporalio import activity

from src.agents.pr_fixer import build_options
from src.models import FixPlan, GitHubEvent, WorkflowState


logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL_S = 30


def _build_prompt(state: WorkflowState, events: list[GitHubEvent]) -> str:
    pr = state.pr
    lines = [
        f"PR: {pr.owner}/{pr.repo}#{pr.number} (head {pr.head_sha[:7]} on {pr.head_ref})",
        f"Iteration: {state.iterations}",
        "",
        "Pending events:",
    ]
    for e in events:
        lines.append(
            f"- [{e.kind}] delivery={e.delivery_id} payload_keys={sorted(e.payload.keys())}"
        )
    return "\n".join(lines)


_JSON_TAIL_RE = re.compile(r"\{[^{}]*\"action\"[^{}]*\}", re.DOTALL)


def _parse_fix_plan(text: str) -> FixPlan:
    """Find the trailing JSON object in the agent's final text and parse
    it as FixPlan. Fall back to a blocked FixPlan if no JSON tail is
    findable or it doesn't validate."""
    if not text:
        return FixPlan(
            action="blocked",
            summary="Agent produced no final output.",
            blocking_reason="agent output not parseable: empty",
        )
    matches = list(_JSON_TAIL_RE.finditer(text))
    if not matches:
        return FixPlan(
            action="blocked",
            summary="Agent did not emit a FixPlan JSON tail.",
            blocking_reason="agent output not parseable: no JSON object containing 'action'",
        )
    candidate = matches[-1].group(0)
    try:
        return FixPlan.model_validate_json(candidate)
    except Exception as e:
        return FixPlan(
            action="blocked",
            summary="Agent FixPlan JSON did not validate.",
            blocking_reason=f"agent output not parseable: {type(e).__name__}",
        )


async def _heartbeat_loop(stop: asyncio.Event, counter: dict) -> None:
    """Background task: heartbeat every HEARTBEAT_INTERVAL_S until stopped."""
    while not stop.is_set():
        try:
            activity.heartbeat(counter)
        except Exception:
            # Outside an activity (e.g., unit test) — nothing to heartbeat to.
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


@activity.defn
async def run_agent_iteration(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    """One full agent iteration. Black-box from Temporal's perspective."""
    # Activity context (skipped in unit tests via monkeypatch)
    try:
        workflow_id = activity.info().workflow_id
    except Exception:
        workflow_id = "unit-test"
    os.environ["AUTOFIX_WORKDIR_ID"] = workflow_id

    prompt = _build_prompt(state, events)
    options = build_options()

    counter = {"assistant_messages": 0, "tool_calls": 0}
    stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(stop, counter))

    final_text: str = ""
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                counter["assistant_messages"] += 1
                # Best-effort tool-call counter from the message's blocks
                blocks = getattr(msg, "content", None) or []
                for blk in blocks:
                    if getattr(blk, "type", None) == "tool_use":
                        counter["tool_calls"] += 1
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "subtype", None) == "success":
                    final_text = getattr(msg, "result", "") or ""
                else:
                    return FixPlan(
                        action="blocked",
                        summary="Agent terminated abnormally.",
                        blocking_reason=f"ResultMessage.subtype={msg.subtype}",
                    )
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await hb_task

    return _parse_fix_plan(final_text)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_agent_iteration.py -v`
Expected: 4 passed.

If `__wrapped__` access doesn't work for the activity-decorated function, refactor: expose `async def _run_agent_iteration_impl(state, events) -> FixPlan` as the actual body, and have `@activity.defn run_agent_iteration` be a thin pass-through. The test then calls `_run_agent_iteration_impl` directly.

- [ ] **Step 5: Commit**

```bash
git add src/activities/agent_iteration.py tests/test_agent_iteration.py
git commit -m "feat(activity): run_agent_iteration wraps claude_agent_sdk.query"
```

---

## Task 8: Workflow — drop Pydantic AI, call the new activity

**Goal:** `PRAutofixWorkflow` no longer inherits from `PydanticAIWorkflow` and no longer references `temporal_agent`. The agent invocation becomes a normal `workflow.execute_activity(run_agent_iteration, ...)` call.

**Files:**
- Rewrite: `src/workflows/pr_autofix.py`
- Rewrite: `tests/test_workflow.py`

- [ ] **Step 1: Rewrite the workflow**

Edit `src/workflows/pr_autofix.py`. The diff from the current version:

```python
# REMOVED: from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow
# REMOVED: from src.agents.pr_fixer import temporal_agent (now build_options, not imported here)
# REMOVED: __pydantic_ai_agents__ class attribute
# REMOVED: class inherits from PydanticAIWorkflow
# REMOVED: temporal_agent.run(prompt, deps=deps) — replaced with execute_activity
# REMOVED: AgentDeps construction inside the workflow (no longer needed)
# ADDED:   import of run_agent_iteration
# ADDED:   import of RetryPolicy
```

The complete new file:

```python
import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.models import (
        PRRef,
        GitHubEvent,
        WorkflowState,
        FixPlan,
    )
    from src.activities.lifecycle import (
        prepare_workdir,
        cleanup_workdir,
        post_status,
    )
    from src.activities.agent_iteration import run_agent_iteration


MAX_ITERATIONS = 5
IDLE_TIMEOUT = timedelta(minutes=30)


@workflow.defn(name="PRAutofixWorkflow")
class PRAutofixWorkflow:
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
        await workflow.execute_activity(
            prepare_workdir,
            self._state.pr,
            start_to_close_timeout=timedelta(minutes=5),
        )
        do_cleanup = True
        try:
            while self._state.iterations < MAX_ITERATIONS:
                try:
                    await workflow.wait_condition(
                        lambda: bool(self._state.pending_events) or self._state.closed,
                        timeout=IDLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    self._state.closed = True
                    break
                if self._state.closed and not self._state.pending_events:
                    break

                self._state.iterations += 1
                events_snapshot = list(self._state.pending_events)
                self._state.pending_events.clear()

                try:
                    plan: FixPlan = await workflow.execute_activity(
                        run_agent_iteration,
                        args=[self._state, events_snapshot],
                        start_to_close_timeout=timedelta(minutes=10),
                        heartbeat_timeout=timedelta(seconds=90),
                        retry_policy=RetryPolicy(
                            maximum_attempts=2,
                            initial_interval=timedelta(seconds=30),
                            backoff_coefficient=2.0,
                        ),
                    )
                except Exception as exc:
                    plan = FixPlan(
                        action="blocked",
                        summary="Agent iteration failed.",
                        blocking_reason=f"{type(exc).__name__}: {exc}",
                    )
                    self._state.closed = True

                self._apply_plan(plan)

                self._state = await workflow.execute_activity(
                    post_status,
                    args=[self._state, plan],
                    start_to_close_timeout=timedelta(seconds=60),
                )

                if workflow.info().is_continue_as_new_suggested():
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

    def _apply_plan(self, plan: FixPlan) -> None:
        self._state.processed_comment_ids |= set(plan.addressed_comment_ids)
```

- [ ] **Step 2: Rewrite the workflow test**

Replace `tests/test_workflow.py` with:

```python
import uuid

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import PRRef, GitHubEvent, WorkflowState, FixPlan
from src.workflows.pr_autofix import PRAutofixWorkflow


# Stub activities by name so the workflow picks these up instead of the real ones.
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
```

The big simplification: no more `PydanticAIPlugin` on Worker, no more `agent.override`, no more poking `_temporal_model` internals.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_workflow.py -v`
Expected: 1 passed.

- [ ] **Step 4: Run full suite — should be green from end-to-end**

Run: `uv run pytest -v`
Expected: all pass. Approximate count: parent branch had 32 tests; this plan deletes 1 (`test_pr_fixer_agent.py`) and adds about 11 (1 sdk probe + 2 workdir env + 4 local_repo_mcp + 4 agent_iteration), so expect roughly **42 tests** green at the end. Don't fail the task on the exact number — just confirm zero failures.

If the workflow test fails because the workflow can't import `claude_agent_sdk` symbols inside the sandbox (the SDK has C extensions or non-sandboxable imports), wrap the import in `with workflow.unsafe.imports_passed_through():` — which we already do. If the activity import itself triggers MCP server boot, add a deferred-import pattern in `agent_iteration.py` (`import claude_agent_sdk` inside the activity body rather than at module top). Note any deviation.

- [ ] **Step 5: Commit**

```bash
git add src/workflows/pr_autofix.py tests/test_workflow.py
git commit -m "refactor(workflow): drop PydanticAIWorkflow, call run_agent_iteration activity"
```

---

## Task 9: Worker — drop plugin, register new activity

**Goal:** `src/worker.py` no longer references `PydanticAIPlugin`. The new activity `run_agent_iteration` is in the activities list.

**Files:**
- Modify: `src/worker.py`

- [ ] **Step 1: Edit `src/worker.py`**

Replace the file:

```python
import asyncio
import concurrent.futures
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from src.workflows.pr_autofix import PRAutofixWorkflow
from src.activities.lifecycle import (
    prepare_workdir,
    cleanup_workdir,
    post_status,
)
from src.activities.agent_iteration import run_agent_iteration


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(
        os.environ.get("TEMPORAL_TARGET", "localhost:7233"),
    )
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "pr-autofix")
    # prepare_workdir and cleanup_workdir are sync activities — Temporal
    # requires an explicit executor for those. post_status and
    # run_agent_iteration are async and run on the event loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as activity_executor:
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[PRAutofixWorkflow],
            activities=[
                prepare_workdir,
                cleanup_workdir,
                post_status,
                run_agent_iteration,
            ],
            activity_executor=activity_executor,
        ):
            logging.info("worker listening on task queue %s", task_queue)
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
GITHUB_TOKEN=dummy ANTHROPIC_API_KEY=dummy AUTOFIX_WORKDIR_ID=test \
  uv run python -c "import src.worker; print(src.worker.main)"
```

Expected: prints `<function main at 0x...>`, no errors.

- [ ] **Step 3: Run the full test suite once more**

Run: `uv run pytest -v`
Expected: 39 passed.

- [ ] **Step 4: Commit**

```bash
git add src/worker.py
git commit -m "refactor(worker): drop PydanticAIPlugin, register run_agent_iteration"
```

---

## Task 10: Rebuild and smoke test

**Goal:** Containers come back up with the SDK and the new agent. Manual smoke check that the gateway and worker boot, then the user drives a real PR test if desired.

**Files:** none — verification only.

- [ ] **Step 1: Rebuild worker and gateway**

Run: `docker compose up -d --build worker gateway`
Expected: both containers come up with no error. `docker compose logs --tail=10 worker` shows `worker listening on task queue pr-autofix`. `docker compose logs --tail=10 gateway` shows `Uvicorn running on http://0.0.0.0:8000`.

- [ ] **Step 2: Verify the GitHub MCP binary is still in the image**

Run: `docker compose exec -T worker which github-mcp-server`
Expected: `/usr/local/bin/github-mcp-server`. (We didn't change the Dockerfile, but worth confirming the rebuild didn't break it.)

- [ ] **Step 3: Verify the SDK is in the venv**

Run: `docker compose exec -T worker sh -c 'ls /app/.venv/lib/python*/site-packages | grep -i claude_agent_sdk'`
Expected: `claude_agent_sdk` directory present.

- [ ] **Step 4: Hand off to manual smoke test**

The PoC's previous manual smoke test (open a PR with a ruff violation; agent fixes and pushes) is the acceptance test. Run it against the same playground repo. The autofix commit should still carry the `[autofix-bot]` trailer (because `_local_repo_impl.git_commit_and_push` is unchanged). The gateway's self-trigger guard should still drop the resulting `pr_synchronize`.

- [ ] **Step 5: Commit any small fixups discovered during smoke (if applicable)**

If nothing needs fixing, no commit. If real bugs surface during smoke, fix them in a follow-up commit on this branch with a message like `fix: <specific issue> uncovered during smoke`.

---

## After all tasks complete

- All unit tests green (~42; pre-conversion was 32, net +10 from this plan)
- `docker compose up --build` brings up the stack cleanly
- Pydantic AI is fully removed from `pyproject.toml`, `uv.lock`, and all source imports (`grep -r pydantic_ai src/` returns nothing)
- The original `impl/pr-autofix-poc` branch is preserved untouched as the Pydantic AI reference implementation
- The new `impl/pr-autofix-claude-sdk` branch carries the Claude Agent SDK version

Follow-up work deliberately deferred (from spec §9):
- Container-per-iteration sandbox for Read/Edit/Grep/Glob path isolation
- Sub-agent spawning (planner / researcher / editor)
- `apply_patch` (unified diff) instead of `apply_edit` (whole file)
- Persistent message history across iterations
- Logfire / structured tracing
- GitHub App auth for working Check Runs
