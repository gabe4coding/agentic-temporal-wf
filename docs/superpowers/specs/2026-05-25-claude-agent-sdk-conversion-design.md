# Conversion to Claude Agent SDK — Design Doc

- **Date**: 2026-05-25
- **Author**: gpavanello
- **Status**: Draft (PoC follow-up)
- **Parent spec**: [`2026-05-25-temporal-pydantic-pr-autofix-design.md`](2026-05-25-temporal-pydantic-pr-autofix-design.md)

## 1. Why

The original PoC validated Temporal + Pydantic AI as an agentic-workflow shape. Iterating with it surfaced a real ceiling: Pydantic AI is a generic agent framework, not a coding agent. To do non-trivial coding work (grep, multi-file edits, real navigation) we'd be building Claude Code's toolset by hand on top of Pydantic AI.

The Claude Agent SDK (`claude-agent-sdk` Python package) ships those tools — Read, Edit, Grep, Glob — and is the same brain Claude Code itself runs. Swapping Pydantic AI for the SDK gives an immediate level-up in coding capability without losing the Temporal orchestration layer that's the whole point of the PoC.

Aligned with TheFork's existing investment in Claude Code as the primary agentic platform.

## 2. Goals / non-goals

### Goals
- Keep the entire Temporal-side orchestration (workflow, signals, gateway, lifecycle activities, docker stack) **unchanged**.
- Replace the Pydantic AI agent with a Claude Agent SDK invocation inside a single long-running Temporal activity per iteration.
- Enable builtin coding tools (Read, Edit, Grep, Glob) **plus** our custom repo MCP (ruff/pytest/git) **plus** the GitHub MCP (external stdio binary).
- Preserve the public surface: same workflow id, same `FixPlan` shape posted to GitHub, same self-trigger guard, same `post_status` behavior.

### Non-goals
- Enabling Bash / shell execution (deliberate sandbox).
- Container-per-workflow isolation (deferred).
- Granular Temporal-level retry per individual model/tool call (one of the trade-offs of going to SDK — see §6).
- Migrating off Anthropic models (the SDK is Claude-only by design).
- Multi-agent / sub-agent spawning (this is a 1-to-1 conversion; agent delegation can come later).

## 3. Architecture

```
gateway/app.py
  │  webhook → HMAC → project event → signal_with_start
  ▼
PRAutofixWorkflow  (unchanged shape: signal-driven, @workflow.init, state, budget)
  │  on every iteration:
  ▼
@activity.defn run_agent_iteration(state, events) -> FixPlan
  │  setup env: AUTOFIX_WORKDIR_ID = workflow_id
  │  build ClaudeAgentOptions:
  │    system_prompt = INSTRUCTIONS
  │    mcp_servers = {
  │      "github": stdio github-mcp-server (Go binary, unchanged),
  │      "repo":   create_sdk_mcp_server(name="repo", tools=[...]),
  │    }
  │    allowed_tools = ["Read", "Edit", "Grep", "Glob",
  │                     "mcp__github__*", "mcp__repo__*"]
  │    permission_mode = "bypassPermissions"
  │    env = {"CLAUDE_CODE_MAX_RETRIES": "0"}
  │  start background heartbeat task (every 30s)
  ▼
  async for msg in query(prompt, options):
      if AssistantMessage: log tool calls
      if ResultMessage: capture .result as final text
  ▼
  parse final text → FixPlan (prompted JSON convention)
  ▼
return FixPlan
  │
  ▼
post_status (unchanged, best-effort check_run)
```

## 4. What stays identical

- `src/models.py` — all Pydantic models including `FixPlan`, `WorkflowState`, `GitHubEvent`, `PRRef`, `AgentDeps`
- `src/tools/_local_repo_impl.py` — pure functions (read/list/edit/ruff/pytest/git_*) unchanged; they don't know about agents
- `src/tools/_workdir.py` — `workdir_root()` and `safe_join()` unchanged
- `src/activities/lifecycle.py` — `prepare_workdir`, `cleanup_workdir`, `post_status` unchanged
- `src/gateway/app.py` — gateway logic incl. self-trigger guard
- `Dockerfile` — still ships `github-mcp-server` Go binary; only Python deps differ
- `docker-compose.yml` — services, volumes, env unchanged
- All existing `.env` keys (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `ANTHROPIC_MAX_RETRIES=0`)

## 5. What changes (file-by-file)

| File | Action | Notes |
|---|---|---|
| `pyproject.toml` | Edit | swap `pydantic-ai[mcp,anthropic]` → `claude-agent-sdk` |
| `src/tools/local_repo.py` | Rewrite | tools now declared via `@tool("name", "desc", schema)`; functions are async, accept args dict, return `{"content": [{"type": "text", "text": ...}]}`. Wrapped into a single SDK-MCP server via `create_sdk_mcp_server(name="repo", tools=[...])`. The wrapper's exported name (`local_repo_mcp_server`) is what `pr_fixer.py` references |
| `src/tools/github_mcp.py` | Slim down | drops `MCPServerStdio` (Pydantic AI). Now returns just a config `dict` `{"command": "github-mcp-server", "args": ["stdio"], "env": {...}}` that the activity inlines into `ClaudeAgentOptions.mcp_servers["github"]` |
| `src/agents/pr_fixer.py` | Rewrite | drops `Agent`, `TemporalAgent`. Exports: `INSTRUCTIONS` (the system prompt) and `build_options(workdir_id) -> ClaudeAgentOptions` |
| `src/activities/agent_iteration.py` | **NEW** | `@activity.defn async def run_agent_iteration(state: WorkflowState, events: list[GitHubEvent]) -> FixPlan`. Builds options, runs `query()`, heartbeats, parses final text into `FixPlan`. Sets `AUTOFIX_WORKDIR_ID` env on entry so tool functions can resolve workdir |
| `src/workflows/pr_autofix.py` | Edit | drops `PydanticAIWorkflow` (use plain `@workflow.defn`), drops `__pydantic_ai_agents__`, drops import of `temporal_agent`. Replaces `temporal_agent.run(prompt, deps=...)` with `workflow.execute_activity(run_agent_iteration, args=[self._state, events_snapshot], start_to_close_timeout=timedelta(minutes=10), heartbeat_timeout=timedelta(seconds=60), retry_policy=RetryPolicy(maximum_attempts=2))` |
| `src/worker.py` | Edit | drops `PydanticAIPlugin`; adds `run_agent_iteration` to `activities=[...]` |
| `tests/test_pr_fixer_agent.py` | Rewrite | mock `claude_agent_sdk.query` via `unittest.mock.patch` returning an async generator that yields fake `AssistantMessage` then a `ResultMessage(subtype="success", result='{...FixPlan json...}')` |
| `tests/test_workflow.py` | Edit | replace the previous TestModel/override mechanism with a `@activity.defn(name="run_agent_iteration")` stub that returns a canned `FixPlan` |
| `tests/test_local_repo_impl.py` | No change | tests the pure functions — same shape |
| `tests/test_lifecycle.py` | No change | tests pure helpers — same shape |
| `tests/test_gateway.py` | No change | gateway behavior identical |
| `tests/test_models.py` | No change | models unchanged |

## 6. Trade-offs and known issues

1. **No native structured output.** Pydantic AI's `output_type=FixPlan` gave us validated typed output for free. The SDK returns text; we tell the agent to end with a JSON object and parse it via `FixPlan.model_validate_json(...)`. If the agent produces non-JSON or invalid JSON, `run_agent_iteration` falls back to a `FixPlan(action="blocked", blocking_reason="agent output not parseable")` — same shape used elsewhere on failure (spec §13 of parent doc). Worth a retry-once attempt on parse error before falling back; out of scope for v1.

2. **No granular Temporal retry per LLM/tool call.** Pydantic AI's `TemporalAgent` wrapped every model and tool call as its own activity. Now the entire iteration is one activity. If Anthropic 429s in the middle of an iteration, Temporal restarts the iteration from scratch (one activity attempt = one full `query()` call). Mitigations:
   - `CLAUDE_CODE_MAX_RETRIES=0` (env) so the SDK doesn't internally swallow + retry
   - `RetryPolicy(maximum_attempts=2, initial_interval=30s, backoff_coefficient=2)` on the activity
   - `start_to_close_timeout=10min` cap

3. **Heartbeat is coarse.** `query()` is a black box from the outside — we don't see per-tool-call events in real time. We start a background asyncio task in `run_agent_iteration` that heartbeats every 30s, and the heartbeat carries a simple counter. If the SDK hangs entirely, Temporal kills the activity via heartbeat timeout. We don't get progress detail; for a PoC, fine.

4. **Workdir resolution via env var.** Tool functions inside an SDK MCP receive an `args` dict — there is no `RunContext`/deps equivalent. The activity sets `AUTOFIX_WORKDIR_ID=activity.info().workflow_id` in the process env before calling `query()`. Tool functions read this env var and pass it to the underlying `_workdir.workdir_root()`. Since activities run sequentially in the worker for one workflow id, env-var contention isn't an issue. This is documented inline and as a comment in the tool wrapper.

5. **Builtin tool path scoping.** Read/Edit/Grep/Glob have no built-in path constraint — they can read anywhere the worker process can read. For the PoC we accept the risk (worker container only contains the PoC code and the per-workflow workdir under `/tmp/`). In production this would warrant a container-per-workflow isolation OR a custom permission hook.

6. **Test mocking is uglier.** No `TestModel` analog. We `unittest.mock.patch("claude_agent_sdk.query", ...)` and return an async generator. The fakes hand-construct `AssistantMessage` and `ResultMessage` instances — couples tests to SDK type names.

7. **`MCPServerStdio` deprecation warning** (from Pydantic AI 1.x → 2.x) — gone, since we drop Pydantic AI entirely.

8. **Self-trigger guard** continues to work unchanged: the gateway still does the GitHub API GET on `pr_synchronize` and drops if the commit message contains `[autofix-bot]`. The trailer is still appended in `git_commit_and_push` in `_local_repo_impl.py`.

## 7. Tool catalog after conversion

What the agent will see:

**Builtin (Claude Code):**
- `Read`, `Edit`, `Grep`, `Glob` — direct filesystem access from the worker

**Custom MCP `mcp__repo__*`:**
- `read_file(path)`, `list_files(glob)`, `apply_edit(path, new_content)` — workdir-scoped, safe_join-protected
- `run_ruff()`, `run_pytest(target?)` — verified execution
- `git_status()`, `git_commit_and_push(message)` — appends `[autofix-bot]` trailer

**Custom MCP `mcp__github__*`** (Go binary, unchanged):
- All GitHub MCP operations: `get_pull_request`, `list_review_comments`, `list_issue_comments`, `list_check_runs`, `create_issue_comment`, `update_issue_comment`, etc.

The agent picks. We expect it to gravitate toward `Grep`/`Read` for codebase navigation (where the builtin tools shine) and toward `mcp__repo__*` for verification/commit (where our safety rails matter).

## 8. Versioning / branch strategy

- Current PoC branch `impl/pr-autofix-poc` is **frozen** at commit `c383014` + the recent fixes. Pydantic AI version preserved.
- New branch `impl/pr-autofix-claude-sdk` branches from current `impl/pr-autofix-poc` HEAD.
- Spec doc lives at `docs/superpowers/specs/2026-05-25-claude-agent-sdk-conversion-design.md` (this file).
- Plan doc at `docs/superpowers/plans/2026-05-25-claude-agent-sdk-conversion.md`.

## 9. Out of scope (deferred)

- Container-per-iteration sandbox so Read/Edit can't see worker internals
- Sub-agent spawning (planner → researcher → editor)
- Replacing `apply_edit` (full-file) with `apply_patch` (unified diff)
- Persistent message history across iterations (currently each iteration runs `query()` fresh)
- Logfire / structured tracing (`LogfirePlugin` doesn't apply to SDK)
- GitHub App auth so Check Runs creation works (currently swallowed best-effort)

## 10. Acceptance criteria

- Test suite still 32/32 (same number of tests after conversion; tests are updated, not reduced)
- `docker compose up --build` brings the stack up cleanly
- Manual smoke test: open a PR with 1 ruff violation on `gabe4coding/claude-profiles` → workflow runs → agent applies fix → push → status comment posted; self-trigger guard prevents re-loop
- The autofix commit on the PR has the `[autofix-bot]` trailer in the message
