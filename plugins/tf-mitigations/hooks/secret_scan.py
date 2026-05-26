#!/usr/bin/env python3
"""PreToolUse hook: refuse Edit/Write whose payload contains a secret.

Patterns are conservative — defense in depth. The full secret-scanning
story lives in the egress proxy + Vault, but blocking at the pre-tool
boundary catches the simplest exfiltration attempts."""
from __future__ import annotations
import json, re, sys

PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                       # AWS access key
    re.compile(r"-----BEGIN (?:RSA|OPENSSH) PRIVATE KEY"),  # ssh/openssl
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                    # GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),            # GitHub fg PAT
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),               # Anthropic key
]

def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("tool_input") or {}
    candidates = []
    for key in ("new_string", "content", "command", "input"):
        v = args.get(key)
        if isinstance(v, str):
            candidates.append(v)
    blob = "\n".join(candidates)
    for pat in PATTERNS:
        if pat.search(blob):
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"payload matched secret pattern {pat.pattern!r}",
                },
                "decision": "block",
                "reason": "secret pattern match",
            }))
            sys.exit(0)
    sys.exit(0)

if __name__ == "__main__":
    main()
