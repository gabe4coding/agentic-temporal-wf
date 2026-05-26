# tf-guardrails

Org-level guardrails enforced via Claude Code PreToolUse hooks. Currently:

- Categorical deny of `Bash` and `WebFetch` (agent must go through MCP tools).
- Path scoping: all path-bearing builtin tools must resolve inside the
  per-workflow workdir, never into `.git/hooks`, `.github/workflows`, `.claude`.

Loaded by Temporal-hosted agents via `ClaudeAgentOptions.plugins=[...]` and
by laptop Claude Code via `/plugin install`.
