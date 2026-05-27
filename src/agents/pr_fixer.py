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

1. Use the `capability` MCP toolset to fetch full context (PR diff, comment \
   bodies, check run details).
2. Decide whether the event is a valid, actionable engineering request.
3. If yes, use the `repo` MCP toolset OR the builtin Read/Edit/Grep/Glob \
   tools to inspect the code, apply the smallest possible edit, and \
   verify locally with `mcp__repo__run_ruff` and `mcp__repo__run_pytest`.
4. Only call `mcp__capability__request_push_changes` if local verification \
   passes. Publication is performed by the trusted workflow after human \
   approval; no sandbox tool may commit or push directly.
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


# Network destinations visible to the SDK sandbox. Upstream APIs are reached
# only through the trusted capability broker.
_ALLOWED_DOMAINS = [
    "capability-broker",
    "pypi.org",
    "files.pythonhosted.org",
]


def build_options() -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions for one agent iteration.

    The Claude subprocess sends its opaque run token to the trusted model
    relay in the API-key slot; that value is not an Anthropic credential.
    """
    egress_proxy = os.environ.get(
        "SANDBOX_EGRESS_PROXY_URL", "http://egress-proxy:8888"
    )
    # Claude Code CLI native OpenTelemetry. The Python SDK runs the CLI
    # as a subprocess that has OTel instrumentation built in but
    # disabled by default. Enable it here and route to Phoenix (or any
    # OTLP collector). Endpoint comes from $OTEL_EXPORTER_OTLP_ENDPOINT
    # which the worker injects into the sandbox env via
    # provision_sandbox.
    # Reference: https://code.claude.com/docs/en/agent-sdk/observability
    cli_otel_env: dict[str, str] = {}
    cli_otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if cli_otlp_endpoint:
        cli_otel_env = {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_ENDPOINT": cli_otlp_endpoint,
            "OTEL_SERVICE_NAME": "agent-temporal-autofix",
            # Default flush is 5s; lower for short-lived agent calls
            # so spans land before the subprocess exits.
            "OTEL_TRACES_EXPORT_INTERVAL": "1000",
            "OTEL_METRIC_EXPORT_INTERVAL": "1000",
            "OTEL_LOGS_EXPORT_INTERVAL": "1000",
            # Content opt-ins (off by default upstream for privacy).
            # Per the Anthropic observability doc:
            #   OTEL_LOG_USER_PROMPTS=1  → prompt text on user_prompt
            #     events and on the claude_code.interaction span
            #   OTEL_LOG_TOOL_DETAILS=1  → tool input arguments
            #     (file paths, shell commands, search patterns)
            #   OTEL_LOG_TOOL_CONTENT=1  → full tool input/output bodies
            #     (truncated at 60 KB). Requires traces to be enabled.
            "OTEL_LOG_USER_PROMPTS": "1",
            "OTEL_LOG_TOOL_DETAILS": "1",
            "OTEL_LOG_TOOL_CONTENT": "1",
        }
    run_token = os.environ["RUN_CAPABILITY_TOKEN"]
    return ClaudeAgentOptions(
        system_prompt=INSTRUCTIONS,
        mcp_servers={
            "capability": build_github_mcp_config(),
            "repo": local_repo_mcp_server,
        },
        allowed_tools=[
            "Read",
            "Edit",
            "Grep",
            "Glob",
            "mcp__capability__*",
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
            "ANTHROPIC_API_KEY": run_token,
            "ANTHROPIC_BASE_URL": os.environ.get(
                "ANTHROPIC_BASE_URL", "http://capability-broker:8443/anthropic"
            ),
            "HTTPS_PROXY": egress_proxy,
            "HTTP_PROXY": egress_proxy,
            **cli_otel_env,
        },
    )
