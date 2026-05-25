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


from tests.conftest import _run  # type: ignore[attr-defined]
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
