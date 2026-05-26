#!/usr/bin/env python3
"""PreToolUse hook on git_commit_and_push: refuse pushes without the
TheFork autofix commit trailer. Operators can identify and roll back
agent-authored commits via this stable trailer."""
from __future__ import annotations
import json, sys

TRAILER = "[autofix-bot]"
GATED_TOOLS = {"mcp__repo__git_commit_and_push"}

def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    tool = payload.get("tool_name") or ""
    if tool not in GATED_TOOLS:
        sys.exit(0)
    msg = (payload.get("tool_input") or {}).get("message") or ""
    if TRAILER not in msg:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"commit message missing {TRAILER}",
            },
            "decision": "block",
            "reason": "missing autofix trailer",
        }))
        sys.exit(0)
    sys.exit(0)

if __name__ == "__main__":
    main()
