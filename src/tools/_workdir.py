import os
from pathlib import Path


def workdir_root(workdir_id: str) -> Path:
    """Resolve the per-workflow workdir root."""
    return Path("/tmp") / f"autofix-{workdir_id}" / "repo"


def safe_join(workdir: Path, relative: str) -> Path:
    """Join a path inside workdir, rejecting traversal."""
    workdir = workdir.resolve()
    candidate = (workdir / relative).resolve()
    if not str(candidate).startswith(str(workdir) + "/") and candidate != workdir:
        raise ValueError(f"path {relative!r} resolves outside workdir")
    return candidate


def workdir_root_from_env() -> Path:
    """Resolve workdir using the AUTOFIX_WORKDIR_ID env var.

    Set by the Temporal activity (run_agent_iteration) so SDK MCP tools,
    which receive a plain dict of args and have no RunContext equivalent,
    can still locate the per-workflow workdir.
    """
    wid = os.environ.get("AUTOFIX_WORKDIR_ID")
    if not wid:
        raise RuntimeError("AUTOFIX_WORKDIR_ID env var is not set")
    return workdir_root(wid)
