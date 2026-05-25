"""Per-workflow Docker sandbox for agent execution.

The worker container mounts /var/run/docker.sock and uses the Docker
Python SDK to spawn one sibling container per Temporal workflow. The
sibling container is the actual L0 sandbox: agent-generated tool calls
run inside it, not inside the worker. The worker stays a control plane.

Lifecycle:
    provision_sandbox(pr)        -> SandboxHandle   (start workflow)
    exec_in_sandbox(handle, cmd) -> ExecResult      (each tool call)
    pause_sandbox / resume_sandbox                   (idle suspension)
    teardown_sandbox(handle)                          (workflow end)

Determinism: container name is derived from workflow_id so retries land
on the same container as long as it still exists.

Security posture (v1):
    - cap_drop ALL, no-new-privileges, pids/cpu/mem limits
    - tmpfs /tmp inside the sandbox, no host bind-mounts
    - egress allow-list is left to the docker network configuration —
      see docker-compose.yml `sandbox-net` (TODO).

Out of scope for v1: auto-pause-on-idle wiring, snapshot/fork. The
pause/resume activities are provided so the workflow can drive them;
the timer is the workflow's job.
"""
from __future__ import annotations

from typing import Any

import docker
from docker.errors import NotFound
from temporalio import activity

from src.models import PRRef, SandboxHandle, ExecResult


SANDBOX_IMAGE = "agent-sandbox:latest"
SANDBOX_WORKDIR = "/work"


# Re-exported for backward compatibility with callers that imported from
# src.activities.sandbox before the move to src.models.
__all__ = [
    "SandboxHandle",
    "ExecResult",
    "provision_sandbox",
    "exec_in_sandbox",
    "pause_sandbox",
    "resume_sandbox",
    "teardown_sandbox",
]


def _container_name(workflow_id: str) -> str:
    # Docker container names must match [a-zA-Z0-9][a-zA-Z0-9_.-]+
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in workflow_id)
    return f"autofix-sbx-{safe}"


def _provision_sandbox_impl(
    *, workflow_id: str, host_workdir: str
) -> SandboxHandle:
    """Start a per-workflow sandbox container.

    The container inherits the worker's mounts (notably /tmp where
    prepare_workdir cloned the repo) via Docker's `volumes_from`, so the
    same host path resolves identically inside the sandbox. The
    container itself is a passive runtime: it runs `sleep infinity` and
    waits for `exec_in_sandbox` calls.

    Args:
        workflow_id: used to derive the container name (stable across retries).
        host_workdir: the path of the prepared workdir on the host, which
            also becomes the SandboxHandle.workdir thanks to volumes_from.
    """
    import os as _os

    client = docker.from_env()
    # The worker container's hostname is the docker-internal name we
    # pass to volumes_from. In docker-compose, $HOSTNAME == container id.
    worker_hostname = _os.environ.get("HOSTNAME") or _os.environ.get(
        "WORKER_CONTAINER_NAME"
    )
    run_kwargs: dict[str, Any] = {
        "name": _container_name(workflow_id),
        "command": ["sleep", "infinity"],
        "detach": True,
        "auto_remove": False,
        "mem_limit": "2g",
        "nano_cpus": 1_000_000_000,
        "pids_limit": 256,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    # Network name is project-prefixed by docker-compose
    # (e.g. agent-temporal_sandbox-net). Read it from env, fall back to
    # default daemon bridge if unset.
    sandbox_network = _os.environ.get("SANDBOX_NETWORK_NAME")
    if sandbox_network:
        run_kwargs["network"] = sandbox_network
    # Force outbound traffic through the egress-proxy on sandbox-net.
    # The proxy enforces an FQDN allow-list. Setting HTTP_PROXY +
    # HTTPS_PROXY covers git, curl/pip via libcurl, and httpx by default.
    egress_proxy = _os.environ.get("SANDBOX_EGRESS_PROXY_URL")
    if egress_proxy:
        run_kwargs["environment"] = {
            "HTTP_PROXY": egress_proxy,
            "HTTPS_PROXY": egress_proxy,
            "http_proxy": egress_proxy,
            "https_proxy": egress_proxy,
            "NO_PROXY": "localhost,127.0.0.1,egress-proxy",
            "no_proxy": "localhost,127.0.0.1,egress-proxy",
        }
    if worker_hostname:
        run_kwargs["volumes_from"] = [worker_hostname]
    container = client.containers.run(SANDBOX_IMAGE, **run_kwargs)
    return SandboxHandle(container_id=container.id, workdir=host_workdir)


def _exec_in_sandbox_impl(handle: SandboxHandle, cmd: list[str]) -> ExecResult:
    client = docker.from_env()
    container = client.containers.get(handle.container_id)
    rc, output = container.exec_run(cmd, workdir=handle.workdir, demux=True)
    stdout_b, stderr_b = output if isinstance(output, tuple) else (output, b"")
    return ExecResult(
        exit_code=rc,
        stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
        stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
    )


def _pause_sandbox_impl(handle: SandboxHandle) -> None:
    client = docker.from_env()
    client.containers.get(handle.container_id).pause()


def _resume_sandbox_impl(handle: SandboxHandle) -> None:
    client = docker.from_env()
    client.containers.get(handle.container_id).unpause()


def _teardown_sandbox_impl(handle: SandboxHandle) -> None:
    client = docker.from_env()
    try:
        container = client.containers.get(handle.container_id)
    except NotFound:
        return
    try:
        container.stop(timeout=5)
    except NotFound:
        return
    try:
        container.remove(force=True)
    except NotFound:
        return


# ---------- Temporal activity wrappers ----------

@activity.defn
def provision_sandbox(pr: PRRef) -> SandboxHandle:
    """Start a per-workflow sandbox container.

    Assumes prepare_workdir has already cloned the repo into
    /tmp/autofix-{workflow_id}/repo on the worker. The sandbox inherits
    that path via volumes_from.

    `pr` is accepted for activity signature stability and future use
    (e.g. tagging) but is not currently read inside the impl.
    """
    workflow_id = activity.info().workflow_id
    host_workdir = f"/tmp/autofix-{workflow_id}/repo"
    return _provision_sandbox_impl(
        workflow_id=workflow_id,
        host_workdir=host_workdir,
    )


@activity.defn
def exec_in_sandbox(handle: SandboxHandle, cmd: list[str]) -> ExecResult:
    return _exec_in_sandbox_impl(handle, cmd)


@activity.defn
def pause_sandbox(handle: SandboxHandle) -> None:
    _pause_sandbox_impl(handle)


@activity.defn
def resume_sandbox(handle: SandboxHandle) -> None:
    _resume_sandbox_impl(handle)


@activity.defn
def teardown_sandbox(handle: SandboxHandle) -> None:
    _teardown_sandbox_impl(handle)
