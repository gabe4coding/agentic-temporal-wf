"""Unit tests for src.activities.sandbox.

The sandbox layer talks to the local Docker daemon via the `docker` SDK.
These tests monkeypatch `docker.from_env` with an in-process fake so they
run without a daemon. A separate marker (`@pytest.mark.integration`) is
reserved for tests that need a real Docker socket — not implemented here.

Design note (hybrid bind-mount): provision_sandbox does NOT clone the
repo. It inherits the worker's /tmp volume via `volumes_from=[worker]`
so the workdir prepared by prepare_workdir (host path
`/tmp/autofix-{wf}/repo`) is visible inside the sandbox at the same
path. The workflow_id-scoped workdir is the SandboxHandle.workdir.
"""
from __future__ import annotations

from typing import Any

import pytest


# ---------- fake docker client ----------

class _FakeContainer:
    def __init__(self, container_id: str = "sbx-cid-abc") -> None:
        self.id = container_id
        self.exec_calls: list[tuple[list[str], dict[str, Any]]] = []
        self.paused = False
        self.stopped = False
        self.removed = False
        # Default exec result; tests can override via .next_exec
        self.next_exec: tuple[int, tuple[bytes, bytes]] = (0, (b"", b""))

    def exec_run(self, cmd, **kwargs):
        self.exec_calls.append((list(cmd), kwargs))
        rc, (out, err) = self.next_exec
        if kwargs.get("demux"):
            return rc, (out, err)
        return rc, (out or b"") + (err or b"")

    def pause(self) -> None:
        self.paused = True

    def unpause(self) -> None:
        self.paused = False

    def stop(self, timeout: int = 5) -> None:
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self) -> None:
        self.run_calls: list[tuple[str, dict[str, Any]]] = []
        self.created: _FakeContainer | None = None
        self.get_raises: Exception | None = None

    def run(self, image: str, **kwargs) -> _FakeContainer:
        self.run_calls.append((image, kwargs))
        self.created = _FakeContainer()
        return self.created

    def get(self, container_id: str) -> _FakeContainer:
        if self.get_raises is not None:
            raise self.get_raises
        assert self.created is not None, "no container provisioned"
        assert self.created.id == container_id
        return self.created


class _FakeDockerClient:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> _FakeDockerClient:
    fake = _FakeDockerClient()
    import src.activities.sandbox as sandbox_mod
    monkeypatch.setattr(sandbox_mod.docker, "from_env", lambda: fake)
    return fake


# ---------- provision_sandbox ----------

def test_provision_sandbox_runs_container_with_hardened_args(fake_docker):
    from src.activities.sandbox import _provision_sandbox_impl

    handle = _provision_sandbox_impl(
        workflow_id="wf-xyz",
        host_workdir="/tmp/autofix-wf-xyz/repo",
    )

    assert len(fake_docker.containers.run_calls) == 1
    image, kwargs = fake_docker.containers.run_calls[0]
    assert image == "agent-sandbox:latest"
    assert kwargs["name"] == "autofix-sbx-wf-xyz"
    assert kwargs["detach"] is True
    assert kwargs["auto_remove"] is False
    # Hardening
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in kwargs["security_opt"][0]
    assert kwargs["mem_limit"] == "2g"
    assert kwargs["pids_limit"] == 256
    # Sandbox keeps running until teardown
    assert kwargs["command"] == ["sleep", "infinity"]
    # Handle exposes the workflow-scoped workdir (same path on host & sandbox
    # thanks to volumes_from inheritance below)
    assert handle.container_id == "sbx-cid-abc"
    assert handle.workdir == "/tmp/autofix-wf-xyz/repo"


def test_provision_sandbox_inherits_worker_mounts_via_volumes_from(
    fake_docker, monkeypatch: pytest.MonkeyPatch
):
    """The sandbox must see the worker's /tmp volume so the workdir
    prepared by prepare_workdir (host path) resolves identically inside
    the sandbox. We achieve that via Docker's `volumes_from` referencing
    the worker container name (HOSTNAME inside the worker)."""
    from src.activities.sandbox import _provision_sandbox_impl

    monkeypatch.setenv("HOSTNAME", "worker-container-name")

    _provision_sandbox_impl(
        workflow_id="wf-1", host_workdir="/tmp/autofix-wf-1/repo"
    )

    _, kwargs = fake_docker.containers.run_calls[0]
    assert kwargs["volumes_from"] == ["worker-container-name"]


def test_provision_sandbox_does_not_clone_or_fetch(fake_docker):
    """v2: the host workdir is already prepared by prepare_workdir.
    The sandbox just runs `sleep infinity` and exec'd commands operate
    on the bind-mounted workdir."""
    from src.activities.sandbox import _provision_sandbox_impl

    _provision_sandbox_impl(
        workflow_id="wf-1", host_workdir="/tmp/autofix-wf-1/repo"
    )

    container = fake_docker.containers.created
    # No setup exec_run calls — sandbox is a passive runtime.
    assert container.exec_calls == []


# ---------- exec_in_sandbox ----------

def test_exec_in_sandbox_runs_command_in_workdir(fake_docker):
    from src.activities.sandbox import _exec_in_sandbox_impl
    from src.models import SandboxHandle

    # Pre-provision so .get() returns a container
    fake_docker.containers.run("agent-sandbox:latest")
    fake_docker.containers.created.next_exec = (0, (b"ok\n", b""))

    handle = SandboxHandle(
        container_id="sbx-cid-abc", workdir="/tmp/autofix-wf-1/repo"
    )
    result = _exec_in_sandbox_impl(handle, ["ruff", "check", "."])

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.stderr == ""

    container = fake_docker.containers.created
    last_cmd, kwargs = container.exec_calls[-1]
    assert last_cmd == ["ruff", "check", "."]
    assert kwargs["workdir"] == "/tmp/autofix-wf-1/repo"
    assert kwargs["demux"] is True


def test_exec_in_sandbox_propagates_non_zero_exit(fake_docker):
    from src.activities.sandbox import _exec_in_sandbox_impl
    from src.models import SandboxHandle

    fake_docker.containers.run("agent-sandbox:latest")
    fake_docker.containers.created.next_exec = (1, (b"", b"boom\n"))

    handle = SandboxHandle(
        container_id="sbx-cid-abc", workdir="/tmp/autofix-wf-1/repo"
    )
    result = _exec_in_sandbox_impl(handle, ["pytest"])

    assert result.exit_code == 1
    assert result.stderr == "boom\n"


# ---------- pause / resume ----------

def test_pause_and_resume_sandbox_toggle_container_state(fake_docker):
    from src.activities.sandbox import (
        _pause_sandbox_impl,
        _resume_sandbox_impl,
    )
    from src.models import SandboxHandle

    fake_docker.containers.run("agent-sandbox:latest")
    handle = SandboxHandle(container_id="sbx-cid-abc", workdir="/tmp/wf/repo")

    _pause_sandbox_impl(handle)
    assert fake_docker.containers.created.paused is True

    _resume_sandbox_impl(handle)
    assert fake_docker.containers.created.paused is False


# ---------- teardown ----------

def test_teardown_sandbox_stops_and_removes(fake_docker):
    from src.activities.sandbox import _teardown_sandbox_impl
    from src.models import SandboxHandle

    fake_docker.containers.run("agent-sandbox:latest")
    handle = SandboxHandle(container_id="sbx-cid-abc", workdir="/tmp/wf/repo")

    _teardown_sandbox_impl(handle)

    assert fake_docker.containers.created.stopped is True
    assert fake_docker.containers.created.removed is True


def test_teardown_sandbox_is_idempotent_when_container_already_gone(fake_docker):
    """If the container is already removed (NotFound), teardown must not raise.

    This matters because Temporal can retry the teardown activity after a
    crash. Idempotency keeps the workflow from getting stuck.
    """
    from docker.errors import NotFound

    from src.activities.sandbox import _teardown_sandbox_impl
    from src.models import SandboxHandle

    fake_docker.containers.get_raises = NotFound("gone")
    handle = SandboxHandle(container_id="sbx-cid-gone", workdir="/tmp/wf/repo")

    # Must not raise.
    _teardown_sandbox_impl(handle)
