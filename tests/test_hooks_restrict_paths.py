"""Tests for .claude/hooks/restrict_paths.py.

The hook is a standalone script that Claude Code invokes as a
PreToolUse subprocess: it reads a JSON payload from stdin and writes a
JSON decision to stdout. We invoke it the same way (subprocess + stdin)
so the tests cover the exact integration the CLI does.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


HOOK = Path(__file__).parent.parent / ".claude" / "hooks" / "restrict_paths.py"


def run_hook(payload: dict) -> tuple[int, dict]:
    """Invoke the hook with `payload` on stdin; return (exit_code, parsed_stdout).

    Parsed stdout is {} when the hook writes nothing (the allow path).
    """
    proc = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    parsed: dict = {}
    if proc.stdout.strip():
        parsed = json.loads(proc.stdout)
    return proc.returncode, parsed


def _decision(parsed: dict) -> str | None:
    """Extract the permission decision from the hook output, supporting
    both the legacy `decision` field and the newer `hookSpecificOutput`."""
    if "hookSpecificOutput" in parsed:
        return parsed["hookSpecificOutput"].get("permissionDecision")
    return parsed.get("decision")


# ---------- allow path inside per-workflow workdir ----------

@pytest.mark.parametrize("tool,arg_key", [
    ("Read", "file_path"),
    ("Edit", "file_path"),
    ("MultiEdit", "file_path"),
    ("Write", "file_path"),
])
def test_allows_tools_targeting_workflow_workdir(tool: str, arg_key: str):
    rc, out = run_hook({
        "tool_name": tool,
        "tool_input": {arg_key: "/tmp/autofix-wf-1/repo/src/foo.py"},
    })
    assert rc == 0
    # An allow is either an explicit allow JSON or empty stdout.
    assert _decision(out) in (None, "allow")


def test_allows_grep_inside_workdir():
    rc, out = run_hook({
        "tool_name": "Grep",
        "tool_input": {
            "path": "/tmp/autofix-wf-1/repo",
            "pattern": "TODO",
        },
    })
    assert rc == 0
    assert _decision(out) in (None, "allow")


# ---------- deny path outside any workdir ----------

@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/app/.env",
    "/root/.ssh/id_rsa",
    "/tmp/notes.txt",                     # /tmp but not autofix workdir
    "/var/run/docker.sock",
])
def test_denies_paths_outside_workdir(path: str):
    rc, out = run_hook({
        "tool_name": "Read",
        "tool_input": {"file_path": path},
    })
    # The hook may exit 0 with a deny JSON or exit non-zero — either
    # signals deny to the CLI. We assert at the decision layer.
    assert _decision(out) == "deny", (rc, out)


def test_denies_path_traversal_attempt():
    """A path that looks workdir-scoped but resolves outside must be denied."""
    rc, out = run_hook({
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "/tmp/autofix-wf-1/repo/../../../etc/passwd",
        },
    })
    assert _decision(out) == "deny", (rc, out)


# ---------- deny within workdir for protected subtrees ----------

@pytest.mark.parametrize("path", [
    "/tmp/autofix-wf-1/repo/.git/hooks/pre-commit",
    "/tmp/autofix-wf-1/repo/.github/workflows/release.yml",
    "/tmp/autofix-wf-1/repo/.claude/settings.json",
])
def test_denies_protected_subtrees_even_inside_workdir(path: str):
    rc, out = run_hook({
        "tool_name": "Edit",
        "tool_input": {"file_path": path},
    })
    assert _decision(out) == "deny", (rc, out)


# ---------- always-denied tools ----------

@pytest.mark.parametrize("tool", ["Bash", "WebFetch"])
def test_denies_disabled_tools_regardless_of_args(tool: str):
    rc, out = run_hook({
        "tool_name": tool,
        "tool_input": {"command": "echo hi"} if tool == "Bash" else {"url": "https://x"},
    })
    assert _decision(out) == "deny", (rc, out)


# ---------- robustness ----------

def test_unknown_tool_is_allowed_silently():
    """The hook must not block tools it doesn't know — that would
    accidentally deny mcp__* tools which are governed at the SDK layer."""
    rc, out = run_hook({
        "tool_name": "mcp__repo__run_ruff",
        "tool_input": {},
    })
    assert rc == 0
    assert _decision(out) in (None, "allow")


def test_malformed_input_does_not_crash():
    """Defensive: if the payload is malformed, deny rather than allow."""
    proc = subprocess.run(
        ["python3", str(HOOK)],
        input="not-json-at-all",
        capture_output=True,
        text=True,
        check=False,
    )
    # Either a deny JSON or non-zero exit — both signal block to the CLI.
    parsed: dict = {}
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = {}
    blocked = (proc.returncode != 0) or (_decision(parsed) == "deny")
    assert blocked, (proc.returncode, proc.stdout, proc.stderr)
