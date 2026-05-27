"""Tests that local command-execution functions in _local_repo_impl
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
