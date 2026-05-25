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
