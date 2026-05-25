from pydantic_ai import Agent
from pydantic_ai.durable_exec.temporal import TemporalAgent

from src.models import AgentDeps, FixPlan
from src.tools.github_mcp import build_github_mcp_server
from src.tools.local_repo import local_repo_toolset


INSTRUCTIONS = """\
You are an autonomous code-review assistant working on one GitHub Pull Request.

You receive: a short brief listing pending events (new review comments, CI \
results) and the PR identifier. For each event:

1. Use the `github` toolset to fetch full context (PR diff, comment bodies, check \
   run details).
2. Decide whether the event is a valid, actionable engineering request.
3. If yes, use the `repo` toolset to inspect the code, apply the smallest possible \
   edit with `apply_edit`, and verify locally with `run_ruff` and `run_pytest`.
4. Only call `git_commit_and_push` if local verification passes. If the push is \
   refused (`remote_advanced` etc.), do NOT retry blindly; report it as \
   `blocking_reason`.
5. If a comment is opinion-only, unclear, or out of scope, do not apply it. \
   Explain in `summary` why you skipped it.

Always return one structured `FixPlan` describing what you did in this iteration. \
Be concise.
"""


def _build_agent() -> Agent[AgentDeps, FixPlan]:
    return Agent(
        "anthropic:claude-sonnet-4-6",
        name="pr_fixer",
        deps_type=AgentDeps,
        output_type=FixPlan,
        toolsets=[build_github_mcp_server(), local_repo_toolset],
        instructions=INSTRUCTIONS,
    )


# Eagerly built at import time so PydanticAIPlugin can register its activities.
# Tests that don't need it set GITHUB_TOKEN to a dummy value, or import
# lazily through a wrapper if that proves awkward.
agent = _build_agent()
temporal_agent = TemporalAgent(agent)
