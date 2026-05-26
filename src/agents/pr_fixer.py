"""Claude Agent SDK options builder for the PR autofix agent.

Pattern-C target:
- SDK-native options.sandbox replaces IS_SANDBOX=1 + bypassPermissions.
- permission_mode=default (combined with disallowed_tools + plugins/hooks).
- plugins[] loaded from /plugins/tf-guardrails and /plugins/tf-mitigations.
- can_use_tool wired to the in-sandbox fast-path guard; the durable HITL
  gate lives on the credential proxy outside the sandbox (rule 7).

Note on types: `SandboxSettings` and `SandboxNetworkConfig` are TypedDicts
defined in claude_agent_sdk.types with camelCase keys (per upstream
src/claude_agent_sdk/types.py). The cleanest call shape is plain dicts.
"""
from __future__ import annotations

import os

from claude_agent_sdk import ClaudeAgentOptions

from src.tools.github_mcp import build_github_mcp_config
from src.tools.local_repo import local_repo_mcp_server


INSTRUCTIONS = """\
You are an autonomous code-review assistant working on one GitHub Pull Request.

You receive: a short brief listing pending events (new review comments, CI \
results) and the PR identifier. For each event:

1. Use the `github` MCP toolset to fetch full context (PR diff, comment \
   bodies, check run details).
2. Decide whether the event is a valid, actionable engineering request.
3. If yes, use the `repo` MCP toolset OR the builtin Read/Edit/Grep/Glob \
   tools to inspect the code, apply the smallest possible edit, and \
   verify locally with `mcp__repo__run_ruff` and `mcp__repo__run_pytest`.
4. Only call `mcp__repo__git_commit_and_push` if local verification \
   passes. If the push is refused (`remote_advanced` etc.), do NOT retry \
   blindly; report it as `blocking_reason`.
5. If a comment is opinion-only, unclear, or out of scope, do not apply \
   it. Explain in `summary` why you skipped it.

At the very end of your final message, you MUST emit a JSON object on its \
own line (the last non-empty line) with exactly these keys:

```
{"action": "applied_fix" | "no_action_needed" | "blocked",
 "summary": "1-3 sentences",
 "addressed_comment_ids": [<int>, ...],
 "addressed_failures": ["ruff", "pytest::test_x", ...],
 "commit_sha": "<sha or null>",
 "blocking_reason": "<text or null>"}
```

Do NOT wrap that JSON in fenced code blocks. The orchestrator parses the \
last JSON-shaped line of your output.
"""


# Domains the agent is allowed to reach. The credential proxy enforces
# this too (defense in depth — the SDK-native block is L1/L2, the proxy
# is L0).
_ALLOWED_DOMAINS = [
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
    "api.anthropic.com",
    "pypi.org",
    "files.pythonhosted.org",
]


def build_options() -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions for one agent iteration."""
    proxy_url = os.environ.get(
        "CREDENTIAL_PROXY_URL", "http://credential-proxy:8443"
    )
    return ClaudeAgentOptions(
        system_prompt=INSTRUCTIONS,
        mcp_servers={
            "github": build_github_mcp_config(),
            "repo": local_repo_mcp_server,
        },
        allowed_tools=[
            "Read",
            "Edit",
            "Grep",
            "Glob",
            "mcp__github__*",
            "mcp__repo__*",
        ],
        disallowed_tools=["Bash", "Write", "WebFetch"],
        permission_mode="default",
        # SandboxSettings is a TypedDict with camelCase keys — see
        # claude_agent_sdk.types in upstream.
        sandbox={
            "enabled": True,
            "autoAllowBashIfSandboxed": False,
            "excludedCommands": ["docker", "kubectl", "ssh"],
            "network": {
                "allowedDomains": _ALLOWED_DOMAINS,
                "allowLocalBinding": True,
            },
        },
        plugins=[
            {"type": "local", "path": "/plugins/tf-guardrails"},
            {"type": "local", "path": "/plugins/tf-mitigations"},
        ],
        env={
            "CLAUDE_CODE_MAX_RETRIES": "0",
            "HTTPS_PROXY": proxy_url,
            "HTTP_PROXY": proxy_url,
        },
    )
