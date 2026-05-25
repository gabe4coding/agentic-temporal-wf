"""Tests that the 4 command-execution functions in _local_repo_impl
dispatch to exec_in_sandbox when given a SandboxHandle.

We monkeypatch `_exec_in_sandbox_impl` with a recorder so the tests
don't need a Docker daemon. The legacy Path-based behavior is covered
by tests/test_local_repo_impl.py.
"""
from __future__ import annotations

import pytest

from src.models import SandboxHandle, ExecResult
from src.tools import _local_repo_impl as impl


class _ExecRecorder:
    """Records (handle, cmd) tuples and returns canned ExecResults."""

    def __init__(self) -> None:
        self.calls: list[tuple[SandboxHandle, list[str]]] = []
        self.responses: list[ExecResult] = []
        self.default = ExecResult(exit_code=0, stdout="", stderr="")

    def __call__(self, handle: SandboxHandle, cmd: list[str]) -> ExecResult:
        self.calls.append((handle, list(cmd)))
        if self.responses:
            return self.responses.pop(0)
        return self.default


@pytest.fixture
def exec_recorder(monkeypatch: pytest.MonkeyPatch) -> _ExecRecorder:
    rec = _ExecRecorder()
    # Patch the symbol imported lazily inside _exec_at.
    import src.activities.sandbox as sandbox_mod
    monkeypatch.setattr(sandbox_mod, "_exec_in_sandbox_impl", rec)
    return rec


@pytest.fixture
def handle() -> SandboxHandle:
    return SandboxHandle(container_id="cid", workdir="/tmp/autofix-wf-1/repo")


# ---------- run_ruff ----------

def test_run_ruff_dispatches_to_sandbox(exec_recorder, handle):
    exec_recorder.default = ExecResult(exit_code=0, stdout="", stderr="")
    res = impl.run_ruff(handle)

    assert res.exit_code == 0
    assert res.violations == []
    assert len(exec_recorder.calls) == 1
    h, cmd = exec_recorder.calls[0]
    assert h is handle
    assert cmd == ["ruff", "check", ".", "--output-format=json"]


def test_run_ruff_parses_violations_from_sandbox_stdout(exec_recorder, handle):
    exec_recorder.default = ExecResult(
        exit_code=1,
        stdout=(
            '[{"filename":"bad.py","code":"F401",'
            '"message":"unused import","location":{"row":3}}]'
        ),
        stderr="",
    )
    res = impl.run_ruff(handle)
    assert res.exit_code == 1
    assert len(res.violations) == 1
    assert res.violations[0].filename == "bad.py"
    assert res.violations[0].line == 3


# ---------- run_pytest ----------

def test_run_pytest_dispatches_to_sandbox(exec_recorder, handle):
    exec_recorder.default = ExecResult(
        exit_code=0, stdout="3 passed in 0.05s\n", stderr=""
    )
    res = impl.run_pytest(handle)
    assert res.exit_code == 0
    assert res.passed == 3
    assert exec_recorder.calls[0][1] == ["pytest", "-q", "--no-header"]


def test_run_pytest_passes_target_argument(exec_recorder, handle):
    impl.run_pytest(handle, "tests/test_x.py::test_y")
    cmd = exec_recorder.calls[0][1]
    assert cmd == ["pytest", "-q", "--no-header", "tests/test_x.py::test_y"]


# ---------- git_status ----------

def test_git_status_dispatches_to_sandbox(exec_recorder, handle):
    exec_recorder.responses = [
        ExecResult(exit_code=0, stdout="feature/x\n"),
        ExecResult(exit_code=0, stdout=""),  # clean
    ]
    s = impl.git_status(handle)
    assert s.branch == "feature/x"
    assert s.dirty is False


def test_git_status_reports_dirty_when_porcelain_nonempty(exec_recorder, handle):
    exec_recorder.responses = [
        ExecResult(exit_code=0, stdout="main\n"),
        ExecResult(exit_code=0, stdout=" M hello.py\n"),
    ]
    s = impl.git_status(handle)
    assert s.dirty is True


# ---------- git_commit_and_push ----------

def test_git_commit_and_push_no_changes_dispatched_to_sandbox(
    exec_recorder, handle
):
    exec_recorder.responses = [
        ExecResult(exit_code=0, stdout="main\n"),       # rev-parse branch
        ExecResult(exit_code=0, stdout=""),              # add -A
        ExecResult(exit_code=0, stdout=""),              # diff --cached --quiet -> 0
    ]
    res = impl.git_commit_and_push(handle, "autofix: foo")
    assert res.pushed is False
    assert res.reason == "no_changes"


def test_git_commit_and_push_success_flow_through_sandbox(
    exec_recorder, handle
):
    sha = "a" * 40
    exec_recorder.responses = [
        ExecResult(exit_code=0, stdout="main\n"),       # rev-parse branch
        ExecResult(exit_code=0, stdout=""),              # add -A
        ExecResult(exit_code=1, stdout=""),              # diff --cached (has changes)
        ExecResult(exit_code=0, stdout=""),              # commit
        ExecResult(exit_code=0, stdout=f"{sha}\n"),      # rev-parse HEAD
        ExecResult(exit_code=0, stdout=""),              # fetch
        ExecResult(exit_code=0, stdout="0\n"),           # rev-list --count
        ExecResult(exit_code=0, stdout=""),              # push
    ]
    res = impl.git_commit_and_push(handle, "autofix: x")
    assert res.pushed is True
    assert res.commit_sha == sha


def test_git_commit_and_push_appends_autofix_trailer(exec_recorder, handle):
    """The trailer must end up in the `git commit -m` invocation that
    reaches the sandbox — that's the only thing the gateway can match."""
    exec_recorder.responses = [
        ExecResult(exit_code=0, stdout="main\n"),
        ExecResult(exit_code=0, stdout=""),
        ExecResult(exit_code=1, stdout=""),
        ExecResult(exit_code=0, stdout=""),
        ExecResult(exit_code=0, stdout="b" * 40 + "\n"),
        ExecResult(exit_code=0, stdout=""),
        ExecResult(exit_code=0, stdout="0\n"),
        ExecResult(exit_code=0, stdout=""),
    ]
    impl.git_commit_and_push(handle, "autofix: y")

    # 4th call is `git commit -m <message>`
    _, commit_cmd = exec_recorder.calls[3]
    assert commit_cmd[:3] == ["git", "commit", "-m"]
    assert "[autofix-bot]" in commit_cmd[3]
