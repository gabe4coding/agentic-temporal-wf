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
