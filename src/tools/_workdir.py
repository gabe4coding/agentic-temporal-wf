"""Workdir path resolution helpers for the autofix toolset.

The per-workflow workdir is `/tmp/autofix-{workflow_id}/repo`. SDK MCP
tools receive a dict of args (no RunContext equivalent), so the workflow
activity communicates the workdir id via a ContextVar that is isolated
per-asyncio-task — safe across concurrent activities on the same worker.

The env-var `AUTOFIX_WORKDIR_ID` is kept as a fallback so unit tests that
monkeypatch the env continue to work. In production the ContextVar wins.
"""
from __future__ import annotations

import contextvars
import os
from pathlib import Path

from src.models import SandboxHandle


_workdir_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "autofix_workdir_id"
)
_sandbox_handle_var: contextvars.ContextVar[SandboxHandle] = contextvars.ContextVar(
    "autofix_sandbox_handle"
)


def workdir_root(workdir_id: str) -> Path:
    """Resolve the per-workflow workdir root."""
    root = Path(os.environ.get("WORKSPACE_ROOT", "/tmp"))
    return root / f"autofix-{workdir_id}" / "repo"


def safe_join(workdir: Path, relative: str) -> Path:
    """Join a path inside workdir, rejecting traversal."""
    workdir = workdir.resolve()
    candidate = (workdir / relative).resolve()
    if not str(candidate).startswith(str(workdir) + "/") and candidate != workdir:
        raise ValueError(f"path {relative!r} resolves outside workdir")
    return candidate


def set_workdir_id(workdir_id: str) -> contextvars.Token:
    """Bind the workdir id for the current asyncio task. Returns a token
    that must be passed back to reset_workdir_id() (typically in a
    finally block)."""
    return _workdir_id_var.set(workdir_id)


def reset_workdir_id(token: contextvars.Token) -> None:
    """Pop the workdir id binding."""
    _workdir_id_var.reset(token)


def workdir_root_from_env() -> Path:
    """Resolve workdir from the ContextVar first; fall back to the env var.

    The ContextVar is set by the Temporal activity (run_agent_iteration)
    via set_workdir_id() and isolated per-asyncio-task. The env-var
    fallback exists so tests can monkeypatch.setenv("AUTOFIX_WORKDIR_ID",
    ...) and still resolve.
    """
    sandbox_workdir = os.environ.get("AUTOFIX_WORKDIR")
    if sandbox_workdir:
        return Path(sandbox_workdir)
    try:
        wid = _workdir_id_var.get()
    except LookupError:
        wid = os.environ.get("AUTOFIX_WORKDIR_ID")
    if not wid:
        raise RuntimeError("AUTOFIX_WORKDIR_ID is not set (neither context nor env)")
    return workdir_root(wid)


def set_sandbox_handle(handle: SandboxHandle) -> contextvars.Token:
    """Bind the per-workflow sandbox handle to the current asyncio task.

    Returns a token that must be passed back to reset_sandbox_handle()
    (typically in a finally block).
    """
    return _sandbox_handle_var.set(handle)


def reset_sandbox_handle(token: contextvars.Token) -> None:
    _sandbox_handle_var.reset(token)


def get_sandbox_handle() -> SandboxHandle | None:
    """Return the bound sandbox handle, or None if not set.

    Returning None rather than raising lets the tool layer keep the
    legacy host-filesystem path when no sandbox is in scope (e.g. in
    unit tests that exercise _local_repo_impl directly).
    """
    try:
        return _sandbox_handle_var.get()
    except LookupError:
        return None
