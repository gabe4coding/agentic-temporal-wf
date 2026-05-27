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

Security posture:
    - cap_drop ALL, no-new-privileges, pids/cpu/mem limits
    - exactly one writable bind mount: the individual run workspace at /work
    - an opaque, run-scoped capability token; never upstream credentials
    - egress limited to the internal broker/proxy network

Out of scope for v1: auto-pause-on-idle wiring, snapshot/fork. The
pause/resume activities are provided so the workflow can drive them;
the timer is the workflow's job.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import secrets
from typing import Any

import docker
import httpx
from docker.errors import NotFound
from temporalio import activity

from src.models import CapabilityBinding, PRRef, SandboxHandle, ExecResult
from src.tools._workdir import workdir_root


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
    *,
    workflow_id: str,
    host_workdir: str,
    pr: PRRef | None = None,
    capability_token: str | None = None,
) -> SandboxHandle:
    """Start a per-workflow sandbox container.

    The container sees only this run's prepared repository as `/work`.
    Broker registration is performed before start, so the container
    receives only the opaque capability token required to use the model
    relay and scoped MCP service.

    Args:
        workflow_id: used to derive the container name (stable across retries).
        host_workdir: trusted worker path of the prepared workdir.
    """
    client = docker.from_env()
    token = capability_token or secrets.token_urlsafe(32)
    broker_url = os.environ.get("CAPABILITY_BROKER_URL", "http://capability-broker:8443")
    if pr is not None:
        registration_secret = os.environ.get("BROKER_REGISTRATION_SECRET", "")
        if not registration_secret:
            raise RuntimeError("BROKER_REGISTRATION_SECRET must be configured")
        binding = CapabilityBinding(
            workflow_id=workflow_id,
            repository=f"{pr.owner}/{pr.repo}",
            pr_number=pr.number,
            workspace_path=host_workdir,
            capabilities={"model.invoke", "source.read", "changes.publish"},
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        headers = {"X-Broker-Registration-Secret": registration_secret}
        response = httpx.post(
            f"{broker_url}/bindings",
            headers=headers,
            json={"token": token, "binding": binding.model_dump(mode="json")},
            timeout=5.0,
        )
        response.raise_for_status()
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
        "volumes": {host_workdir: {"bind": SANDBOX_WORKDIR, "mode": "rw"}},
    }
    # Network name is project-prefixed by docker-compose
    # (e.g. agent-temporal_sandbox-net). Read it from env, fall back to
    # default daemon bridge if unset.
    sandbox_network = os.environ.get("SANDBOX_NETWORK_NAME")
    if sandbox_network:
        run_kwargs["network"] = sandbox_network
    egress_proxy = os.environ.get("SANDBOX_EGRESS_PROXY_URL")
    mcp_url = os.environ.get("CAPABILITY_MCP_URL", f"{broker_url}/mcp")
    relay_url = os.environ.get("ANTHROPIC_RELAY_URL", f"{broker_url}/anthropic")
    env_for_sandbox: dict[str, str] = {
        "RUN_CAPABILITY_TOKEN": token,
        "ANTHROPIC_BASE_URL": relay_url,
        "CAPABILITY_MCP_URL": mcp_url,
        "AUTOFIX_WORKDIR": SANDBOX_WORKDIR,
        "AUTOFIX_WORKDIR_ID": workflow_id,
    }
    # Local telemetry routes may be forwarded; cloud observability
    # credentials remain trusted-service-only.
    for var in (
        "PHOENIX_COLLECTOR_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "ARIZE_PROJECT",
    ):
        val = os.environ.get(var)
        if val:
            env_for_sandbox[var] = val
    if egress_proxy:
        env_for_sandbox.update(
            {
                "HTTP_PROXY": egress_proxy,
                "HTTPS_PROXY": egress_proxy,
                "http_proxy": egress_proxy,
                "https_proxy": egress_proxy,
                # Broker calls and local telemetry routes stay inside the
                # sandbox network rather than traversing the egress proxy.
                "NO_PROXY": "localhost,127.0.0.1,egress-proxy,capability-broker,phoenix,otel-collector",
                "no_proxy": "localhost,127.0.0.1,egress-proxy,capability-broker,phoenix,otel-collector",
            }
        )
    run_kwargs["environment"] = env_for_sandbox
    container = client.containers.run(SANDBOX_IMAGE, **run_kwargs)
    return SandboxHandle(container_id=container.id, workdir=SANDBOX_WORKDIR)


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

    Assumes prepare_workdir has already cloned the repo into the worker's
    dedicated workspace root. Registers the broker capability immediately
    before mounting the individual repository into the sandbox.
    """
    workflow_id = activity.info().workflow_id
    host_workdir = str(workdir_root(workflow_id))
    return _provision_sandbox_impl(
        workflow_id=workflow_id,
        host_workdir=host_workdir,
        pr=pr,
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
