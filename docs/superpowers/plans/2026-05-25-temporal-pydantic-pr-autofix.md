# PR Autofix PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable PoC where a Temporal workflow drives a Pydantic AI agent that auto-fixes a GitHub Pull Request (review comments + CI failures), posting status back to the PR.

**Architecture:** A FastAPI gateway maps GitHub webhooks to `signal_with_start` on a long-lived `PRAutofixWorkflow` (one per PR). The workflow uses `pydantic_ai.durable_exec.temporal.TemporalAgent`, which auto-offloads model calls, MCP server calls, and toolset calls to Temporal activities. Two toolsets: the official GitHub MCP server (`MCPServerStdio`) and a local `FunctionToolset` that operates on a cloned working copy at `/tmp/autofix-{workflow_id}/repo` (ruff, pytest, git).

**Tech Stack:** Python 3.12, `temporalio`, `pydantic-ai[temporal,mcp,anthropic]`, `fastapi`, `uvicorn`, `httpx`, `ruff`, `pytest`, `pytest-asyncio`. LLM: `anthropic:claude-sonnet-4-6`. Container runtime: docker-compose with `temporalio/auto-setup`. Package manager: `uv`.

**Spec:** `docs/superpowers/specs/2026-05-25-temporal-pydantic-pr-autofix-design.md`

---

## File layout produced by this plan

```
agent-temporal/
├── pyproject.toml                       # Task 1
├── .env.example                         # Task 1
├── docker-compose.yml                   # Task 10
├── README.md                            # Task 10
├── src/
│   ├── __init__.py                      # Task 1
│   ├── models.py                        # Task 2
│   ├── tools/
│   │   ├── __init__.py                  # Task 3
│   │   ├── _workdir.py                  # Task 3 (helper, pure)
│   │   ├── local_repo.py                # Tasks 3, 4, 5 (toolset wrapper)
│   │   ├── _local_repo_impl.py          # Tasks 3, 4, 5 (pure functions)
│   │   └── github_mcp.py                # Task 6
│   ├── activities/
│   │   ├── __init__.py                  # Task 6
│   │   └── lifecycle.py                 # Task 6
│   ├── agents/
│   │   ├── __init__.py                  # Task 7
│   │   └── pr_fixer.py                  # Task 7
│   ├── workflows/
│   │   ├── __init__.py                  # Task 8
│   │   └── pr_autofix.py                # Task 8
│   ├── gateway/
│   │   ├── __init__.py                  # Task 9
│   │   └── app.py                       # Task 9
│   └── worker.py                        # Task 10
└── tests/
    ├── __init__.py                      # Task 1
    ├── conftest.py                      # Task 3 (extended by later tasks)
    ├── test_models.py                   # Task 2
    ├── test_local_repo_impl.py          # Tasks 3, 4, 5
    ├── test_lifecycle.py                # Task 6
    ├── test_pr_fixer_agent.py           # Task 7
    ├── test_workflow.py                 # Task 8
    └── test_gateway.py                  # Task 9
```

The split between `local_repo.py` (FunctionToolset wrapping `RunContext`) and `_local_repo_impl.py` (pure functions taking a `Path`) keeps tests fast and free of agent machinery.

---

## Task 1: Project bootstrap

**Goal:** A working Python package with `uv`, a passing trivial pytest, and a placeholder `.env.example`.

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/test_smoke.py`:

```python
def test_python_works():
    assert 1 + 1 == 2
```

- [ ] **Step 2: Create empty package init files**

Create `src/__init__.py` and `tests/__init__.py` as empty files.

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "agent-temporal"
version = "0.1.0"
description = "PoC: PR autofix agent on Temporal + Pydantic AI"
requires-python = ">=3.12"
dependencies = [
    "temporalio>=1.7.0",
    "pydantic-ai[mcp,anthropic]>=1.0.0",
    "pydantic>=2.7",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["src*"]
```

Note: the spec calls for `pydantic-ai[temporal,mcp,anthropic]`. The `temporal` extra name should be confirmed against the installed pydantic-ai version. If `temporal` is not a valid extra in the installed version, install `temporalio` and `pydantic-ai` separately and `pydantic_ai.durable_exec.temporal` will be importable. The dependency list above intentionally lists `temporalio` separately to be robust to either case.

- [ ] **Step 4: Create `.env.example`**

```
# Anthropic
ANTHROPIC_API_KEY=

# GitHub (fine-grained PAT with PR read/write + checks write)
GITHUB_TOKEN=
GITHUB_WEBHOOK_SECRET=

# Temporal (used by worker + gateway containers)
TEMPORAL_TARGET=temporal:7233
TEMPORAL_TASK_QUEUE=pr-autofix
```

- [ ] **Step 5: Install and run the smoke test**

```bash
uv sync --extra dev
uv run pytest tests/test_smoke.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .env.example src/__init__.py tests/__init__.py tests/test_smoke.py uv.lock
git commit -m "chore: bootstrap project (uv + pytest)"
```

---

## Task 2: Pydantic models

**Goal:** All data structures from spec §6, round-tripping through Pydantic JSON.

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models.py`:

```python
from src.models import (
    PRRef,
    GitHubEvent,
    AgentDeps,
    FixPlan,
    WorkflowState,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: ImportError / ModuleNotFoundError on `src.models`.

- [ ] **Step 3: Implement `src/models.py`**

```python
from typing import Literal

from pydantic import BaseModel, Field


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat(models): pydantic data structures for workflow, agent, events"
```

---

## Task 3: Local repo toolset — file operations

**Goal:** Pure file ops (`read_file`, `list_files`, `apply_edit`) implemented and tested with a tmp git repo, then wrapped as a `FunctionToolset`.

**Files:**
- Create: `src/tools/__init__.py` (empty)
- Create: `src/tools/_workdir.py`
- Create: `src/tools/_local_repo_impl.py`
- Create: `src/tools/local_repo.py`
- Create: `tests/conftest.py`
- Create: `tests/test_local_repo_impl.py`

- [ ] **Step 1: Write a shared fixture for a tmp git repo**

Create `tests/conftest.py`:

```python
import subprocess
from pathlib import Path

import pytest


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Initialize a git repo with one file and one commit. Returns the workdir."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t.test"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    (repo / "hello.py").write_text("def hello():\n    return 'hi'\n")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "init"], repo)
    return repo
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_local_repo_impl.py`:

```python
from pathlib import Path

import pytest

from src.tools._local_repo_impl import (
    read_file,
    list_files,
    apply_edit,
)


def test_read_file_returns_content(tmp_repo: Path):
    assert read_file(tmp_repo, "hello.py").startswith("def hello()")


def test_read_file_rejects_outside_workdir(tmp_repo: Path):
    with pytest.raises(ValueError, match="outside"):
        read_file(tmp_repo, "../etc/passwd")


def test_list_files_globs(tmp_repo: Path):
    (tmp_repo / "extra.py").write_text("x = 1\n")
    assert sorted(list_files(tmp_repo, "*.py")) == ["extra.py", "hello.py"]


def test_apply_edit_writes_full_content(tmp_repo: Path):
    new = "def hello():\n    return 'bye'\n"
    sha = apply_edit(tmp_repo, "hello.py", new)
    assert (tmp_repo / "hello.py").read_text() == new
    assert len(sha) == 40  # sha-1 hex


def test_apply_edit_rejects_outside_workdir(tmp_repo: Path):
    with pytest.raises(ValueError, match="outside"):
        apply_edit(tmp_repo, "../escape.txt", "hi")
```

- [ ] **Step 3: Implement `src/tools/_workdir.py`**

```python
from pathlib import Path


def workdir_root(workdir_id: str) -> Path:
    """Resolve the per-workflow workdir root."""
    return Path("/tmp") / f"autofix-{workdir_id}" / "repo"


def safe_join(workdir: Path, relative: str) -> Path:
    """Join a path inside workdir, rejecting traversal."""
    workdir = workdir.resolve()
    candidate = (workdir / relative).resolve()
    if not str(candidate).startswith(str(workdir) + "/") and candidate != workdir:
        raise ValueError(f"path {relative!r} resolves outside workdir")
    return candidate
```

- [ ] **Step 4: Implement `src/tools/_local_repo_impl.py` (file ops only)**

```python
import hashlib
from pathlib import Path

from src.tools._workdir import safe_join


def read_file(workdir: Path, path: str) -> str:
    return safe_join(workdir, path).read_text()


def list_files(workdir: Path, glob: str = "**/*.py") -> list[str]:
    workdir = workdir.resolve()
    return sorted(
        str(p.relative_to(workdir))
        for p in workdir.glob(glob)
        if p.is_file() and ".git" not in p.parts
    )


def apply_edit(workdir: Path, path: str, new_content: str) -> str:
    target = safe_join(workdir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content)
    return hashlib.sha1(new_content.encode()).hexdigest()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_local_repo_impl.py -v`
Expected: 5 passed.

- [ ] **Step 6: Create the `FunctionToolset` wrapper**

Create `src/tools/local_repo.py`:

```python
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from src.models import AgentDeps
from src.tools._workdir import workdir_root
from src.tools import _local_repo_impl as impl


local_repo_toolset = FunctionToolset[AgentDeps](id="repo")


@local_repo_toolset.tool
def read_file(ctx: RunContext[AgentDeps], path: str) -> str:
    """Read a file in the working copy."""
    return impl.read_file(workdir_root(ctx.deps.workdir_id), path)


@local_repo_toolset.tool
def list_files(ctx: RunContext[AgentDeps], glob: str = "**/*.py") -> list[str]:
    """List files in the working copy matching glob."""
    return impl.list_files(workdir_root(ctx.deps.workdir_id), glob)


@local_repo_toolset.tool
def apply_edit(ctx: RunContext[AgentDeps], path: str, new_content: str) -> str:
    """Overwrite a file with new content. Returns SHA-1 of new content."""
    return impl.apply_edit(workdir_root(ctx.deps.workdir_id), path, new_content)
```

Note: the exact `FunctionToolset` import path may be `pydantic_ai.toolsets` or `pydantic_ai.tools` depending on the version. Confirm at implementation time and adjust the import accordingly — the test in this task does NOT exercise this wrapper (it tests `_local_repo_impl` directly), so the wrapper import is verified later in Task 7.

- [ ] **Step 7: Commit**

```bash
git add src/tools/__init__.py src/tools/_workdir.py src/tools/_local_repo_impl.py src/tools/local_repo.py tests/conftest.py tests/test_local_repo_impl.py
git commit -m "feat(tools): local repo file ops (read/list/apply_edit) + toolset wrapper"
```

---

## Task 4: Local repo toolset — lint and test runners

**Goal:** `run_ruff` and `run_pytest` pure functions, returning structured results.

**Files:**
- Modify: `src/tools/_local_repo_impl.py` (append)
- Modify: `src/tools/local_repo.py` (append)
- Modify: `tests/test_local_repo_impl.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_local_repo_impl.py`:

```python
from src.tools._local_repo_impl import run_ruff, run_pytest, RuffResult, PytestResult


def test_run_ruff_clean(tmp_repo: Path):
    res = run_ruff(tmp_repo)
    assert isinstance(res, RuffResult)
    assert res.violations == []
    assert res.exit_code == 0


def test_run_ruff_detects_violation(tmp_repo: Path):
    (tmp_repo / "bad.py").write_text("import os\nimport sys\n")  # unused imports
    res = run_ruff(tmp_repo)
    assert res.exit_code != 0
    assert any("bad.py" in v.filename for v in res.violations)


def test_run_pytest_passes(tmp_repo: Path):
    (tmp_repo / "test_a.py").write_text("def test_x():\n    assert 1 == 1\n")
    res = run_pytest(tmp_repo)
    assert isinstance(res, PytestResult)
    assert res.exit_code == 0
    assert res.passed >= 1
    assert res.failed == 0


def test_run_pytest_fails(tmp_repo: Path):
    (tmp_repo / "test_a.py").write_text("def test_x():\n    assert 1 == 2\n")
    res = run_pytest(tmp_repo)
    assert res.exit_code != 0
    assert res.failed >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_local_repo_impl.py -v`
Expected: ImportError on `run_ruff`/`run_pytest`/`RuffResult`/`PytestResult`.

- [ ] **Step 3: Append models and impl**

Append to `src/tools/_local_repo_impl.py`:

```python
import json
import subprocess
from pathlib import Path

from pydantic import BaseModel


class RuffViolation(BaseModel):
    filename: str
    code: str
    message: str
    line: int


class RuffResult(BaseModel):
    exit_code: int
    violations: list[RuffViolation]
    raw_stderr: str = ""


class PytestResult(BaseModel):
    exit_code: int
    passed: int
    failed: int
    errors: int
    summary: str


def run_ruff(workdir: Path) -> RuffResult:
    proc = subprocess.run(
        ["ruff", "check", ".", "--output-format=json"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if proc.stdout.strip():
        raw = json.loads(proc.stdout)
        violations = [
            RuffViolation(
                filename=item["filename"],
                code=item["code"],
                message=item["message"],
                line=item["location"]["row"],
            )
            for item in raw
        ]
    else:
        violations = []
    return RuffResult(exit_code=proc.returncode, violations=violations, raw_stderr=proc.stderr)


def run_pytest(workdir: Path, target: str | None = None) -> PytestResult:
    cmd = ["pytest", "-q", "--no-header"]
    if target:
        cmd.append(target)
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    out = proc.stdout
    passed = _count_token(out, "passed")
    failed = _count_token(out, "failed")
    errors = _count_token(out, "error")
    summary = (out.splitlines() or [""])[-1].strip()
    return PytestResult(
        exit_code=proc.returncode,
        passed=passed,
        failed=failed,
        errors=errors,
        summary=summary,
    )


def _count_token(out: str, token: str) -> int:
    """Parse pytest's terminal summary tokens like '3 passed', '1 failed'."""
    import re
    m = re.search(rf"(\d+)\s+{token}", out)
    return int(m.group(1)) if m else 0
```

- [ ] **Step 4: Append wrappers**

Append to `src/tools/local_repo.py`:

```python
from src.tools._local_repo_impl import RuffResult, PytestResult


@local_repo_toolset.tool
def run_ruff(ctx: RunContext[AgentDeps]) -> RuffResult:
    """Run ruff check on the working copy. Returns violations as structured data."""
    return impl.run_ruff(workdir_root(ctx.deps.workdir_id))


@local_repo_toolset.tool
def run_pytest(ctx: RunContext[AgentDeps], target: str | None = None) -> PytestResult:
    """Run pytest. Optionally limit to a target (file::test)."""
    return impl.run_pytest(workdir_root(ctx.deps.workdir_id), target)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_local_repo_impl.py -v`
Expected: 9 passed total (5 from Task 3 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add src/tools/_local_repo_impl.py src/tools/local_repo.py tests/test_local_repo_impl.py
git commit -m "feat(tools): ruff + pytest runners"
```

---

## Task 5: Local repo toolset — git operations

**Goal:** `git_status` and `git_commit_and_push`, including the safety check against an advanced remote.

**Files:**
- Modify: `src/tools/_local_repo_impl.py` (append)
- Modify: `src/tools/local_repo.py` (append)
- Modify: `tests/test_local_repo_impl.py` (append)
- Modify: `tests/conftest.py` (append bare-remote fixture)

- [ ] **Step 1: Add a bare-remote fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def tmp_repo_with_remote(tmp_path: Path) -> Path:
    """Initialize a working repo with a sibling bare remote configured as 'origin'."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _run(["git", "init", "--bare", "-b", "main"], remote)

    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], repo)
    _run(["git", "config", "user.email", "t@t.test"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    (repo / "hello.py").write_text("def hello():\n    return 'hi'\n")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "init"], repo)
    _run(["git", "remote", "add", "origin", str(remote)], repo)
    _run(["git", "push", "-u", "origin", "main"], repo)
    return repo
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_local_repo_impl.py`:

```python
from src.tools._local_repo_impl import (
    git_status,
    git_commit_and_push,
    GitStatus,
    CommitResult,
)


def test_git_status_clean(tmp_repo: Path):
    s = git_status(tmp_repo)
    assert isinstance(s, GitStatus)
    assert s.branch == "main"
    assert s.dirty is False


def test_git_status_dirty_after_edit(tmp_repo: Path):
    (tmp_repo / "hello.py").write_text("x = 1\n")
    assert git_status(tmp_repo).dirty is True


def test_git_commit_and_push_succeeds(tmp_repo_with_remote: Path):
    (tmp_repo_with_remote / "hello.py").write_text("x = 1\n")
    res = git_commit_and_push(tmp_repo_with_remote, "autofix: x=1")
    assert isinstance(res, CommitResult)
    assert res.pushed is True
    assert res.commit_sha and len(res.commit_sha) == 40
    assert res.reason is None


def test_git_commit_and_push_refuses_when_remote_advanced(tmp_repo_with_remote: Path):
    # Make remote advance independently
    other = tmp_repo_with_remote.parent / "other"
    other.mkdir()
    _run(["git", "clone", str(tmp_repo_with_remote.parent / "remote.git"), "."], other)
    _run(["git", "config", "user.email", "t@t.test"], other)
    _run(["git", "config", "user.name", "Test"], other)
    (other / "from_other.py").write_text("y = 2\n")
    _run(["git", "add", "."], other)
    _run(["git", "commit", "-m", "from other"], other)
    _run(["git", "push"], other)

    # Now our workdir tries to push without fetching
    (tmp_repo_with_remote / "hello.py").write_text("x = 1\n")
    res = git_commit_and_push(tmp_repo_with_remote, "autofix")
    assert res.pushed is False
    assert res.reason == "remote_advanced"


def test_git_commit_and_push_nothing_to_commit(tmp_repo_with_remote: Path):
    res = git_commit_and_push(tmp_repo_with_remote, "autofix")
    assert res.pushed is False
    assert res.reason == "no_changes"
```

The fixture file already imports `_run`; add it to the test file if you want a fresh import (the test calls `_run` directly):

```python
from tests.conftest import _run  # type: ignore[attr-defined]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_local_repo_impl.py -v`
Expected: ImportError on `git_status`/`git_commit_and_push`/`GitStatus`/`CommitResult`.

- [ ] **Step 4: Append impl**

Append to `src/tools/_local_repo_impl.py`:

```python
class GitStatus(BaseModel):
    branch: str
    dirty: bool
    ahead: int = 0
    behind: int = 0


class CommitResult(BaseModel):
    pushed: bool
    commit_sha: str | None = None
    reason: str | None = None  # "no_changes" | "remote_advanced" | other


def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=workdir, capture_output=True, text=True, check=False
    )


def git_status(workdir: Path) -> GitStatus:
    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    porcelain = _git(workdir, "status", "--porcelain").stdout
    dirty = bool(porcelain.strip())
    return GitStatus(branch=branch, dirty=dirty)


def git_commit_and_push(workdir: Path, message: str) -> CommitResult:
    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    add = _git(workdir, "add", "-A")
    if add.returncode != 0:
        return CommitResult(pushed=False, reason=add.stderr.strip())

    diff_cached = _git(workdir, "diff", "--cached", "--quiet")
    if diff_cached.returncode == 0:
        return CommitResult(pushed=False, reason="no_changes")

    commit = _git(workdir, "commit", "-m", message)
    if commit.returncode != 0:
        return CommitResult(pushed=False, reason=commit.stderr.strip())
    sha = _git(workdir, "rev-parse", "HEAD").stdout.strip()

    fetch = _git(workdir, "fetch", "origin", branch)
    if fetch.returncode != 0:
        return CommitResult(pushed=False, commit_sha=sha, reason=fetch.stderr.strip())

    behind = _git(
        workdir, "rev-list", "--count", f"HEAD..origin/{branch}"
    ).stdout.strip()
    if behind and int(behind) > 0:
        return CommitResult(pushed=False, commit_sha=sha, reason="remote_advanced")

    push = _git(workdir, "push", "origin", branch)
    if push.returncode != 0:
        return CommitResult(pushed=False, commit_sha=sha, reason=push.stderr.strip())

    return CommitResult(pushed=True, commit_sha=sha)
```

- [ ] **Step 5: Append wrappers**

Append to `src/tools/local_repo.py`:

```python
from src.tools._local_repo_impl import GitStatus, CommitResult


@local_repo_toolset.tool
def git_status(ctx: RunContext[AgentDeps]) -> GitStatus:
    """Return the git status of the working copy."""
    return impl.git_status(workdir_root(ctx.deps.workdir_id))


@local_repo_toolset.tool
def git_commit_and_push(ctx: RunContext[AgentDeps], message: str) -> CommitResult:
    """Stage all changes, commit, fetch, refuse if remote advanced, push."""
    return impl.git_commit_and_push(workdir_root(ctx.deps.workdir_id), message)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_local_repo_impl.py -v`
Expected: 14 passed total.

- [ ] **Step 7: Commit**

```bash
git add src/tools/_local_repo_impl.py src/tools/local_repo.py tests/conftest.py tests/test_local_repo_impl.py
git commit -m "feat(tools): git status + commit/push with remote-advanced guard"
```

---

## Task 6: GitHub MCP server + lifecycle activities

**Goal:** A configured `MCPServerStdio` instance for the GitHub MCP server, plus the three lifecycle activities (`prepare_workdir`, `cleanup_workdir`, `post_status`). Lifecycle activities are unit-tested with a local bare remote (no real GitHub).

**Files:**
- Create: `src/tools/github_mcp.py`
- Create: `src/activities/__init__.py` (empty)
- Create: `src/activities/lifecycle.py`
- Create: `tests/test_lifecycle.py`

- [ ] **Step 1: Implement GitHub MCP module**

Create `src/tools/github_mcp.py`:

```python
import os

from pydantic_ai.mcp import MCPServerStdio


def build_github_mcp_server() -> MCPServerStdio:
    """Construct the GitHub MCP server. Reads GITHUB_TOKEN at call time."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return MCPServerStdio(
        "npx",
        args=["-y", "@github/github-mcp-server"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": token},
        id="github",
        timeout=15,
    )
```

We expose a builder rather than a module-level singleton because constructing it at import time crashes anyone importing the module without `GITHUB_TOKEN` set (including unit tests).

- [ ] **Step 2: Write the failing tests for lifecycle**

Create `tests/test_lifecycle.py`:

```python
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.activities.lifecycle import (
    _prepare_workdir_at,
    _cleanup_workdir_at,
)
from src.models import PRRef


def test_prepare_workdir_clones_when_missing(tmp_path: Path, tmp_repo_with_remote: Path):
    target = tmp_path / "autofix-wf1" / "repo"
    remote_url = str(tmp_repo_with_remote.parent / "remote.git")
    _prepare_workdir_at(
        target=target,
        clone_url=remote_url,
        head_ref="main",
        head_sha="HEAD",
    )
    assert (target / ".git").is_dir()
    assert (target / "hello.py").exists()


def test_prepare_workdir_is_idempotent(tmp_path: Path, tmp_repo_with_remote: Path):
    target = tmp_path / "autofix-wf1" / "repo"
    remote_url = str(tmp_repo_with_remote.parent / "remote.git")
    for _ in range(2):
        _prepare_workdir_at(
            target=target,
            clone_url=remote_url,
            head_ref="main",
            head_sha="HEAD",
        )
    assert (target / "hello.py").exists()


def test_cleanup_workdir_removes_tree(tmp_path: Path):
    target = tmp_path / "autofix-wf1"
    (target / "repo").mkdir(parents=True)
    (target / "repo" / "junk.txt").write_text("x")
    _cleanup_workdir_at(target)
    assert not target.exists()
```

Note: we test the underlying `_prepare_workdir_at` / `_cleanup_workdir_at` helpers, not the `@activity.defn`-decorated functions (those require Temporal's `activity.info()` context). The decorated functions are thin shims.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_lifecycle.py -v`
Expected: ImportError on `src.activities.lifecycle`.

- [ ] **Step 4: Implement `src/activities/lifecycle.py`**

```python
import os
import shutil
import subprocess
from pathlib import Path

import httpx
from temporalio import activity

from src.models import FixPlan, PRRef, WorkflowState
from src.tools._workdir import workdir_root


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def _prepare_workdir_at(
    *, target: Path, clone_url: str, head_ref: str, head_sha: str
) -> None:
    """Idempotent clone-or-fetch."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").is_dir():
        target.mkdir(exist_ok=True)
        _run(["git", "clone", "--depth=50", clone_url, "."], target)
    _run(["git", "fetch", "origin", head_ref], target)
    _run(["git", "checkout", "-B", "autofix", "FETCH_HEAD"], target)


def _cleanup_workdir_at(workdir_parent: Path) -> None:
    if workdir_parent.exists():
        shutil.rmtree(workdir_parent)


def _clone_url(pr: PRRef) -> str:
    token = os.environ["GITHUB_TOKEN"]
    return f"https://x-access-token:{token}@github.com/{pr.owner}/{pr.repo}.git"


@activity.defn
def prepare_workdir(pr: PRRef) -> None:
    workflow_id = activity.info().workflow_id
    target = workdir_root(workflow_id)
    _prepare_workdir_at(
        target=target,
        clone_url=_clone_url(pr),
        head_ref=pr.head_ref,
        head_sha=pr.head_sha,
    )


@activity.defn
def cleanup_workdir(pr: PRRef) -> None:
    workflow_id = activity.info().workflow_id
    workdir_parent = workdir_root(workflow_id).parent
    _cleanup_workdir_at(workdir_parent)


@activity.defn
async def post_status(state: WorkflowState, plan: FixPlan) -> WorkflowState:
    """Update (or create) the status comment and Check Run on the PR.

    Returns the updated state with comment/check_run ids filled in.
    """
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    pr = state.pr
    body = _render_status_markdown(state, plan)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Comment: create or update in place
        if state.posted_status_comment_id is None:
            r = await client.post(
                f"https://api.github.com/repos/{pr.owner}/{pr.repo}/issues/{pr.number}/comments",
                headers=headers,
                json={"body": body},
            )
            r.raise_for_status()
            state.posted_status_comment_id = r.json()["id"]
        else:
            r = await client.patch(
                f"https://api.github.com/repos/{pr.owner}/{pr.repo}/issues/comments/{state.posted_status_comment_id}",
                headers=headers,
                json={"body": body},
            )
            r.raise_for_status()

        # Check Run: create new (we don't track its conclusion lifecycle in the PoC)
        conclusion = {
            "applied_fix": "success",
            "no_action_needed": "neutral",
            "blocked": "failure",
        }[plan.action]
        r = await client.post(
            f"https://api.github.com/repos/{pr.owner}/{pr.repo}/check-runs",
            headers=headers,
            json={
                "name": "AutoFix",
                "head_sha": pr.head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": "AutoFix", "summary": plan.summary},
            },
        )
        r.raise_for_status()
        state.last_check_run_id = r.json()["id"]

    return state


def _render_status_markdown(state: WorkflowState, plan: FixPlan) -> str:
    lines = [
        f"### 🤖 AutoFix — iteration {state.iterations}",
        f"**Action:** `{plan.action}`",
        "",
        plan.summary,
    ]
    if plan.commit_sha:
        lines += ["", f"Commit: `{plan.commit_sha[:7]}`"]
    if plan.blocking_reason:
        lines += ["", f"**Blocked because:** {plan.blocking_reason}"]
    return "\n".join(lines)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_lifecycle.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/tools/github_mcp.py src/activities/__init__.py src/activities/lifecycle.py tests/test_lifecycle.py
git commit -m "feat(activities): github mcp + prepare/cleanup workdir + post_status"
```

---

## Task 7: Agent definition

**Goal:** Construct the Pydantic AI agent (`pr_fixer`), wrap it with `TemporalAgent`, smoke-test with `TestModel`.

**Files:**
- Create: `src/agents/__init__.py` (empty)
- Create: `src/agents/pr_fixer.py`
- Create: `tests/test_pr_fixer_agent.py`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_pr_fixer_agent.py`:

```python
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from src.agents.pr_fixer import agent
from src.models import AgentDeps, PRRef, FixPlan


@pytest.fixture
def agent_deps(tmp_repo: Path) -> AgentDeps:
    """Point the workdir helper at a tmp repo by monkeypatching workdir_root."""
    return AgentDeps(
        workdir_id="test",
        pr=PRRef(owner="o", repo="r", number=1, head_sha="a", head_ref="main"),
    )


async def test_agent_returns_fix_plan_with_test_model(agent_deps: AgentDeps):
    """With TestModel, agent returns a synthesized FixPlan without real LLM calls."""
    test_model = TestModel(custom_output_args={
        "action": "no_action_needed",
        "summary": "stub",
    })
    with agent.override(model=test_model, toolsets=[]):
        result = await agent.run("hello", deps=agent_deps)
    assert isinstance(result.output, FixPlan)
    assert result.output.action == "no_action_needed"
```

We override `toolsets=[]` so the GitHub MCP server is not actually launched during the unit test. The local repo toolset would also need a real workdir, which we sidestep by overriding to empty.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pr_fixer_agent.py -v`
Expected: ImportError on `src.agents.pr_fixer`.

- [ ] **Step 3: Implement `src/agents/pr_fixer.py`**

```python
from pydantic_ai import Agent
from pydantic_ai.durable_exec.temporal import TemporalAgent

from src.models import AgentDeps, FixPlan
from src.tools.github_mcp import build_github_mcp_server
from src.tools.local_repo import local_repo_toolset


INSTRUCTIONS = """\
You are an autonomous code-review assistant working on one GitHub Pull Request.

You receive: a short brief listing pending events (new review comments, CI \
results) and the PR identifier. For each event:

1. Use the `github` toolset to fetch full context (PR diff, comment bodies, check \
   run details).
2. Decide whether the event is a valid, actionable engineering request.
3. If yes, use the `repo` toolset to inspect the code, apply the smallest possible \
   edit with `apply_edit`, and verify locally with `run_ruff` and `run_pytest`.
4. Only call `git_commit_and_push` if local verification passes. If the push is \
   refused (`remote_advanced` etc.), do NOT retry blindly; report it as \
   `blocking_reason`.
5. If a comment is opinion-only, unclear, or out of scope, do not apply it. \
   Explain in `summary` why you skipped it.

Always return one structured `FixPlan` describing what you did in this iteration. \
Be concise.
"""


def _build_agent() -> Agent[AgentDeps, FixPlan]:
    return Agent(
        "anthropic:claude-sonnet-4-6",
        name="pr_fixer",
        deps_type=AgentDeps,
        output_type=FixPlan,
        toolsets=[build_github_mcp_server(), local_repo_toolset],
        instructions=INSTRUCTIONS,
    )


# Eagerly built at import time so PydanticAIPlugin can register its activities.
# Tests that don't need it set GITHUB_TOKEN to a dummy value, or import
# lazily through a wrapper if that proves awkward.
agent = _build_agent()
temporal_agent = TemporalAgent(agent)
```

- [ ] **Step 4: Make tests tolerate the missing token**

The build needs `GITHUB_TOKEN`. For tests, set a dummy in `tests/conftest.py`. Append:

```python
import os

os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_pr_fixer_agent.py -v`
Expected: 1 passed.

- [ ] **Step 6: Verify all tests still pass**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/agents/__init__.py src/agents/pr_fixer.py tests/test_pr_fixer_agent.py tests/conftest.py
git commit -m "feat(agent): pr_fixer agent + TemporalAgent wrap"
```

---

## Task 8: Workflow

**Goal:** Implement `PRAutofixWorkflow`, test it under `WorkflowEnvironment` with the agent overridden to a `TestModel`.

**Files:**
- Create: `src/workflows/__init__.py` (empty)
- Create: `src/workflows/pr_autofix.py`
- Create: `tests/test_workflow.py`

- [ ] **Step 1: Write the failing workflow test**

Create `tests/test_workflow.py`:

```python
import uuid

import pytest
from pydantic_ai.models.test import TestModel
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin

from src.agents.pr_fixer import agent
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

    with agent.override(model=test_model, toolsets=[]):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workflow.py -v`
Expected: ImportError on `src.workflows.pr_autofix`.

- [ ] **Step 3: Implement `src/workflows/pr_autofix.py`**

```python
import asyncio
from datetime import timedelta

from temporalio import workflow
from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow

with workflow.unsafe.imports_passed_through():
    from src.agents.pr_fixer import temporal_agent
    from src.models import (
        PRRef,
        GitHubEvent,
        WorkflowState,
        AgentDeps,
        FixPlan,
    )
    from src.activities.lifecycle import (
        prepare_workdir,
        cleanup_workdir,
        post_status,
    )


MAX_ITERATIONS = 5
IDLE_TIMEOUT = timedelta(minutes=30)


@workflow.defn(name="PRAutofixWorkflow")
class PRAutofixWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [temporal_agent]

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
            while (
                not self._state.closed
                and self._state.iterations < MAX_ITERATIONS
            ):
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

                deps = AgentDeps(
                    workdir_id=workflow.info().workflow_id,
                    pr=self._state.pr,
                )
                result = await temporal_agent.run(
                    self._build_prompt(events_snapshot),
                    deps=deps,
                )
                plan: FixPlan = result.output
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

    def _build_prompt(self, events: list[GitHubEvent]) -> str:
        pr = self._state.pr
        lines = [
            f"PR: {pr.owner}/{pr.repo}#{pr.number} (head {pr.head_sha[:7]} on {pr.head_ref})",
            f"Iteration: {self._state.iterations}",
            "",
            "Pending events:",
        ]
        for e in events:
            lines.append(f"- [{e.kind}] delivery={e.delivery_id} payload_keys={sorted(e.payload.keys())}")
        return "\n".join(lines)

    def _apply_plan(self, plan: FixPlan) -> None:
        self._state.processed_comment_ids |= set(plan.addressed_comment_ids)
```

- [ ] **Step 4: Run workflow test to verify it passes**

Run: `uv run pytest tests/test_workflow.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/workflows/__init__.py src/workflows/pr_autofix.py tests/test_workflow.py
git commit -m "feat(workflow): signal-driven PRAutofixWorkflow with agent loop"
```

---

## Task 9: Gateway (FastAPI webhook receiver)

**Goal:** HTTP endpoint that verifies HMAC, maps payload → `GitHubEvent`, and `signal_with_start`s the workflow. Tested with `TestClient` and a mock Temporal client.

**Files:**
- Create: `src/gateway/__init__.py` (empty)
- Create: `src/gateway/app.py`
- Create: `tests/test_gateway.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gateway.py`:

```python
import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.gateway.app import create_app


WEBHOOK_SECRET = "shh"


def _sign(body: bytes) -> str:
    mac = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


@pytest.fixture
def client_and_temporal():
    fake_client = AsyncMock()
    app = create_app(temporal_client=fake_client, webhook_secret=WEBHOOK_SECRET)
    return TestClient(app), fake_client


def test_rejects_bad_signature(client_and_temporal):
    client, _ = client_and_temporal
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "abc",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
        content=b"{}",
    )
    assert r.status_code == 401


def test_drops_unhandled_event_kind(client_and_temporal):
    client, fake = client_and_temporal
    body = b"{}"
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "release",
            "X-GitHub-Delivery": "abc",
            "X-Hub-Signature-256": _sign(body),
        },
        content=body,
    )
    assert r.status_code == 204
    fake.start_workflow.assert_not_called()


def test_pull_request_opened_starts_workflow(client_and_temporal):
    client, fake = client_and_temporal
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 42,
            "head": {"sha": "abc1234", "ref": "feature-x"},
            "base": {"repo": {"owner": {"login": "o"}, "name": "r"}},
        },
        "repository": {"owner": {"login": "o"}, "name": "r"},
    }
    body = json.dumps(payload).encode()
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-1",
            "X-Hub-Signature-256": _sign(body),
        },
        content=body,
    )
    assert r.status_code == 202
    fake.start_workflow.assert_awaited_once()
    kwargs = fake.start_workflow.call_args.kwargs
    assert kwargs["id"] == "pr-autofix-o-r-42"
    assert kwargs["start_signal"] == "on_event"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gateway.py -v`
Expected: ImportError on `src.gateway.app`.

- [ ] **Step 3: Implement `src/gateway/app.py`**

```python
import hashlib
import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from temporalio.client import Client, WorkflowIDReusePolicy

from src.models import GitHubEvent, PRRef
from src.workflows.pr_autofix import PRAutofixWorkflow


def _verify(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, signature)


def _project_event(
    event: str, payload: dict, delivery_id: str
) -> tuple[PRRef, GitHubEvent] | None:
    if event == "pull_request":
        action = payload.get("action")
        kind = {"opened": "pr_opened", "synchronize": "pr_synchronize"}.get(action)
        if not kind:
            return None
        pr_node = payload["pull_request"]
        pr = PRRef(
            owner=payload["repository"]["owner"]["login"],
            repo=payload["repository"]["name"],
            number=pr_node["number"],
            head_sha=pr_node["head"]["sha"],
            head_ref=pr_node["head"]["ref"],
        )
    elif event in ("issue_comment", "pull_request_review_comment", "check_suite"):
        # PoC: extract minimal PR identity if present
        if event == "issue_comment":
            issue = payload.get("issue", {})
            if "pull_request" not in issue:
                return None
            kind = "issue_comment"
            pr = PRRef(
                owner=payload["repository"]["owner"]["login"],
                repo=payload["repository"]["name"],
                number=issue["number"],
                head_sha=payload.get("pull_request", {}).get("head", {}).get("sha", ""),
                head_ref=payload.get("pull_request", {}).get("head", {}).get("ref", ""),
            )
        elif event == "pull_request_review_comment":
            pr_node = payload.get("pull_request", {})
            kind = "review_comment"
            pr = PRRef(
                owner=payload["repository"]["owner"]["login"],
                repo=payload["repository"]["name"],
                number=pr_node["number"],
                head_sha=pr_node["head"]["sha"],
                head_ref=pr_node["head"]["ref"],
            )
        else:  # check_suite
            action = payload.get("action")
            if action != "completed":
                return None
            kind = "check_suite_completed"
            cs = payload["check_suite"]
            prs = cs.get("pull_requests") or []
            if not prs:
                return None
            pr_node = prs[0]
            pr = PRRef(
                owner=payload["repository"]["owner"]["login"],
                repo=payload["repository"]["name"],
                number=pr_node["number"],
                head_sha=cs["head_sha"],
                head_ref=cs["head_branch"],
            )
    else:
        return None

    return pr, GitHubEvent(kind=kind, delivery_id=delivery_id, payload=payload)


def create_app(
    *, temporal_client: Any, webhook_secret: str, task_queue: str = "pr-autofix"
) -> FastAPI:
    app = FastAPI(title="PR Autofix Gateway")

    @app.post("/webhook")
    async def webhook(
        request: Request,
        x_github_event: str = Header(...),
        x_github_delivery: str = Header(...),
        x_hub_signature_256: str | None = Header(default=None),
    ):
        body = await request.body()
        if not _verify(webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="bad signature")

        payload = await request.json() if body else {}
        projected = _project_event(x_github_event, payload, x_github_delivery)
        if projected is None:
            return Response(status_code=204)

        pr, event = projected
        wf_id = f"pr-autofix-{pr.owner}-{pr.repo}-{pr.number}"
        await temporal_client.start_workflow(
            PRAutofixWorkflow.run,
            pr,
            id=wf_id,
            task_queue=task_queue,
            start_signal="on_event",
            start_signal_args=[event],
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        return Response(status_code=202)

    return app


def build_default_app() -> FastAPI:
    """Entry point for uvicorn: builds the real client lazily."""
    import asyncio

    async def _client() -> Client:
        return await Client.connect(os.environ.get("TEMPORAL_TARGET", "localhost:7233"))

    secret = os.environ["GITHUB_WEBHOOK_SECRET"]
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "pr-autofix")
    client = asyncio.run(_client())
    return create_app(
        temporal_client=client, webhook_secret=secret, task_queue=task_queue
    )


app = build_default_app() if os.environ.get("GATEWAY_BOOT") == "1" else None  # uvicorn imports this
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gateway.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/__init__.py src/gateway/app.py tests/test_gateway.py
git commit -m "feat(gateway): HMAC-verified webhook → signal_with_start"
```

---

## Task 10: Worker, docker-compose, README

**Goal:** A worker entrypoint that registers everything, a docker-compose stack, and a README that gets a developer from clone to first manual smoke test.

**Files:**
- Create: `src/worker.py`
- Create: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `README.md`

This task is configuration, not TDD. We rely on the test suite already being green and end with a manual smoke test step.

- [ ] **Step 1: Implement `src/worker.py`**

```python
import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin

from src.workflows.pr_autofix import PRAutofixWorkflow
from src.activities.lifecycle import (
    prepare_workdir,
    cleanup_workdir,
    post_status,
)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(
        os.environ.get("TEMPORAL_TARGET", "localhost:7233"),
        plugins=[PydanticAIPlugin()],
    )
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "pr-autofix")
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[PRAutofixWorkflow],
        activities=[prepare_workdir, cleanup_workdir, post_status],
    ):
        logging.info("worker listening on task queue %s", task_queue)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

# git + node (for npx github-mcp-server)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen || uv sync --no-dev

COPY src/ ./src/

# Worker entrypoint by default; gateway overrides via `command:` in compose
CMD ["uv", "run", "python", "-m", "src.worker"]
```

- [ ] **Step 3: Create `docker-compose.yml`**

```yaml
services:
  temporal:
    image: temporalio/auto-setup:1.24
    environment:
      - DB=postgres12
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=temporal
      - POSTGRES_SEEDS=postgres
    depends_on:
      - postgres
    ports:
      - "7233:7233"

  postgres:
    image: postgres:15
    environment:
      - POSTGRES_PASSWORD=temporal
      - POSTGRES_USER=temporal
    volumes:
      - temporal_pg:/var/lib/postgresql/data

  temporal-ui:
    image: temporalio/ui:2.30.1
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
      - TEMPORAL_CORS_ORIGINS=http://localhost:3000
    depends_on:
      - temporal
    ports:
      - "8233:8080"

  worker:
    build: .
    env_file: .env
    environment:
      - TEMPORAL_TARGET=temporal:7233
    depends_on:
      - temporal

  gateway:
    build: .
    env_file: .env
    environment:
      - TEMPORAL_TARGET=temporal:7233
      - GATEWAY_BOOT=1
    depends_on:
      - temporal
    command: ["uv", "run", "uvicorn", "src.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
    ports:
      - "8000:8000"

volumes:
  temporal_pg:
```

- [ ] **Step 4: Create `README.md`**

```markdown
# agent-temporal — PR Autofix PoC

PoC of an agentic workflow on **Temporal + Pydantic AI** that auto-fixes
GitHub Pull Requests (review comments, lint, tests).

See [design doc](docs/superpowers/specs/2026-05-25-temporal-pydantic-pr-autofix-design.md).

## Prerequisites

- Docker + docker-compose
- A GitHub fine-grained PAT with PR read/write + checks write on a playground repo
- An Anthropic API key
- A way to expose `localhost:8000` to GitHub (smee.io or cloudflared)

## Local run

1. `cp .env.example .env` and fill in the values.
2. `docker compose up --build`
3. Temporal UI: <http://localhost:8233>
4. Gateway: <http://localhost:8000/webhook>

## Wiring the webhook

Two easy options:

### Option A — smee.io
```bash
npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8000/webhook
```
Then in your playground repo, add a webhook pointing at the smee URL,
content type `application/json`, secret = `GITHUB_WEBHOOK_SECRET` from `.env`,
events: PR, Issue comments, PR review comments, Check suites.

### Option B — cloudflared
```bash
cloudflared tunnel --url http://localhost:8000
```
Same webhook configuration, target = the cloudflared URL.

## Manual smoke test

1. Open a PR on your playground repo with a deliberate lint violation
   (e.g. `import os` unused).
2. The gateway receives `pull_request.opened`, starts the workflow.
3. The worker logs show the agent iteration; the PR gets a status comment.
4. Check the Temporal UI to inspect the workflow run.

## Running tests

```bash
uv run pytest -v
```

## Limitations (PoC)

- Single Python repo; not language-agnostic.
- We run pytest inside the worker container — fine for a playground, not for
  arbitrary code.
- One rolling status comment per PR, no auto-merge.
- No observability beyond worker logs + Temporal UI.
```

- [ ] **Step 5: Sanity check the full suite still passes**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 6: Bring up the stack**

```bash
docker compose up --build
```

Expected: `worker listening on task queue pr-autofix` log line, Temporal UI
reachable on :8233, gateway reachable on :8000.

- [ ] **Step 7: Commit**

```bash
git add src/worker.py Dockerfile docker-compose.yml README.md
git commit -m "chore: worker entrypoint + docker-compose stack + README"
```

---

## After all tasks complete

- All 25+ unit tests green (`uv run pytest -v`).
- `docker compose up` brings up Temporal, UI, worker, gateway.
- Opening a PR on the playground repo triggers an iteration, posts a
  status comment, and (for fixable lint) pushes a commit.

Follow-up work, deliberately deferred:
- Logfire + structured tool-call traces (`LogfirePlugin`).
- Token-budget guardrail per workflow.
- Container-sandboxed `run_pytest` so the worker is safe against arbitrary
  repos.
- GitHub App auth instead of PAT.
- Language-agnostic toolset (config-driven lint/test commands).
