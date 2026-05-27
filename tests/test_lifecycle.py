from base64 import b64encode
from pathlib import Path

import pytest

from src.activities.lifecycle import (
    _cleanup_workdir_at,
    _git_network_args,
    _prepare_workdir_at,
    _push_changes_at,
    _run,
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


def test_prepare_workdir_uses_head_ref_as_local_branch(tmp_path: Path, tmp_repo_with_remote: Path):
    target = tmp_path / "autofix-wf1" / "repo"
    remote_url = str(tmp_repo_with_remote.parent / "remote.git")
    _prepare_workdir_at(
        target=target,
        clone_url=remote_url,
        head_ref="main",
        head_sha="HEAD",
    )
    import subprocess
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=target, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "main"


def test_prepare_workdir_wipes_dirty_local_edits(tmp_path: Path, tmp_repo_with_remote: Path):
    """Second prepare_workdir_at on the same workdir must wipe local edits
    from a previous iteration so the checkout doesn't refuse."""
    target = tmp_path / "autofix-wf1" / "repo"
    remote_url = str(tmp_repo_with_remote.parent / "remote.git")
    _prepare_workdir_at(
        target=target,
        clone_url=remote_url,
        head_ref="main",
        head_sha="HEAD",
    )
    # Simulate an uncommitted edit left by a previous iteration.
    (target / "hello.py").write_text("agent edits never committed\n")
    # Second call: must succeed and reset the file.
    _prepare_workdir_at(
        target=target,
        clone_url=remote_url,
        head_ref="main",
        head_sha="HEAD",
    )
    assert (target / "hello.py").read_text().startswith("def hello()")


def test_prepare_workdir_sets_repo_local_bot_identity(tmp_path: Path, tmp_repo_with_remote: Path):
    target = tmp_path / "autofix-wf1" / "repo"
    remote_url = str(tmp_repo_with_remote.parent / "remote.git")
    _prepare_workdir_at(
        target=target,
        clone_url=remote_url,
        head_ref="main",
        head_sha="HEAD",
    )
    import subprocess
    name = subprocess.run(
        ["git", "config", "user.name"],
        cwd=target, capture_output=True, text=True, check=True,
    ).stdout.strip()
    email = subprocess.run(
        ["git", "config", "user.email"],
        cwd=target, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert name == "Claude"
    assert email == "noreply@anthropic.com"


def test_git_network_args_uses_temporary_basic_auth_header():
    args = _git_network_args(["fetch", "origin", "main"], "secret-token")
    encoded = b64encode(b"x-access-token:secret-token").decode()
    assert args == [
        "git",
        "-c",
        f"http.extraHeader=Authorization: Basic {encoded}",
        "fetch",
        "origin",
        "main",
    ]


def test_git_failure_does_not_echo_credential_bearing_command(tmp_path: Path):
    credential = b64encode(b"x-access-token:secret-token").decode()
    with pytest.raises(RuntimeError) as exc_info:
        _run(
            ["git", "-c", f"http.extraHeader=Authorization: Basic {credential}", "bad-command"],
            tmp_path,
        )
    assert "secret-token" not in str(exc_info.value)
    assert credential not in str(exc_info.value)


def test_trusted_push_is_idempotent(tmp_repo_with_remote: Path):
    pr = PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="main")
    (tmp_repo_with_remote / "hello.py").write_text("x = 2\n")
    first = _push_changes_at(
        target=tmp_repo_with_remote,
        pr=pr,
        message="autofix: apply",
        operation_key="stable-key",
    )
    second = _push_changes_at(
        target=tmp_repo_with_remote,
        pr=pr,
        message="autofix: apply",
        operation_key="stable-key",
    )
    assert first.pushed is True
    assert second.pushed is True
    assert second.commit_sha == first.commit_sha
