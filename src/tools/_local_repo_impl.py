import hashlib
import json
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from src.tools._workdir import safe_join


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


def run_ruff(workdir: Path) -> RuffResult:
    proc = subprocess.run(
        ["ruff", "check", ".", "--output-format=json"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if proc.stdout.strip():
        raw = json.loads(proc.stdout)
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
    return RuffResult(exit_code=proc.returncode, violations=violations, raw_stderr=proc.stderr)


def run_pytest(workdir: Path, target: str | None = None) -> PytestResult:
    cmd = ["pytest", "-q", "--no-header"]
    if target:
        cmd.append(target)
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    out = proc.stdout
    passed = _count_token(out, "passed")
    failed = _count_token(out, "failed")
    errors = _count_token(out, "error")
    summary = (out.splitlines() or [""])[-1].strip()
    return PytestResult(
        exit_code=proc.returncode,
        passed=passed,
        failed=failed,
        errors=errors,
        summary=summary,
    )


def _count_token(out: str, token: str) -> int:
    """Parse pytest's terminal summary tokens like '3 passed', '1 failed', '2 errors'."""
    m = re.search(rf"(\d+)\s+{token}s?", out)
    return int(m.group(1)) if m else 0
