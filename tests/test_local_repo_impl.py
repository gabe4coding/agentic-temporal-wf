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
