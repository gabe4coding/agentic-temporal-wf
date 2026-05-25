"""Claude Agent SDK options builder for the PR autofix agent.

This module exports the system prompt (INSTRUCTIONS) and a factory
build_options() that produces ClaudeAgentOptions ready to pass to
claude_agent_sdk.query(). The actual loop is in
src/activities/agent_iteration.py.
"""
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


def build_options() -> ClaudeAgentOptions:
    """Construct ClaudeAgentOptions for one agent iteration.

    Reads GITHUB_TOKEN and AUTOFIX_WORKDIR_ID from the process env. Both
    must be set by the activity before calling this.
    """
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
        permission_mode="bypassPermissions",
        env={"CLAUDE_CODE_MAX_RETRIES": "0"},
    )
