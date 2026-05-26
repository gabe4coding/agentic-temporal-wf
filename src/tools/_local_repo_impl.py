import hashlib
import json
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from src.models import SandboxHandle
from src.tools._workdir import safe_join


# ---------- exec dispatch ----------
#
# The 4 command-execution functions below (run_ruff, run_pytest,
# git_status, git_commit_and_push) accept either a `Path` (legacy host
# filesystem path, kept for the existing unit tests) or a
# `SandboxHandle` (per-workflow Docker sandbox). The dispatch is by
# isinstance — explicit and easy to follow at the call site.
#
# File-based helpers (read_file, list_files, apply_edit) keep operating
# on the host filesystem. The hybrid bind-mount design (volumes_from)
# ensures the same path is visible inside the sandbox, so an edit done
# on the host is picked up by the next exec_in_sandbox.

Target = Path | SandboxHandle


def _exec_at(target: Target, cmd: list[str]) -> tuple[int, str, str]:
    """Run `cmd` against `target`; returns (exit_code, stdout, stderr)."""
    if isinstance(target, SandboxHandle):
        from src.activities.sandbox import _exec_in_sandbox_impl

        res = _exec_in_sandbox_impl(target, cmd)
        return res.exit_code, res.stdout, res.stderr
    proc = subprocess.run(
        cmd, cwd=target, capture_output=True, text=True, check=False
    )
    return proc.returncode, proc.stdout, proc.stderr


def _workdir_path(target: Target) -> Path:
    return Path(target.workdir) if isinstance(target, SandboxHandle) else target


def read_file(workdir: Path, path: str) -> str:
    return safe_join(workdir, path).read_text()


def list_files(workdir: Path, glob: str = "**/*.py") -> list[str]:
    workdir = workdir.resolve()
    return sorted(
        str(p.relative_to(workdir))
        for p in workdir.glob(glob)
        if p.is_file() and ".git" not in p.parts
    )


def apply_edit(workdir: Path, path: str, new_content: str) -> str:
    target = safe_join(workdir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content)
    return hashlib.sha1(new_content.encode()).hexdigest()


class RuffViolation(BaseModel):
    filename: str
    code: str
    message: str
    line: int


class RuffResult(BaseModel):
    exit_code: int
    violations: list[RuffViolation]
    raw_stderr: str = ""


class PytestResult(BaseModel):
    exit_code: int
    passed: int
    failed: int
    errors: int
    summary: str


def run_ruff(workdir: Target) -> RuffResult:
    rc, stdout, stderr = _exec_at(
        workdir, ["ruff", "check", ".", "--output-format=json"]
    )
    if stdout.strip():
        raw = json.loads(stdout)
        violations = [
            RuffViolation(
                filename=item["filename"],
                code=item["code"],
                message=item["message"],
                line=item["location"]["row"],
            )
            for item in raw
        ]
    else:
        violations = []
    return RuffResult(exit_code=rc, violations=violations, raw_stderr=stderr)


def run_pytest(workdir: Target, target: str | None = None) -> PytestResult:
    cmd = ["pytest", "-q", "--no-header"]
    if target:
        cmd.append(target)
    rc, stdout, _stderr = _exec_at(workdir, cmd)
    passed = _count_token(stdout, "passed")
    failed = _count_token(stdout, "failed")
    errors = _count_token(stdout, "error")
    summary = (stdout.splitlines() or [""])[-1].strip()
    return PytestResult(
        exit_code=rc,
        passed=passed,
        failed=failed,
        errors=errors,
        summary=summary,
    )


def _count_token(out: str, token: str) -> int:
    """Parse pytest's terminal summary tokens like '3 passed', '1 failed', '2 errors'."""
    m = re.search(rf"(\d+)\s+{token}s?", out)
    return int(m.group(1)) if m else 0


class GitStatus(BaseModel):
    branch: str
    dirty: bool
    ahead: int = 0
    behind: int = 0


class CommitResult(BaseModel):
    pushed: bool
    commit_sha: str | None = None
    reason: str | None = None  # "no_changes" | "remote_advanced" | other


# Trailer appended to every autofix commit so the gateway can recognize
# the push and skip the self-triggered pull_request.synchronize event.
AUTOFIX_COMMIT_TRAILER = "[autofix-bot]"


def _git(workdir: Target, *args: str) -> tuple[int, str, str]:
    """Run a git command against workdir; returns (rc, stdout, stderr)."""
    return _exec_at(workdir, ["git", *args])


def git_status(workdir: Target) -> GitStatus:
    _, branch_out, _ = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_out.strip()
    _, porcelain, _ = _git(workdir, "status", "--porcelain")
    dirty = bool(porcelain.strip())
    return GitStatus(branch=branch, dirty=dirty)


def git_commit_and_push(
    workdir: Target,
    message: str,
    *,
    idempotency_key: str | None = None,
) -> CommitResult:
    """Stage all, commit, push. If `idempotency_key` is given, it is
    appended as an `Autofix-Idempotency:` trailer so retries are
    detectable from git history (Pattern-C rule 6)."""
    _, branch_out, _ = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_out.strip()

    add_rc, _, add_err = _git(workdir, "add", "-A")
    if add_rc != 0:
        return CommitResult(pushed=False, reason=add_err.strip())

    diff_rc, _, _ = _git(workdir, "diff", "--cached", "--quiet")
    if diff_rc == 0:
        return CommitResult(pushed=False, reason="no_changes")

    # Always tag autofix commits with a stable trailer so the gateway can
    # recognize and drop the resulting pull_request.synchronize event.
    full_message = (
        message
        if AUTOFIX_COMMIT_TRAILER in message
        else f"{message}\n\n{AUTOFIX_COMMIT_TRAILER}"
    )
    if idempotency_key:
        full_message += f"\nAutofix-Idempotency: {idempotency_key}"
    commit_rc, _, commit_err = _git(workdir, "commit", "-m", full_message)
    if commit_rc != 0:
        return CommitResult(pushed=False, reason=commit_err.strip())
    _, sha_out, _ = _git(workdir, "rev-parse", "HEAD")
    sha = sha_out.strip()

    fetch_rc, _, fetch_err = _git(workdir, "fetch", "origin", branch)
    if fetch_rc != 0:
        return CommitResult(pushed=False, commit_sha=sha, reason=fetch_err.strip())

    _, behind_out, _ = _git(
        workdir, "rev-list", "--count", f"HEAD..origin/{branch}"
    )
    behind = behind_out.strip()
    if behind and int(behind) > 0:
        return CommitResult(pushed=False, commit_sha=sha, reason="remote_advanced")

    push_rc, _, push_err = _git(workdir, "push", "origin", branch)
    if push_rc != 0:
        return CommitResult(pushed=False, commit_sha=sha, reason=push_err.strip())

    return CommitResult(pushed=True, commit_sha=sha)
