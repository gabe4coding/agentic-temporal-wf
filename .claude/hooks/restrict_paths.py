#!/usr/bin/env python3
"""Claude Code PreToolUse hook: enforce path scoping for the autofix agent.

Configured in `.claude/settings.json` with a matcher of the affected
builtin tools (Read, Edit, MultiEdit, Write, Grep, Glob) and the
always-denied ones (Bash, WebFetch). For each tool call the CLI invokes
this script with a JSON payload on stdin; we write a JSON decision on
stdout.

Policy:
- `Bash` and `WebFetch` are denied unconditionally (the agent must go
  through the MCP tools that are sandbox-aware).
- For path-bearing tools, the target must resolve inside
  `/tmp/autofix-<workflow_id>/repo/` and must NOT fall inside
  `.git/hooks/`, `.github/workflows/`, or `.claude/` — those subtrees
  would let a compromised agent escalate beyond the workdir.
- Unknown tools (e.g. `mcp__*`) are allowed silently: they are governed
  by the SDK `allowed_tools` allow-list, not by this hook.

Output format follows the Claude Code hook spec:
    {
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" | "deny",
        "permissionDecisionReason": "<text>"
      }
    }

A bare `exit 0` is also a valid "allow" signal; we use it for the
fast-path to keep the per-call overhead minimal.

Defensive default: on any internal error (malformed JSON, unexpected
exception), deny rather than allow. The CLI considers a non-zero exit
or a `deny` JSON as a block.
"""
from __future__ import annotations

import json
import os
import re
import sys


# ---- Policy constants ----

_ALLOWED_PREFIX_RE = re.compile(r"^/tmp/autofix-[^/]+/repo(?:/|$)")

# Paths under the workdir that must remain off-limits even though the
# workdir itself is in-scope. Each pattern is matched against the
# workdir-relative path (e.g. ".git/hooks/foo" or ".github/workflows/x.yml").
_DENIED_WITHIN_RE = [
    re.compile(r"^\.git/hooks(?:/|$)"),
    re.compile(r"^\.github/workflows(?:/|$)"),
    re.compile(r"^\.claude(?:/|$)"),
]

# Tools whose primary argument is a filesystem path, and the key that
# carries it in the tool_input payload.
_PATH_TOOLS = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "Write": ("file_path",),
    "Grep": ("path",),
    "Glob": ("path",),
}

# Tools we never want the agent to invoke directly.
_DENIED_TOOLS = {"Bash", "WebFetch"}


# ---- Decision helpers ----

def _allow() -> None:
    # Fast-path: empty stdout, exit 0 → CLI allows.
    sys.exit(0)


def _deny(reason: str) -> None:
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        # Legacy field, for older CLI versions that look for `decision`.
        "decision": "block",
        "reason": reason,
    }))
    sys.exit(0)


def _check_path(p: str) -> str | None:
    """Return None when the path is allowed, otherwise a deny reason."""
    if not p:
        return "empty path"
    # Resolve to absolute and normalize traversal attempts.
    abs_p = os.path.normpath(os.path.abspath(p))
    m = _ALLOWED_PREFIX_RE.match(abs_p)
    if not m:
        return (
            f"path {p!r} resolves to {abs_p!r}, outside the per-workflow "
            "sandbox workdir (/tmp/autofix-*/repo/)"
        )
    # Anything matched: check the within-workdir denylist.
    workdir_relative = abs_p[m.end():].lstrip("/")
    for deny_re in _DENIED_WITHIN_RE:
        if deny_re.match(workdir_relative):
            return (
                f"path {p!r} targets a protected subtree "
                f"({deny_re.pattern}) inside the workdir"
            )
    return None


# ---- Entry point ----

def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        _deny(f"malformed hook payload: {e}")
        return

    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}

    if tool in _DENIED_TOOLS:
        _deny(f"tool {tool!r} is disabled by policy")
        return

    spec = _PATH_TOOLS.get(tool)
    if spec is None:
        # Unknown / MCP tool: out of this hook's scope.
        _allow()
        return

    for key in spec:
        if key in args:
            err = _check_path(args[key])
            if err:
                _deny(err)
                return

    _allow()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # last-resort defensive deny
        _deny(f"hook internal error: {type(e).__name__}: {e}")
