import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


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
