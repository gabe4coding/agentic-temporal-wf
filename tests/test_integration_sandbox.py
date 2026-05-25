"""Integration tests: exercise the real sandbox + dispatch + egress
against a live Docker daemon.

NOT part of the default `pytest` run (the `integration` marker is
deselected by addopts in pyproject.toml). Run them from INSIDE the
worker container so that:
  - HOSTNAME refers to a Docker container (needed for volumes_from)
  - sandbox-net is reachable and the egress-proxy DNS alias resolves
  - the worker_tmp volume is the same filesystem the sandbox will see

Procedure:
    docker compose up -d
    docker compose exec worker uv run pytest -m integration \\
        tests/test_integration_sandbox.py -v

The tests cover three layers in one slice:
  L0 (per-workflow sandbox)  — provision/exec/teardown actually work
  L0 dispatch                — run_ruff via SandboxHandle routes to exec
  Egress allow-list          — github.com → allowed, example.com → denied
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from src.activities.sandbox import (
    _provision_sandbox_impl,
    _exec_in_sandbox_impl,
    _teardown_sandbox_impl,
)
from src.models import SandboxHandle


pytestmark = pytest.mark.integration


# ---------- helpers ----------

def _docker_available() -> bool:
    """True if we can reach the local Docker daemon."""
    try:
        import docker  # local import to avoid import-time failure
        docker.from_env().ping()
        return True
    except Exception:
        return False


def _image_exists(tag: str) -> bool:
    try:
        import docker
        docker.from_env().images.get(tag)
        return True
    except Exception:
        return False


def _force_remove(name: str) -> None:
    """Best-effort cleanup of a leftover container by name."""
    import docker
    from docker.errors import NotFound
    try:
        c = docker.from_env().containers.get(name)
        c.remove(force=True)
    except NotFound:
        pass
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _skip_when_no_docker():
    if not _docker_available():
        pytest.skip("Docker daemon not reachable")
    if not _image_exists("agent-sandbox:latest"):
        pytest.skip(
            "agent-sandbox:latest image not built; run `docker compose build sandbox-image`"
        )


@pytest.fixture
def host_workdir(tmp_path: Path) -> Path:
    """Prepare a clean workdir under /tmp/autofix-* that the sandbox
    will inherit via volumes_from. We can't bind-mount tmp_path because
    that path is on the host filesystem, not inside the worker_tmp
    volume — so when running from the host (not inside the worker
    container) we put the workdir straight in /tmp."""
    base = Path(f"/tmp/autofix-integ-{os.getpid()}-{int(time.time())}")
    work = base / "repo"
    work.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.test"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    # F401 violation — easy to detect.
    (work / "broken.py").write_text("import sys\n\ndef hello():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=work, check=True)

    yield work

    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def sandbox(host_workdir: Path):
    """Provision a sandbox bound to host_workdir, ensure teardown."""
    name = "autofix-sbx-integ"
    _force_remove(name)  # leftover from a previous failed run

    # Provision. We pass a stable workflow_id so the container name is
    # predictable for cleanup.
    handle = _provision_sandbox_impl(
        workflow_id="integ", host_workdir=str(host_workdir)
    )
    try:
        yield handle
    finally:
        _teardown_sandbox_impl(handle)


# ---------- L0: provision/exec/teardown ----------

def test_provision_and_exec_basic(sandbox: SandboxHandle):
    """The sandbox container is up, exec_run reaches it, the workdir
    bind-mount is visible inside."""
    r = _exec_in_sandbox_impl(sandbox, ["ls", "-1", sandbox.workdir])
    assert r.exit_code == 0, r
    assert "broken.py" in r.stdout


def test_ruff_runs_inside_sandbox(sandbox: SandboxHandle):
    """ruff is installed in the sandbox image and detects the seeded
    F401 violation."""
    r = _exec_in_sandbox_impl(sandbox, ["ruff", "check", ".", "--output-format=json"])
    assert r.exit_code == 1, (r.exit_code, r.stdout, r.stderr)
    assert "F401" in r.stdout


# ---------- L0 dispatch: SandboxHandle in _local_repo_impl ----------

def test_local_repo_impl_run_ruff_dispatches_to_real_sandbox(
    sandbox: SandboxHandle,
):
    from src.tools._local_repo_impl import run_ruff

    res = run_ruff(sandbox)
    assert res.exit_code == 1
    assert any(v.code == "F401" for v in res.violations)


def test_local_repo_impl_git_status_dispatches_to_real_sandbox(
    sandbox: SandboxHandle,
):
    from src.tools._local_repo_impl import git_status

    s = git_status(sandbox)
    assert s.branch == "main"
    assert s.dirty is False  # nothing changed since the init commit


# ---------- Egress allow-list ----------

@pytest.fixture
def _egress_proxy_reachable(sandbox: SandboxHandle) -> bool:
    """Skip egress tests if the proxy isn't reachable from the sandbox.

    The proxy is only on sandbox-net; if the test is started without
    `docker compose up egress-proxy`, the sandbox can't see it.
    """
    r = _exec_in_sandbox_impl(sandbox, ["sh", "-c", "getent hosts egress-proxy"])
    if r.exit_code != 0:
        pytest.skip(
            "egress-proxy not reachable from sandbox; bring it up with "
            "`docker compose up -d egress-proxy`"
        )
    return True


def test_egress_allowed_domain_succeeds(
    sandbox: SandboxHandle, _egress_proxy_reachable: bool
):
    """github.com is on the allow-list → CONNECT through the proxy succeeds."""
    # We use git ls-remote because the agent-sandbox image already has git
    # but no curl. The proxy env is set on the container; git honors
    # https_proxy.
    r = _exec_in_sandbox_impl(
        sandbox,
        ["git", "ls-remote", "--exit-code", "https://github.com/torvalds/linux.git", "HEAD"],
    )
    assert r.exit_code == 0, (r.exit_code, r.stdout[:200], r.stderr[:500])


def test_egress_denied_domain_blocked(
    sandbox: SandboxHandle, _egress_proxy_reachable: bool
):
    """A domain not on the allow-list → proxy returns 403, git fails."""
    r = _exec_in_sandbox_impl(
        sandbox,
        ["git", "ls-remote", "--exit-code", "https://example.com/x.git", "HEAD"],
    )
    assert r.exit_code != 0, (
        "expected failure for non-allow-listed domain, got success"
    )
    # tinyproxy returns a 403-shaped error; git surfaces it in stderr.
    combined = (r.stdout + r.stderr).lower()
    assert "403" in combined or "forbidden" in combined or "denied" in combined or "filtered" in combined, combined[:500]
