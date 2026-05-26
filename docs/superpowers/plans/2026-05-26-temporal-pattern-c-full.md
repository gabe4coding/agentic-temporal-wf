# Pattern C (Reference Architecture — Temporal × Sandbox) — Full Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the `agent-temporal` PoC to 100% compliance with the Pattern C "Temporal × Sandbox" reference architecture from the *Agents Deployment at TheFork* ADR (Notion `36b1237c10bf818d85d3ee6b1bb28240`).

**Architecture:** The Temporal Worker becomes a *control plane* — it provisions a per-workflow sandbox, dispatches the Claude Agent SDK *inside* the sandbox, and observes its message stream. Vault credentials never enter the sandbox — they are injected at a Worker-side credential/MCP proxy. HITL approval flows through a Workflow Update issued by the proxy. OpenTelemetry spans flow to Arize. Long payloads spill to S3 via External Payload Storage.

**Tech Stack:** Python 3.12, `temporalio>=1.7`, `claude-agent-sdk>=0.1`, `openinference-instrumentation-anthropic`, `arize-otel`, `boto3` (S3 codec), Docker sibling containers (sandbox), tinyproxy + custom HTTP proxy (credential injection), `mitmproxy` style header rewriting.

**Scope check.** Pattern C touches 10 independent subsystems. Each phase below produces standalone, testable software and is committable on its own. They can be implemented in order (recommended — earlier phases reduce blast radius) or partially (only those relevant to a sub-goal). Phases 4 (credential proxy) and 5 (agent-in-sandbox) are the architectural inflection point and are tightly coupled.

---

## File Structure

### New files

- `plugins/tf-guardrails/.claude-plugin/plugin.json` — plugin manifest
- `plugins/tf-guardrails/hooks/hooks.json` — PreToolUse wiring
- `plugins/tf-guardrails/hooks/restrict_paths.py` — moved from `.claude/hooks/`
- `plugins/tf-guardrails/SKILL.md` — plugin docs
- `plugins/tf-mitigations/.claude-plugin/plugin.json`
- `plugins/tf-mitigations/hooks/hooks.json`
- `plugins/tf-mitigations/hooks/secret_scan.py` — input guardrail
- `plugins/tf-mitigations/hooks/signed_trailer_verify.py` — PostToolUse on git commits
- `plugins/tf-mitigations/SKILL.md`
- `src/repo_allowlist.py` — allowed-repo gate at gateway
- `src/observability/__init__.py`
- `src/observability/otel.py` — OpenInference + Arize wiring
- `src/payload_storage/__init__.py`
- `src/payload_storage/s3_codec.py` — Temporal `PayloadCodec` for S3 spill
- `src/proxy/__init__.py`
- `src/proxy/credential_proxy.py` — FastAPI HTTPS forward proxy with credential injection + FQDN allowlist + HITL gate
- `src/proxy/Dockerfile`
- `src/agent_runner/__init__.py`
- `src/agent_runner/main.py` — `python -m src.agent_runner.main`, runs *inside* the sandbox
- `src/agent_runner/stream_codec.py` — JSON-lines marshaling of SDK messages
- `src/activities/idempotency.py` — derive tool-call idempotency keys
- `src/activities/snapshot.py` — sandbox snapshot/restore via Docker commit + S3
- `src/activities/approval.py` — `notify_human_for_approval` activity
- `src/activities/cve_check.py` — daily activity comparing pinned SDK versions to advisory feed
- `tests/test_repo_allowlist.py`
- `tests/test_plugin_load.py`
- `tests/test_credential_proxy.py`
- `tests/test_agent_runner.py`
- `tests/test_approval.py`
- `tests/test_otel.py`
- `tests/test_s3_codec.py`
- `tests/test_idempotency.py`
- `tests/test_snapshot.py`
- `tests/test_worker_versioning.py`
- `tests/test_cve_check.py`
- `scripts/gc_orphans.py` — periodic GC for orphan sandbox containers

### Modified files

- `src/agents/pr_fixer.py` — switch to SDK-native `options.sandbox`, drop `bypassPermissions`, add `plugins=[...]` and `can_use_tool`
- `src/activities/agent_iteration.py` — full rewrite: dispatch + observe pattern (no in-process `query()`)
- `src/activities/sandbox.py` — point `HTTPS_PROXY` at credential-proxy, no GITHUB_TOKEN in sandbox env
- `src/workflows/pr_autofix.py` — Workflow Update + Signal handlers for HITL, sandbox snapshot cadence
- `src/worker.py` — Worker Versioning (build_id), OTel init, S3 PayloadCodec, register new activities
- `src/gateway/app.py` — repo allowlist enforcement
- `src/models.py` — `ApprovalRequest`, `ApprovalDecision`, `ToolCallIdemKey`, `SnapshotRef`
- `sandbox/Dockerfile` — install `claude-agent-sdk`, copy `agent_runner/`, pin Claude Code CLI version
- `docker-compose.yml` — new `credential-proxy` service, `otel-collector` service, `minio` service (S3 emulator for local), remove GITHUB_TOKEN from worker env path going into sandbox
- `Dockerfile` — pin `ANTHROPIC_BASE_URL`/`HTTPS_PROXY`, drop sensitive-path mounts, switch worker to non-root uid where possible
- `pyproject.toml` — add `openinference-instrumentation-anthropic`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `arize-otel`, `boto3`
- `.env.example` — `ARIZE_API_KEY`, `ARIZE_SPACE_ID`, `AWS_S3_BUCKET`, `ANTHROPIC_BASE_URL`, `ALLOWED_REPOS`

### Deleted files

- `.claude/hooks/restrict_paths.py` — moved to `plugins/tf-guardrails/`
- `.claude/worker-settings.json` — replaced by plugin distribution

---

## Phase 1 — Common hardening (CVE mitigations + repo allowlist)

**Goal:** Close the Production Hardening Checklist *"Common to all engines"* items: pin `ANTHROPIC_BASE_URL`, scrub sensitive mounts, refuse non-allowlisted repos. Pure additions, no architectural shift.

### Task 1.1: Repo allowlist module + tests

**Files:**
- Create: `src/repo_allowlist.py`
- Create: `tests/test_repo_allowlist.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repo_allowlist.py
import pytest

from src.repo_allowlist import RepoAllowlist, RepoDenied


def test_allowlist_accepts_listed_repo() -> None:
    allow = RepoAllowlist(["lafourchette/playground", "lafourchette/web"])
    allow.check("lafourchette", "playground")  # does not raise


def test_allowlist_rejects_unknown_repo() -> None:
    allow = RepoAllowlist(["lafourchette/playground"])
    with pytest.raises(RepoDenied, match="lafourchette/other not in allowlist"):
        allow.check("lafourchette", "other")


def test_allowlist_from_env_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_REPOS", "lafourchette/playground,lafourchette/web")
    allow = RepoAllowlist.from_env()
    allow.check("lafourchette", "web")


def test_allowlist_empty_env_is_deny_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_REPOS", raising=False)
    allow = RepoAllowlist.from_env()
    with pytest.raises(RepoDenied):
        allow.check("lafourchette", "playground")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repo_allowlist.py -v`
Expected: FAIL with `ModuleNotFoundError: src.repo_allowlist`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/repo_allowlist.py
"""Refuses to run the agent on repositories not in the allowlist.

Pattern-C requirement (Sandboxing CVE callout):
'(c) refuse to run agents on repositories not in an allowlist'.

The allowlist is the set of repos that the deployment owners explicitly
listed in ALLOWED_REPOS. Empty/unset => deny everything (fail closed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class RepoDenied(Exception):
    """Raised when a repo is not in the allowlist."""


@dataclass(frozen=True)
class RepoAllowlist:
    repos: frozenset[str]

    @classmethod
    def from_env(cls) -> "RepoAllowlist":
        raw = os.environ.get("ALLOWED_REPOS", "")
        items = [s.strip() for s in raw.split(",") if s.strip()]
        return cls(frozenset(items))

    def __init__(self, repos):  # type: ignore[no-untyped-def]
        object.__setattr__(self, "repos", frozenset(repos))

    def check(self, owner: str, repo: str) -> None:
        slug = f"{owner}/{repo}"
        if slug not in self.repos:
            raise RepoDenied(f"{slug} not in allowlist")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repo_allowlist.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/repo_allowlist.py tests/test_repo_allowlist.py
git commit -m "feat(hardening): add repo allowlist gate

Pattern-C common hardening: refuse agent runs on repositories not in
ALLOWED_REPOS. Empty/unset env fails closed (deny all)."
```

### Task 1.2: Wire allowlist into the gateway

**Files:**
- Modify: `src/gateway/app.py` (after the projection step, before `start_workflow`)
- Modify: `tests/test_gateway.py` (add allowlist test)

- [ ] **Step 1: Add the test (extend `tests/test_gateway.py`)**

```python
# tests/test_gateway.py — append
import hmac, hashlib, json

from fastapi.testclient import TestClient

from src.gateway.app import create_app


def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_gateway_rejects_non_allowlisted_repo(monkeypatch):
    monkeypatch.setenv("ALLOWED_REPOS", "lafourchette/playground")

    class FakeClient:
        async def start_workflow(self, *a, **kw):  # pragma: no cover
            raise AssertionError("workflow must not start for denied repo")

    app = create_app(temporal_client=FakeClient(), webhook_secret="s")
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 1,
            "head": {"sha": "deadbeef", "ref": "feature"},
        },
        "repository": {"owner": {"login": "evil"}, "name": "thirdparty"},
    }
    body = json.dumps(payload).encode()
    r = TestClient(app).post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d-1",
            "X-Hub-Signature-256": _sig("s", body),
        },
    )
    assert r.status_code == 403
    assert "not in allowlist" in r.text
```

- [ ] **Step 2: Run the new test — verify it fails**

Run: `uv run pytest tests/test_gateway.py::test_gateway_rejects_non_allowlisted_repo -v`
Expected: FAIL — gateway currently returns 202.

- [ ] **Step 3: Edit `src/gateway/app.py`**

After `pr, event = projected` and before the self-trigger guard, insert:

```python
from src.repo_allowlist import RepoAllowlist, RepoDenied

# ... inside the webhook function, right after pr, event = projected:
        try:
            RepoAllowlist.from_env().check(pr.owner, pr.repo)
        except RepoDenied as e:
            raise HTTPException(status_code=403, detail=str(e))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_gateway.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/app.py tests/test_gateway.py
git commit -m "feat(gateway): enforce repo allowlist before starting workflow

Maps to Pattern-C common hardening: refuse non-allowlisted repos
(mitigates supply-chain abuse via webhook-as-trigger)."
```

### Task 1.3: Pin ANTHROPIC_BASE_URL + drop sensitive mounts

**Files:**
- Modify: `Dockerfile` (worker)
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Edit `.env.example`** — append:

```bash
# CVE-2026-21852 mitigation: pin the API base URL so a hijacked project
# config cannot redirect traffic. Set to the official Anthropic API host.
ANTHROPIC_BASE_URL=https://api.anthropic.com

# Comma-separated allowlist of {owner}/{repo} slugs the agent is allowed
# to act on. Empty/unset = deny all.
ALLOWED_REPOS=lafourchette/playground
```

- [ ] **Step 2: Edit `docker-compose.yml`** — under the `worker` service `environment:` block, add:

```yaml
      # CVE-2026-21852 mitigation: pin API base URL.
      - ANTHROPIC_BASE_URL=${ANTHROPIC_BASE_URL:-https://api.anthropic.com}
      - ALLOWED_REPOS=${ALLOWED_REPOS}
```

Also under `gateway`:

```yaml
      - ALLOWED_REPOS=${ALLOWED_REPOS}
```

- [ ] **Step 3: Edit `Dockerfile`** — after the `WORKDIR /app` line, add a sanity-check that fails the build if sensitive paths somehow end up in the image:

```dockerfile
# Pattern-C hardening: refuse to ship sensitive paths in the image.
RUN test ! -e /root/.ssh && test ! -e /root/.aws \
    && test ! -e /root/.config/gcloud && test ! -e /root/.docker/config.json \
    || (echo "sensitive path leaked into image" && exit 1)
```

- [ ] **Step 4: Validate the env wiring**

Run: `docker compose config | grep -E "ANTHROPIC_BASE_URL|ALLOWED_REPOS"`
Expected: both vars resolved in `worker` (and `ALLOWED_REPOS` in `gateway`).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example
git commit -m "feat(hardening): pin ANTHROPIC_BASE_URL + refuse sensitive mounts

CVE-2026-21852 mitigation (API base URL hijack). Build fails if any of
the canonical sensitive paths is present in the worker image."
```

---

## Phase 2 — SDK-native sandbox + tighter permission model

**Goal:** Replace the pre-SDK `IS_SANDBOX=1` + `permission_mode=bypassPermissions` pair with the December-2025 `options.sandbox` block. This is the "What changed in Dec 2025" callout in the ADR.

### Task 2.1: Update agent options builder

**Files:**
- Modify: `src/agents/pr_fixer.py`
- Modify: `tests/test_sdk_probe.py` (and any test that imports `build_options`)

- [ ] **Step 1: Add a contract test for the new options shape**

Create `tests/test_build_options.py`:

```python
from src.agents.pr_fixer import build_options


def test_build_options_uses_sdk_sandbox(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("CREDENTIAL_PROXY_URL", "http://credential-proxy:8443")
    opts = build_options()
    # Pattern-C: no more bypassPermissions.
    assert opts.permission_mode != "bypassPermissions"
    # SDK-native sandbox block must be present and enabled.
    # SandboxSettings is a TypedDict (camelCase keys).
    sandbox = getattr(opts, "sandbox", None)
    assert sandbox is not None
    assert sandbox["enabled"] is True
    assert sandbox["network"]["allowedDomains"]
    # Bash / Write / WebFetch explicitly disallowed.
    for forbidden in ("Bash", "Write", "WebFetch"):
        assert forbidden in (opts.disallowed_tools or [])
```

- [ ] **Step 2: Run it — verify failure**

Run: `uv run pytest tests/test_build_options.py -v`
Expected: FAIL — `permission_mode == "bypassPermissions"`.

- [ ] **Step 3: Rewrite `build_options`**

Replace `src/agents/pr_fixer.py`:

```python
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
            "github": build_github_mcp_config(proxy_url),
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
```

(`build_github_mcp_config(proxy_url)` will be updated in Phase 4 to point the GitHub MCP at the proxy.)

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_build_options.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/pr_fixer.py tests/test_build_options.py
git commit -m "feat(agent): switch to SDK-native options.sandbox

Replace IS_SANDBOX=1 + permission_mode=bypassPermissions with the
December-2025 sandbox block (network allowlist + excluded_commands).
Explicit disallowed_tools for Bash / Write / WebFetch as defense in
depth alongside the tf-guardrails plugin hooks."
```

### Task 2.2: Remove `IS_SANDBOX=1` env from the worker container

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Delete the line `- IS_SANDBOX=1` from `worker.environment`**

- [ ] **Step 2: Quick sanity boot**

Run: `docker compose up --build worker 2>&1 | head -20`
Expected: worker connects to Temporal without complaining about sandbox env (the SDK now configures it via `options.sandbox`).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: remove IS_SANDBOX=1 env (superseded by options.sandbox)"
```

---

## Phase 3 — `thefork-agent-plugins` distribution shape

**Goal:** Repackage the existing hook policy into the Claude-Code plugin bundle format so the same content is loadable on (a) the worker (via `plugins=[...]`), (b) a gh-aw runner (via APM `dependencies:`), (c) an engineer's laptop (`/plugin install`). One bundle, three deployment surfaces — the unifying value proposition the ADR states.

### Task 3.1: Create the `tf-guardrails` plugin skeleton

**Files:**
- Create: `plugins/tf-guardrails/.claude-plugin/plugin.json`
- Create: `plugins/tf-guardrails/hooks/hooks.json`
- Move: `.claude/hooks/restrict_paths.py` → `plugins/tf-guardrails/hooks/restrict_paths.py`
- Create: `plugins/tf-guardrails/SKILL.md`
- Create: `tests/test_plugin_load.py`

- [ ] **Step 1: Write the failing structural test**

```python
# tests/test_plugin_load.py
import json
from pathlib import Path


PLUGIN_ROOT = Path("plugins/tf-guardrails")


def test_plugin_manifest_present():
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "tf-guardrails"
    assert manifest["version"]


def test_plugin_hooks_present():
    hooks = json.loads((PLUGIN_ROOT / "hooks/hooks.json").read_text())
    matchers = [h["matcher"] for h in hooks["hooks"]["PreToolUse"]]
    assert any("Bash" in m and "WebFetch" in m for m in matchers)


def test_restrict_paths_executable():
    p = PLUGIN_ROOT / "hooks/restrict_paths.py"
    assert p.exists()
    assert p.read_text().startswith("#!")
```

- [ ] **Step 2: Run — verify FAIL**

Run: `uv run pytest tests/test_plugin_load.py -v`
Expected: FAIL (files not yet created).

- [ ] **Step 3: Create the manifest**

```json
// plugins/tf-guardrails/.claude-plugin/plugin.json
{
  "name": "tf-guardrails",
  "version": "1.0.0",
  "description": "TheFork org-level guardrails: path scoping + categorical tool deny.",
  "author": "TheFork Architecture",
  "hooks": "./hooks/hooks.json"
}
```

```json
// plugins/tf-guardrails/hooks/hooks.json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Edit|MultiEdit|Write|Grep|Glob|Bash|WebFetch",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/restrict_paths.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Move the hook**

```bash
git mv .claude/hooks/restrict_paths.py plugins/tf-guardrails/hooks/restrict_paths.py
chmod +x plugins/tf-guardrails/hooks/restrict_paths.py
```

The hook already targets `/tmp/autofix-*/repo` — keep as-is for now; Phase 5 will adjust the path to the in-sandbox `/work` location.

- [ ] **Step 5: Create `plugins/tf-guardrails/SKILL.md`** (5–10 lines)

```markdown
# tf-guardrails

Org-level guardrails enforced via Claude Code PreToolUse hooks. Currently:

- Categorical deny of `Bash` and `WebFetch` (agent must go through MCP tools).
- Path scoping: all path-bearing builtin tools must resolve inside the
  per-workflow workdir, never into `.git/hooks`, `.github/workflows`, `.claude`.

Loaded by Temporal-hosted agents via `ClaudeAgentOptions.plugins=[...]` and
by laptop Claude Code via `/plugin install`.
```

- [ ] **Step 6: Run the test — should pass**

Run: `uv run pytest tests/test_plugin_load.py -v`
Expected: PASS.

- [ ] **Step 7: Update the Dockerfile to bundle the plugin into the image**

Replace the `.claude/` copy lines in `Dockerfile` with:

```dockerfile
# Pattern-C plugin distribution: ship the org-level guardrails bundle
# into the worker image at a stable path. The Claude Agent SDK loads it
# via plugins=[{"type": "local", "path": "/plugins/tf-guardrails"}].
COPY plugins/ /plugins/
RUN chmod -R 555 /plugins
```

Remove the now-obsolete `COPY .claude/...` lines and delete `.claude/worker-settings.json`.

- [ ] **Step 8: Commit**

```bash
git add plugins/tf-guardrails tests/test_plugin_load.py Dockerfile
git rm .claude/hooks/restrict_paths.py .claude/worker-settings.json
git commit -m "feat(plugins): package guardrails as a Claude Code plugin bundle

tf-guardrails moves the path-scoping + categorical-deny hooks into the
industry-standard plugin format. Same bundle loadable from a laptop
(/plugin install) and from the worker (plugins=[...])."
```

### Task 3.2: Create the `tf-mitigations` plugin (secret-scan + commit trailer verify)

**Files:**
- Create: `plugins/tf-mitigations/.claude-plugin/plugin.json`
- Create: `plugins/tf-mitigations/hooks/hooks.json`
- Create: `plugins/tf-mitigations/hooks/secret_scan.py`
- Create: `plugins/tf-mitigations/hooks/signed_trailer_verify.py`
- Create: `plugins/tf-mitigations/SKILL.md`
- Create: `tests/test_mitigations.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_mitigations.py
import json
import subprocess
from pathlib import Path

HOOKS = Path("plugins/tf-mitigations/hooks")


def _run_hook(script: str, payload: dict) -> tuple[int, str]:
    p = subprocess.run(
        ["python3", str(HOOKS / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    return p.returncode, p.stdout


def test_secret_scan_blocks_aws_key():
    rc, out = _run_hook(
        "secret_scan.py",
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/autofix-x/repo/foo.py",
                "new_string": "key = 'AKIAIOSFODNN7EXAMPLE'",
            },
        },
    )
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_secret_scan_allows_normal_edit():
    rc, out = _run_hook(
        "secret_scan.py",
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/autofix-x/repo/foo.py",
                "new_string": "x = 1",
            },
        },
    )
    assert rc == 0


def test_signed_trailer_verify_passes_with_trailer():
    rc, out = _run_hook(
        "signed_trailer_verify.py",
        {
            "tool_name": "mcp__repo__git_commit_and_push",
            "tool_input": {"message": "fix: lint\n\n[autofix-bot]"},
        },
    )
    assert rc == 0


def test_signed_trailer_verify_denies_without_trailer():
    rc, out = _run_hook(
        "signed_trailer_verify.py",
        {
            "tool_name": "mcp__repo__git_commit_and_push",
            "tool_input": {"message": "fix: lint"},
        },
    )
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
```

- [ ] **Step 2: Run — verify FAIL**

Run: `uv run pytest tests/test_mitigations.py -v`
Expected: FAIL (scripts missing).

- [ ] **Step 3: Implement `secret_scan.py`**

```python
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
```

- [ ] **Step 4: Implement `signed_trailer_verify.py`**

```python
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
```

- [ ] **Step 5: Create `plugins/tf-mitigations/.claude-plugin/plugin.json` and `hooks.json`**

```json
// plugin.json
{
  "name": "tf-mitigations",
  "version": "1.0.0",
  "description": "TheFork defensive mitigations: secret-scan + commit-trailer verification.",
  "hooks": "./hooks/hooks.json"
}
```

```json
// hooks.json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|MultiEdit|Write|Bash",
        "hooks": [
          {"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/secret_scan.py", "timeout": 5}
        ]
      },
      {
        "matcher": "mcp__repo__git_commit_and_push",
        "hooks": [
          {"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/signed_trailer_verify.py", "timeout": 5}
        ]
      }
    ]
  }
}
```

- [ ] **Step 6: SKILL.md** (one paragraph each on the two hooks)

- [ ] **Step 7: Run — passes**

Run: `uv run pytest tests/test_mitigations.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add plugins/tf-mitigations tests/test_mitigations.py
git commit -m "feat(plugins): tf-mitigations (secret-scan + commit-trailer)

Two PreToolUse hooks delivered as a separate plugin so each can be
turned on/off independently. Conservative regex-based secret patterns
(AWS, GitHub PAT, ssh private key, Anthropic key)."
```

---

## Phase 4 — Credential / MCP proxy outside the sandbox

**Goal:** The agent inside the sandbox must NEVER see real PATs, OAuth tokens, or the Anthropic API key (Pattern-C rule 1). They live in the Worker env and are injected at a Worker-side proxy that the sandbox reaches via `HTTPS_PROXY`. The same proxy is the policy enforcement point for HITL (Pattern-C rule 7) and FQDN allowlist (replaces the standalone `egress-proxy`).

### Task 4.1: Scaffolding the credential proxy

**Files:**
- Create: `src/proxy/__init__.py`
- Create: `src/proxy/credential_proxy.py`
- Create: `src/proxy/Dockerfile`
- Create: `tests/test_credential_proxy.py`

- [ ] **Step 1: Write the test stub for header injection**

```python
# tests/test_credential_proxy.py
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from src.proxy.credential_proxy import create_proxy_app


def _bg_target():
    # Inline "backend" we proxy to in unit tests
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.get("/echo")
    async def echo(request: Request):
        return {
            "auth": request.headers.get("authorization"),
            "url": str(request.url),
        }

    return app


def test_proxy_injects_github_pat(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    app = create_proxy_app(
        github_token=os.environ["GITHUB_TOKEN"],
        anthropic_key="sk-ant-anth",
        allowed_hosts={"api.github.com"},
    )
    # The proxy exposes /__inject_test for unit tests to exercise the
    # injection logic without an outbound HTTP hop.
    r = TestClient(app).post(
        "/__inject_test",
        json={"host": "api.github.com", "method": "GET", "path": "/repos/x/y"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["injected"]["authorization"].startswith("Bearer ghp_secret123")
    assert body["allowed"] is True


def test_proxy_denies_unknown_host():
    app = create_proxy_app(
        github_token="t", anthropic_key="k", allowed_hosts={"api.github.com"}
    )
    r = TestClient(app).post(
        "/__inject_test",
        json={"host": "evil.example", "method": "GET", "path": "/x"},
    )
    assert r.status_code == 403
```

- [ ] **Step 2: Run — verify FAIL**

Run: `uv run pytest tests/test_credential_proxy.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the proxy**

```python
# src/proxy/credential_proxy.py
"""Worker-side credential / MCP proxy.

Pattern-C trust boundary:
- The sandbox container talks to *this* service via HTTPS_PROXY.
- This service holds the real Vault-loaded credentials (GitHub PAT,
  Anthropic API key) and injects them based on the destination host.
- A FQDN allowlist is the L0 network policy. Anything outside the list
  returns 403.
- The HITL approval gate (Phase 6) plugs in here: for a small set of
  side-effectful tool routes (git push, github writes, deploy), the
  proxy issues a Workflow Update via the Temporal client and waits for
  the Signal before forwarding.

The unit-test surface (`/__inject_test`) lets us verify the injection
logic without a forward-proxy hop. The real HTTPS forward-proxy hop is
exercised by tests/test_integration_proxy.py (Docker integration).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request


@dataclass
class _InjectionResult:
    allowed: bool
    injected: dict[str, str]


def _injection_for(
    host: str, *, github_token: str, anthropic_key: str
) -> dict[str, str]:
    h = host.lower()
    if h == "api.github.com" or h == "github.com" or h.endswith(".github.com"):
        return {"authorization": f"Bearer {github_token}"}
    if h == "api.anthropic.com":
        return {
            "x-api-key": anthropic_key,
            "anthropic-version": "2023-06-01",
        }
    return {}


def create_proxy_app(
    *,
    github_token: str,
    anthropic_key: str,
    allowed_hosts: Iterable[str],
) -> FastAPI:
    app = FastAPI(title="agent-temporal credential proxy")
    allowed = {h.lower() for h in allowed_hosts}

    @app.post("/__inject_test")
    async def inject_test(req: Request):
        body = await req.json()
        host = (body.get("host") or "").lower()
        if host not in allowed and not any(
            host.endswith(f".{a}") for a in allowed
        ):
            raise HTTPException(status_code=403, detail=f"host {host} not allowed")
        return {
            "allowed": True,
            "injected": _injection_for(
                host, github_token=github_token, anthropic_key=anthropic_key
            ),
        }

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


def build_default_app() -> FastAPI:
    return create_proxy_app(
        github_token=os.environ["GITHUB_TOKEN"],
        anthropic_key=os.environ["ANTHROPIC_API_KEY"],
        allowed_hosts={
            "api.github.com",
            "github.com",
            "raw.githubusercontent.com",
            "api.anthropic.com",
            "pypi.org",
            "files.pythonhosted.org",
        },
    )


app = build_default_app() if os.environ.get("CREDENTIAL_PROXY_BOOT") == "1" else None
```

- [ ] **Step 4: Run — passes**

Run: `uv run pytest tests/test_credential_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Dockerfile + compose service**

```dockerfile
# src/proxy/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir uv && uv sync --no-dev --frozen || uv sync --no-dev
COPY src/ ./src/
ENV CREDENTIAL_PROXY_BOOT=1
CMD ["uv", "run", "uvicorn", "src.proxy.credential_proxy:app", "--host", "0.0.0.0", "--port", "8443"]
```

In `docker-compose.yml`, add:

```yaml
  credential-proxy:
    build:
      context: .
      dockerfile: src/proxy/Dockerfile
    env_file: .env
    networks:
      - sandbox-net    # internal-only, where sandbox containers reach it
      - default        # outbound side (to GitHub / Anthropic)
    restart: unless-stopped
```

Update `worker.environment`:

```yaml
      - CREDENTIAL_PROXY_URL=http://credential-proxy:8443
      - SANDBOX_EGRESS_PROXY_URL=http://credential-proxy:8443
```

(`SANDBOX_EGRESS_PROXY_URL` is kept name-stable so existing `sandbox.py` keeps working; Phase 5 renames it.)

Remove the standalone `egress-proxy` service — its FQDN allowlist behavior is now subsumed by `credential-proxy`.

- [ ] **Step 6: Commit**

```bash
git add src/proxy tests/test_credential_proxy.py docker-compose.yml
git rm -r sandbox/egress-proxy/
git commit -m "feat(proxy): worker-side credential/MCP proxy

Subsumes the standalone egress-proxy: enforces the FQDN allowlist AND
injects real credentials at the boundary. Sandbox containers reach it
via HTTPS_PROXY=http://credential-proxy:8443. Credentials never enter
the sandbox env."
```

### Task 4.2: Real HTTPS forward-proxy mode

The previous task gives us the injection logic and the test surface. We also need an actual forward-proxy hop (CONNECT method) so that `httpx` / `git` / `curl` inside the sandbox can reach external hosts with credentials transparently appended.

**Files:**
- Modify: `src/proxy/credential_proxy.py`
- Create: `tests/test_credential_proxy_forward.py` (integration; marked `integration`)

- [ ] **Step 1: Add an integration test (Docker-required)**

```python
# tests/test_credential_proxy_forward.py
import pytest

pytestmark = pytest.mark.integration


def test_https_connect_to_allowed_host(running_proxy):
    # `running_proxy` fixture spins the FastAPI app via uvicorn on a port.
    import httpx
    r = httpx.get(
        "https://api.github.com/zen",
        proxy=running_proxy.url,
        timeout=10.0,
    )
    assert r.status_code == 200
```

(Fixture details belong in `tests/conftest.py`; integration tests are off by default per `pyproject.toml`.)

- [ ] **Step 2: Implement CONNECT handling**

Extend `create_proxy_app` with an ASGI middleware that intercepts the HTTP CONNECT method, validates the host against `allowed`, opens a raw TCP tunnel to it, and pumps bytes both ways. For HTTPS (CONNECT) the proxy cannot inject headers — credential injection only applies to *plaintext* `Bearer` tokens for hosts that the proxy itself terminates TLS for. So strategy:

  - HTTPS CONNECT to allowlisted hosts → straight tunnel (no header injection — the agent's own HTTPS client sends the request end-to-end). The Anthropic API key and GitHub PAT must be injected *before* the request leaves the sandbox. Two patterns:
    - **For GitHub MCP / our own httpx clients in `src/tools/_local_repo_impl.py`**: configure them to fetch the token from a local endpoint of the proxy (`GET http://credential-proxy:8443/__token/github`) at request time. The token never persists in the sandbox.
    - **For Anthropic API calls made by the Claude Code CLI**: the CLI reads `ANTHROPIC_API_KEY` from env. In Pattern C we replace this with a *short-lived* API key fetched at iteration start from the proxy (`/__token/anthropic`) and injected into the agent_runner's env right before `query()`.

The proxy app exposes:

```python
@app.get("/__token/{name}")
async def token(name: str, request: Request):
    if name == "github":
        return {"token": github_token, "ttl_s": 600}
    if name == "anthropic":
        return {"token": anthropic_key, "ttl_s": 600}
    raise HTTPException(status_code=404)
```

A future improvement (out of PoC scope, noted in Open Questions) is to mint a short-lived GitHub App installation token instead of a static PAT.

- [ ] **Step 3: Commit**

```bash
git add src/proxy/credential_proxy.py tests/test_credential_proxy_forward.py
git commit -m "feat(proxy): forward CONNECT + per-tenant token endpoint

HTTPS_CONNECT to allowlisted hosts is tunneled raw (no MitM). Sandbox
clients (GitHub MCP, agent_runner) fetch tokens from a localhost
endpoint of the proxy at request time — the secret never persists in
sandbox env."
```

### Task 4.3: Update GitHub MCP wiring to fetch the token from the proxy

**Files:**
- Modify: `src/tools/github_mcp.py`
- Modify: `tests/test_github_mcp.py` (add if missing)

- [ ] **Step 1: New signature `build_github_mcp_config(proxy_url)`**

```python
# src/tools/github_mcp.py
import os
from typing import Any

import httpx


def _fetch_github_token(proxy_url: str) -> str:
    r = httpx.get(f"{proxy_url}/__token/github", timeout=5.0)
    r.raise_for_status()
    return r.json()["token"]


def build_github_mcp_config(proxy_url: str | None = None) -> dict[str, Any]:
    """Build the mcp_servers["github"] config.

    In Pattern C the GitHub PAT lives on the credential proxy; the sandbox
    fetches it via http://credential-proxy:8443/__token/github at iteration
    start and passes it to the MCP server via env. The token is rotated on
    every iteration."""
    proxy = proxy_url or os.environ.get(
        "CREDENTIAL_PROXY_URL", "http://credential-proxy:8443"
    )
    token = _fetch_github_token(proxy)
    return {
        "command": "github-mcp-server",
        "args": ["stdio"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
```

- [ ] **Step 2: Update the test stub**

```python
def test_build_github_mcp_calls_proxy(monkeypatch, httpx_mock):
    httpx_mock.add_response(
        url="http://proxy:8443/__token/github",
        json={"token": "ghp_xxx", "ttl_s": 60},
    )
    cfg = build_github_mcp_config("http://proxy:8443")
    assert cfg["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_xxx"
```

- [ ] **Step 3: Run — passes**

- [ ] **Step 4: Commit**

```bash
git add src/tools/github_mcp.py tests/test_github_mcp.py
git commit -m "feat(github-mcp): fetch PAT from credential proxy per iteration

Sandbox no longer needs GITHUB_TOKEN in its env. The token is fetched
at iteration start and lives only in the github-mcp-server child
process's env, which is reaped at iteration end."
```

---

## Phase 5 — Agent loop inside the sandbox

**Goal:** Implement Pattern C's central rule: *"the Activity host code is provision + dispatch + observe + teardown, not the agent itself. The agent runs inside the sandbox."* Today `claude_agent_sdk.query()` runs in the Worker process. We move it into the sandbox and have the Activity stream messages back via JSON-lines on stdout.

### Task 5.1: Sandbox image gains the agent runtime

**Files:**
- Modify: `sandbox/Dockerfile`

- [ ] **Step 1: Pin Claude Code CLI + bake in claude-agent-sdk**

Replace `sandbox/Dockerfile`:

```dockerfile
FROM python:3.12-slim

ARG CLAUDE_CODE_VERSION=2.0.65

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

RUN pip install --no-cache-dir \
    ruff pytest claude-agent-sdk pydantic httpx

# agent_runner module is copied at build time. It is the entrypoint
# spawned by exec_in_sandbox each iteration.
COPY src/agent_runner /opt/agent_runner
COPY src/models.py /opt/src/models.py
COPY src/tools /opt/src/tools
COPY plugins /plugins

# A read-only mount-point convention. /work is the per-workflow workdir
# mounted from the host's /tmp/autofix-{workflow_id}/repo path.
WORKDIR /work

ENV PYTHONPATH=/opt
ENV PATH=/usr/local/lib/node_modules/.bin:$PATH
```

- [ ] **Step 2: Smoke build**

Run: `docker compose build sandbox-image`
Expected: image `agent-sandbox:latest` rebuilds successfully.

- [ ] **Step 3: Commit**

```bash
git add sandbox/Dockerfile
git commit -m "build(sandbox): ship Claude Agent SDK + agent_runner into the sandbox

Pin Claude Code CLI to v2.0.65 (post-CVE-2025-66479). Bake the SDK and
the agent_runner module into /opt/agent_runner so exec_in_sandbox can
spawn it without bind-mounting worker code."
```

### Task 5.2: `agent_runner` — runs inside the sandbox, streams JSON-lines

**Files:**
- Create: `src/agent_runner/__init__.py` (empty)
- Create: `src/agent_runner/main.py`
- Create: `src/agent_runner/stream_codec.py`
- Create: `tests/test_agent_runner.py`

- [ ] **Step 1: Failing test for stream codec**

```python
# tests/test_agent_runner.py
import json

from src.agent_runner.stream_codec import (
    encode_message,
    decode_messages,
)


def test_round_trip_assistant_message():
    line = encode_message({"type": "assistant", "content": "hi"})
    decoded = list(decode_messages([line]))
    assert decoded == [{"type": "assistant", "content": "hi"}]


def test_decode_skips_garbage_lines():
    lines = ["not json", json.dumps({"type": "result", "result": "ok"})]
    out = list(decode_messages(lines))
    assert out == [{"type": "result", "result": "ok"}]
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement codec**

```python
# src/agent_runner/stream_codec.py
"""JSON-lines codec for SDK messages crossing the sandbox boundary.

Each line on the sandbox's stdout is one JSON object. The Activity host
parses these one at a time so the heartbeat can fire on each tick."""
from __future__ import annotations
import json
from typing import Iterable, Iterator


def encode_message(msg: dict) -> str:
    return json.dumps(msg, default=str)


def decode_messages(lines: Iterable[str]) -> Iterator[dict]:
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue
```

- [ ] **Step 4: Implement runner main**

```python
# src/agent_runner/main.py
"""In-sandbox entrypoint.

Reads a prompt from stdin, runs claude_agent_sdk.query() with the
options built locally (the SDK plugins point at /plugins/*), and writes
each message to stdout as one JSON line. The Activity host parses the
stream, heartbeats per message, and extracts the FixPlan from the
trailing ResultMessage."""
from __future__ import annotations

import asyncio
import json
import os
import sys

from claude_agent_sdk import query, AssistantMessage, ResultMessage

from src.agent_runner.stream_codec import encode_message
# IMPORTANT: build_options is imported lazily because it imports
# httpx etc. which fail-fast on missing env in some test harnesses.


def _serialise(msg) -> dict:
    if isinstance(msg, AssistantMessage):
        return {
            "type": "assistant",
            "content": [
                {
                    "type": getattr(b, "type", None),
                    "text": getattr(b, "text", None),
                    "id": getattr(b, "id", None),
                    "name": getattr(b, "name", None),
                    "input": getattr(b, "input", None),
                }
                for b in (getattr(msg, "content", None) or [])
            ],
        }
    if isinstance(msg, ResultMessage):
        return {
            "type": "result",
            "subtype": getattr(msg, "subtype", None),
            "result": getattr(msg, "result", None),
        }
    return {"type": "other"}


async def _amain() -> int:
    from src.agents.pr_fixer import build_options

    prompt = sys.stdin.read()
    options = build_options()
    async for msg in query(prompt=prompt, options=options):
        sys.stdout.write(encode_message(_serialise(msg)) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:  # exposed for the in-sandbox shell entrypoint
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run — passes**

- [ ] **Step 6: Commit**

```bash
git add src/agent_runner tests/test_agent_runner.py
git commit -m "feat(agent-runner): in-sandbox entrypoint streaming JSON-lines

Runs claude_agent_sdk.query() inside the sandbox and serialises every
message to stdout as one JSON line. Activity host parses the stream
and heartbeats per message."
```

### Task 5.3: Replace `run_agent_iteration` with dispatch + observe

**Files:**
- Modify: `src/activities/agent_iteration.py`
- Modify: `tests/test_agent_iteration.py`

- [ ] **Step 1: Adjust the existing test (or add new) for the dispatch shape**

The new activity contract:
- Input: `(state, events, sandbox_handle)`
- Pipes the prompt into `docker exec ... python -m src.agent_runner.main`
- Iterates lines on stdout, heartbeats per line
- Parses the trailing `result` message's `result` text → `FixPlan`

```python
# tests/test_agent_iteration.py — rewrite
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.activities.agent_iteration import _run_iteration_impl
from src.models import FixPlan, GitHubEvent, PRRef, SandboxHandle, WorkflowState


@pytest.mark.asyncio
async def test_dispatch_parses_result_line(monkeypatch):
    state = WorkflowState(
        pr=PRRef(owner="o", repo="r", number=1, head_sha="abc", head_ref="f"),
        sandbox=SandboxHandle(container_id="cid"),
    )
    events: list[GitHubEvent] = []

    # The dispatch_into_sandbox dependency returns a stream of two lines:
    # one assistant tool_call, one result containing the FixPlan JSON.
    fake_stream = [
        json.dumps({"type": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "result": (
                'Done.\n{"action":"no_action_needed","summary":"nothing to do",'
                '"addressed_comment_ids":[],"addressed_failures":[],'
                '"commit_sha":null,"blocking_reason":null}'
            ),
        }),
    ]

    async def fake_dispatch(handle, prompt):
        for line in fake_stream:
            yield line

    monkeypatch.setattr(
        "src.activities.agent_iteration.dispatch_into_sandbox", fake_dispatch
    )

    plan = await _run_iteration_impl(state, events)
    assert isinstance(plan, FixPlan)
    assert plan.action == "no_action_needed"
```

- [ ] **Step 2: Run — verify it fails**

- [ ] **Step 3: Rewrite `agent_iteration.py`**

```python
"""Pattern-C run_agent_iteration: dispatch + observe.

The activity:
  1. Resolves the workflow's SandboxHandle from state.
  2. Builds the prompt (deterministic from state + events).
  3. Calls dispatch_into_sandbox(handle, prompt) which spawns
     `python -m src.agent_runner.main` inside the sandbox and yields
     JSON-lines from its stdout.
  4. For each line: counts it (for heartbeat detail), keeps a rolling
     reference to the last result message.
  5. Parses the FixPlan out of the result message's trailing JSON.

No in-process claude_agent_sdk.query() call lives here anymore. The
Activity host is a control plane that never executes LLM-generated code."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import AsyncIterator

from temporalio import activity

from src.models import FixPlan, GitHubEvent, SandboxHandle, WorkflowState


logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_S = 30


def _build_prompt(state: WorkflowState, events: list[GitHubEvent]) -> str:
    pr = state.pr
    lines = [
        f"PR: {pr.owner}/{pr.repo}#{pr.number} (head {pr.head_sha[:7]} on {pr.head_ref})",
        f"Iteration: {state.iterations}",
        "",
        "Pending events:",
    ]
    for e in events:
        lines.append(
            f"- [{e.kind}] delivery={e.delivery_id} "
            f"payload_keys={sorted(e.payload.keys())}"
        )
    return "\n".join(lines)


_JSON_TAIL_RE = re.compile(r"\{[^{}]*\"action\"[^{}]*\}", re.DOTALL)


def _parse_fix_plan(text: str) -> FixPlan:
    if not text:
        return FixPlan(
            action="blocked",
            summary="Agent produced no final output.",
            blocking_reason="agent output not parseable: empty",
        )
    matches = list(_JSON_TAIL_RE.finditer(text))
    if not matches:
        return FixPlan(
            action="blocked",
            summary="Agent did not emit a FixPlan JSON tail.",
            blocking_reason=(
                "agent output not parseable: no JSON object containing 'action'"
            ),
        )
    try:
        return FixPlan.model_validate_json(matches[-1].group(0))
    except Exception as e:
        return FixPlan(
            action="blocked",
            summary="Agent FixPlan JSON did not validate.",
            blocking_reason=f"agent output not parseable: {type(e).__name__}",
        )


async def dispatch_into_sandbox(
    handle: SandboxHandle, prompt: str
) -> AsyncIterator[str]:
    """Spawn `python -m src.agent_runner.main` inside the sandbox via the
    Docker exec API and yield stdout lines.

    Streaming `exec_run` from docker-py needs the low-level API
    (`client.api.exec_create` + `exec_start(stream=True)`) so we can
    consume bytes as they arrive."""
    import docker

    client = docker.from_env()
    container = client.containers.get(handle.container_id)
    exec_id = client.api.exec_create(
        container.id,
        cmd=["python", "-m", "src.agent_runner.main"],
        stdin=True,
        stdout=True,
        stderr=False,
        workdir=handle.workdir,
    )["Id"]
    sock = client.api.exec_start(
        exec_id, detach=False, tty=False, stream=False, socket=True
    )
    # docker-py's socket is a raw _SocketStreamReader; write prompt then half-close.
    try:
        sock._sock.sendall(prompt.encode())
        sock._sock.shutdown(1)  # SHUT_WR
        buffer = b""
        while True:
            chunk = sock._sock.recv(65536)
            if not chunk:
                if buffer:
                    yield buffer.decode("utf-8", errors="replace")
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield line.decode("utf-8", errors="replace")
    finally:
        with contextlib.suppress(Exception):
            sock.close()


async def _heartbeat_loop(stop: asyncio.Event, counter: dict) -> None:
    while not stop.is_set():
        with contextlib.suppress(RuntimeError):
            activity.heartbeat(counter)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_S)


async def _run_iteration_impl(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    handle = state.sandbox
    if handle is None:
        return FixPlan(
            action="blocked",
            summary="No sandbox provisioned.",
            blocking_reason="state.sandbox is None — provision_sandbox not run",
        )
    prompt = _build_prompt(state, events)
    counter: dict = {"messages": 0, "tool_calls": 0}
    stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(stop, counter))

    final_text: str = ""
    final_subtype: str | None = None
    try:
        async for raw in dispatch_into_sandbox(handle, prompt):
            counter["messages"] += 1
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "assistant":
                for blk in msg.get("content") or []:
                    if blk.get("type") == "tool_use":
                        counter["tool_calls"] += 1
            elif msg.get("type") == "result":
                final_subtype = msg.get("subtype")
                final_text = msg.get("result") or ""
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await hb_task

    if final_subtype and final_subtype != "success":
        return FixPlan(
            action="blocked",
            summary="Agent terminated abnormally.",
            blocking_reason=f"ResultMessage.subtype={final_subtype}",
        )
    return _parse_fix_plan(final_text)


@activity.defn
async def run_agent_iteration(
    state: WorkflowState, events: list[GitHubEvent]
) -> FixPlan:
    return await _run_iteration_impl(state, events)
```

- [ ] **Step 4: Run unit tests — passes**

Run: `uv run pytest tests/test_agent_iteration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/activities/agent_iteration.py tests/test_agent_iteration.py
git commit -m "refactor(activity): dispatch + observe instead of in-process query

The Worker is now a pure control plane. claude_agent_sdk.query() runs
inside the sandbox; the activity pipes the prompt over docker exec and
parses the JSON-lines stream from stdout. No LLM-generated code ever
runs in the Worker process."
```

### Task 5.4: Idempotency keys for tool side-effects

**Files:**
- Create: `src/activities/idempotency.py`
- Create: `tests/test_idempotency.py`
- Modify: `src/tools/_local_repo_impl.py` to consume the key in `git_commit_and_push`

- [ ] **Step 1: Failing test**

```python
# tests/test_idempotency.py
from src.activities.idempotency import tool_call_key


def test_key_is_deterministic():
    a = tool_call_key("wf-1", 3, "tooluse-abc")
    b = tool_call_key("wf-1", 3, "tooluse-abc")
    assert a == b


def test_key_changes_with_iteration():
    a = tool_call_key("wf-1", 3, "tooluse-abc")
    b = tool_call_key("wf-1", 4, "tooluse-abc")
    assert a != b
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

```python
# src/activities/idempotency.py
"""Idempotency keys for agent tool side-effects.

Pattern-C rule 6: keys derive from (workflow_id, iteration_id, tool_use_id)
and never from anything the agent generates. A retried activity cannot
double-comment, double-commit, or double-charge."""
from __future__ import annotations

import hashlib


def tool_call_key(workflow_id: str, iteration: int, tool_use_id: str) -> str:
    raw = f"{workflow_id}|{iteration}|{tool_use_id}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]
```

- [ ] **Step 4: Use it in `git_commit_and_push`**

The trailer is already stable; add the idempotency key as a second trailer so retries are detectable from git history:

In `src/tools/_local_repo_impl.py`:

```python
def git_commit_and_push(
    workdir: Target,
    message: str,
    *,
    idempotency_key: str | None = None,
) -> CommitResult:
    ...
    full_message = message if AUTOFIX_COMMIT_TRAILER in message else f"{message}\n\n{AUTOFIX_COMMIT_TRAILER}"
    if idempotency_key:
        full_message += f"\nAutofix-Idempotency: {idempotency_key}"
    ...
```

The MCP-tool wrapper in `src/tools/local_repo.py` reads the idempotency key from a contextvar set by `agent_runner.main` from the env (the Activity puts it in the docker exec env at iteration start). For the PoC, leave the contextvar empty when not set — tests still pass.

- [ ] **Step 5: Run — passes**

- [ ] **Step 6: Commit**

```bash
git add src/activities/idempotency.py tests/test_idempotency.py src/tools/_local_repo_impl.py
git commit -m "feat(idempotency): per-tool-call idempotency key

Derives from (workflow_id, iteration, tool_use_id). Stamped as a
Git trailer on autofix commits so a retried activity is detectable from
the commit history."
```

---

## Phase 6 — Human-in-the-loop approval (`can_use_tool` + Workflow Update)

**Goal:** Pattern-C rule 7 — durable HITL approval. The agent calls `can_use_tool`; for risky tools, the callback issues a Workflow Update, the Workflow stores the request, fires a notification activity, blocks on a wait_condition, and returns the decision when the Signal carries it back.

### Task 6.1: Approval data model

**Files:**
- Modify: `src/models.py`
- Create: `tests/test_approval_models.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_approval_models.py
from src.models import ApprovalRequest, ApprovalDecision, ApprovalState


def test_approval_state_round_trip():
    s = ApprovalState(approval_id="a", pending=True)
    assert not s.decided
    s = ApprovalState(approval_id="a", pending=False, allowed=True, reason="ok")
    assert s.decided
    d = s.to_decision()
    assert isinstance(d, ApprovalDecision) and d.allowed is True
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Extend `src/models.py`**

Append:

```python
class ApprovalRequest(BaseModel):
    approval_id: str
    tool_name: str
    tool_input: dict
    iteration: int


class ApprovalDecision(BaseModel):
    allowed: bool
    reason: str = ""
    modified_input: dict | None = None


class ApprovalState(BaseModel):
    approval_id: str
    pending: bool = True
    allowed: bool = False
    reason: str = ""

    @property
    def decided(self) -> bool:
        return not self.pending

    def to_decision(self) -> ApprovalDecision:
        return ApprovalDecision(allowed=self.allowed, reason=self.reason)
```

- [ ] **Step 4: Pass**, then commit:

```bash
git add src/models.py tests/test_approval_models.py
git commit -m "feat(models): ApprovalRequest/Decision/State for HITL"
```

### Task 6.2: Workflow Update + Signal + notification activity

**Files:**
- Modify: `src/workflows/pr_autofix.py`
- Create: `src/activities/approval.py`
- Create: `tests/test_approval.py`

- [ ] **Step 1: Failing test using `WorkflowEnvironment.start_time_skipping`**

```python
# tests/test_approval.py
import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.models import PRRef, ApprovalRequest, ApprovalDecision
from src.workflows.pr_autofix import PRAutofixWorkflow


@pytest.mark.asyncio
async def test_workflow_update_blocks_until_signal(monkeypatch):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async def fake_notify(req: ApprovalRequest) -> None:
            return None

        async with Worker(
            env.client,
            task_queue="t",
            workflows=[PRAutofixWorkflow],
            activities=[fake_notify],  # plus the others, abridged
        ):
            handle = await env.client.start_workflow(
                PRAutofixWorkflow.run,
                PRRef(owner="o", repo="r", number=1, head_sha="x", head_ref="f"),
                id="wf-test",
                task_queue="t",
            )
            update_task = handle.execute_update(
                "request_tool_approval",
                ApprovalRequest(
                    approval_id="a1",
                    tool_name="Bash",
                    tool_input={"command": "git push origin main"},
                    iteration=1,
                ),
            )
            await handle.signal(
                "submit_approval_decision",
                {"approval_id": "a1", "allowed": True, "reason": "ok"},
            )
            decision: ApprovalDecision = await update_task
            assert decision.allowed
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Add `notify_human_for_approval` activity**

```python
# src/activities/approval.py
"""HITL notification activity.

For the PoC, posts a comment on the PR with the approval request. In
production this fan-outs to Slack / Backstage / the dedicated approval UI."""
from __future__ import annotations

import os

import httpx
from temporalio import activity

from src.models import ApprovalRequest


@activity.defn
async def notify_human_for_approval(
    pr_owner: str, pr_repo: str, pr_number: int, req: ApprovalRequest
) -> None:
    proxy = os.environ["CREDENTIAL_PROXY_URL"]
    token = (
        await httpx.AsyncClient(timeout=5.0).get(f"{proxy}/__token/github")
    ).json()["token"]
    body = (
        f"🛑 **AutoFix needs approval** for tool `{req.tool_name}` "
        f"(approval_id `{req.approval_id}`).\n\n"
        f"```\n{req.tool_input}\n```\n\n"
        "Reply with `/autofix approve {approval_id}` or "
        "`/autofix deny {approval_id} <reason>` to resolve."
    )
    async with httpx.AsyncClient(timeout=30.0) as c:
        await c.post(
            f"https://api.github.com/repos/{pr_owner}/{pr_repo}/issues/{pr_number}/comments",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"body": body},
        )
```

- [ ] **Step 4: Extend the Workflow**

In `src/workflows/pr_autofix.py`, add an internal dict + handlers:

```python
from datetime import timedelta
from src.models import ApprovalDecision, ApprovalRequest, ApprovalState
from src.activities.approval import notify_human_for_approval

class PRAutofixWorkflow:
    @workflow.init
    def __init__(self, init):
        ...
        self._approvals: dict[str, ApprovalState] = {}

    @workflow.update
    async def request_tool_approval(self, req: ApprovalRequest) -> ApprovalDecision:
        self._approvals[req.approval_id] = ApprovalState(approval_id=req.approval_id, pending=True)
        await workflow.execute_activity(
            notify_human_for_approval,
            args=[self._state.pr.owner, self._state.pr.repo, self._state.pr.number, req],
            start_to_close_timeout=timedelta(minutes=1),
        )
        await workflow.wait_condition(
            lambda: self._approvals[req.approval_id].decided,
            timeout=timedelta(hours=24),
        )
        return self._approvals.pop(req.approval_id).to_decision()

    @workflow.signal
    def submit_approval_decision(self, payload: dict) -> None:
        st = self._approvals.get(payload["approval_id"])
        if st is None:
            return
        st.pending = False
        st.allowed = bool(payload["allowed"])
        st.reason = payload.get("reason", "")
```

- [ ] **Step 5: Register the new activity in `src/worker.py`** (add `notify_human_for_approval`).

- [ ] **Step 6: Run — passes**

- [ ] **Step 7: Commit**

```bash
git add src/activities/approval.py src/workflows/pr_autofix.py src/worker.py tests/test_approval.py
git commit -m "feat(hitl): durable Workflow Update for tool approval

request_tool_approval (Update) → notify_human_for_approval activity →
wait_condition (24h cap) → submit_approval_decision Signal carries the
human's decision back. Pattern-C rule 7."
```

### Task 6.3: Wire the proxy as the HITL enforcement point

**Files:**
- Modify: `src/proxy/credential_proxy.py`

In Pattern C, the in-sandbox `can_use_tool` is just a fast-path local guard. The durable gate is the credential proxy: when the agent's HTTPS request matches a *gated route* (e.g. `POST /repos/*/*/pulls/*/comments`, `POST /repos/*/*/git/refs`, `POST /repos/*/*/check-runs`), the proxy issues the Workflow Update via the Temporal client and only forwards the call on a "yes" decision.

- [ ] **Step 1: Add `gated_route_matches(method, host, path)` helper + a Temporal client field on the app state**

(implementation detail: depends on how the Temporal client is initialised in the proxy. For the PoC, fetch a client at app start via `Client.connect(TEMPORAL_TARGET)` and store it on `app.state`. The proxy needs to know `workflow_id` per request — pass it via a custom `X-TheFork-Workflow-Id` header that the GitHub MCP tool stamps on every outbound call.)

- [ ] **Step 2: Test + commit** — analogous to 4.1 with the gated paths mocked.

```bash
git commit -m "feat(proxy): enforce HITL gate on side-effectful GitHub routes"
```

---

## Phase 7 — Observability (OpenInference → Arize)

**Goal:** Wire OpenInference's Anthropic instrumentor in the Worker and the Arize OTel exporter so every workflow run is a trace tree in Arize.

### Task 7.1: Add deps + setup module

**Files:**
- Modify: `pyproject.toml`
- Create: `src/observability/__init__.py`
- Create: `src/observability/otel.py`
- Create: `tests/test_otel.py`

- [ ] **Step 1: Add to `pyproject.toml`:**

```toml
    # The claude-agent-sdk-specific instrumentor captures both query()
    # calls and tool calls as OpenTelemetry AGENT/TOOL spans, which is
    # closer to what Arize expects than the raw Anthropic instrumentor.
    # See https://github.com/Arize-ai/openinference/tree/main/python/instrumentation/openinference-instrumentation-claude-agent-sdk
    "openinference-instrumentation-claude-agent-sdk>=0.1",
    "opentelemetry-sdk>=1.25",
    "opentelemetry-exporter-otlp>=1.25",
    "arize-otel>=0.7",
```

- [ ] **Step 2: Failing test**

```python
# tests/test_otel.py
from src.observability.otel import setup_otel


def test_setup_otel_is_idempotent(monkeypatch):
    monkeypatch.setenv("ARIZE_API_KEY", "x")
    monkeypatch.setenv("ARIZE_SPACE_ID", "s")
    p1 = setup_otel("agent-temporal-dev")
    p2 = setup_otel("agent-temporal-dev")
    assert p1 is p2  # singleton TracerProvider
```

- [ ] **Step 3: Implement**

```python
# src/observability/otel.py
"""OpenInference + Arize OTel wiring.

Loaded once at worker boot. Claude Agent SDK calls (both `query()` and
`ClaudeSDKClient` sessions) are auto-instrumented as AGENT spans, with
tool calls becoming TOOL child spans.

Upstream signatures verified against:
- arize-otel: arize.otel.register(space_id, api_key, project_name) -> TracerProvider
- openinference-instrumentation-claude-agent-sdk:
  ClaudeAgentSDKInstrumentor().instrument(tracer_provider=...)
"""
from __future__ import annotations

import os

from arize.otel import register
from openinference.instrumentation.claude_agent_sdk import (
    ClaudeAgentSDKInstrumentor,
)
from opentelemetry import trace


_PROVIDER = None


def setup_otel(project_name: str):
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    _PROVIDER = register(
        space_id=os.environ["ARIZE_SPACE_ID"],
        api_key=os.environ["ARIZE_API_KEY"],
        project_name=project_name,
    )
    trace.set_tracer_provider(_PROVIDER)
    ClaudeAgentSDKInstrumentor().instrument(tracer_provider=_PROVIDER)
    return _PROVIDER
```

- [ ] **Step 4: Boot OTel from `src/worker.py`** — call `setup_otel(os.environ.get("ARIZE_PROJECT", "agent-temporal-dev"))` before constructing the Worker.

- [ ] **Step 5: Add `.env.example` entries**

```bash
ARIZE_API_KEY=
ARIZE_SPACE_ID=
ARIZE_PROJECT=agent-temporal-dev
```

- [ ] **Step 6: Run unit test, then end-to-end smoke**

Run: `uv run pytest tests/test_otel.py -v`
Expected: PASS.

End-to-end: run a single PR through the system, open the Arize Space `agent-temporal-dev`, confirm at least one trace tree.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/observability src/worker.py tests/test_otel.py .env.example
git commit -m "feat(observability): OpenInference + Arize OTel wiring

Worker boots OTel + Anthropic instrumentor before the Temporal worker.
Every claude_agent_sdk LLM call lands as a span in Arize Space
agent-temporal-dev."
```

---

## Phase 8 — External Payload Storage (S3)

**Goal:** Payloads > 10 KB spill to S3 transparently, so long agent transcripts can live in Workflow state without hitting the 2 MB Event-History ceiling.

### Task 8.1: PayloadCodec with size threshold

**Files:**
- Create: `src/payload_storage/__init__.py`
- Create: `src/payload_storage/s3_codec.py`
- Create: `tests/test_s3_codec.py`

- [ ] **Step 1: Failing test using `moto` (mock S3)**

```python
# tests/test_s3_codec.py
import boto3
import pytest

from moto import mock_aws

from src.payload_storage.s3_codec import S3PayloadCodec


@pytest.fixture
def bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="agent-temporal-payloads")
        yield "agent-temporal-payloads"


@pytest.mark.asyncio
async def test_small_payload_inline(bucket):
    codec = S3PayloadCodec(bucket=bucket, threshold_bytes=10_000)
    payloads = await codec.encode(["hello"])
    decoded = await codec.decode(payloads)
    assert decoded[0].data == payloads[0].data  # untouched


@pytest.mark.asyncio
async def test_large_payload_spills(bucket):
    codec = S3PayloadCodec(bucket=bucket, threshold_bytes=10)
    big = "x" * 20_000
    payloads = await codec.encode([big])
    assert payloads[0].metadata.get(b"encoding") == b"binary/s3"
    decoded = await codec.decode(payloads)
    import json
    assert json.loads(decoded[0].data) == big
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

```python
# src/payload_storage/s3_codec.py
"""External Payload Storage for Temporal.

Pattern-C hardening checklist: "payloads >10 KB routed to External
Payload Storage (S3 driver)".

The Codec serializes the payload to S3 when it exceeds threshold; the
inline payload carries only the s3:// reference. The reverse path
fetches and reinflates."""
from __future__ import annotations

import json
import uuid
from typing import Iterable, Sequence

import boto3
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec


class S3PayloadCodec(PayloadCodec):
    def __init__(self, *, bucket: str, threshold_bytes: int = 10_000, prefix: str = "payloads"):
        self._bucket = bucket
        self._threshold = threshold_bytes
        self._prefix = prefix
        self._s3 = boto3.client("s3")

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        out = []
        for p in payloads:
            if len(p.data) <= self._threshold:
                out.append(p)
                continue
            key = f"{self._prefix}/{uuid.uuid4().hex}.bin"
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=p.data)
            out.append(
                Payload(
                    metadata={**p.metadata, "encoding": b"binary/s3"},
                    data=json.dumps({"bucket": self._bucket, "key": key}).encode(),
                )
            )
        return out

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        out = []
        for p in payloads:
            if p.metadata.get("encoding") != b"binary/s3":
                out.append(p)
                continue
            ref = json.loads(p.data.decode())
            data = self._s3.get_object(Bucket=ref["bucket"], Key=ref["key"])["Body"].read()
            out.append(Payload(metadata={k: v for k, v in p.metadata.items() if k != "encoding"}, data=data))
        return out
```

- [ ] **Step 4: Run — passes**

- [ ] **Step 5: Wire into worker**

In `src/worker.py`, when constructing the `Client`. The Temporal Python
SDK doesn't provide a `.with_payload_codec()` builder — the documented
pattern is `dataclasses.replace()` on the default DataConverter
(see https://docs.temporal.io/develop/python/converters-and-encryption):

```python
import dataclasses

from temporalio.client import Client
from temporalio.converter import DataConverter

from src.payload_storage.s3_codec import S3PayloadCodec

bucket = os.environ.get("AWS_S3_BUCKET")
if bucket:
    data_converter = dataclasses.replace(
        DataConverter.default, payload_codec=S3PayloadCodec(bucket=bucket)
    )
else:
    data_converter = DataConverter.default

client = await Client.connect(target, data_converter=data_converter)
```

- [ ] **Step 6: Add `minio` (or use AWS for staging) — compose only**

```yaml
  minio:
    image: minio/minio:latest
    command: server /data
    environment:
      - MINIO_ROOT_USER=minio
      - MINIO_ROOT_PASSWORD=miniominio
    ports:
      - "9000:9000"
```

Plus an init container that creates the bucket; or document setup in README.

- [ ] **Step 7: Commit**

```bash
git add src/payload_storage src/worker.py docker-compose.yml tests/test_s3_codec.py pyproject.toml
git commit -m "feat(payload-storage): S3 codec for payloads >10 KB

Implements the External Payload Storage hardening item. Local dev uses
MinIO; AWS S3 in non-local envs."
```

---

## Phase 9 — Worker Versioning + sandbox snapshot/fork

**Goal:** Pin in-flight workflows to their deploy-time worker version (Replay 2026 GA), and snapshot the sandbox periodically so a worker crash can resume from the last snapshot rather than restart the agent.

### Task 9.1: Enable Worker Versioning

**Files:**
- Modify: `src/worker.py`
- Modify: `docker-compose.yml`
- Create: `tests/test_worker_versioning.py`

- [ ] **Step 1: Add a test that asserts the build_id is propagated**

```python
# tests/test_worker_versioning.py
import os

from src.worker import _build_id


def test_build_id_falls_back_to_git_sha(monkeypatch):
    monkeypatch.setenv("WORKER_BUILD_ID", "abc123")
    assert _build_id() == "abc123"


def test_build_id_default(monkeypatch):
    monkeypatch.delenv("WORKER_BUILD_ID", raising=False)
    assert _build_id() == "dev"
```

- [ ] **Step 2: Implement in `src/worker.py`**

```python
def _build_id() -> str:
    return os.environ.get("WORKER_BUILD_ID", "dev")

# When constructing Worker:
async with Worker(
    client,
    task_queue=task_queue,
    workflows=[PRAutofixWorkflow],
    activities=[...],
    activity_executor=activity_executor,
    build_id=_build_id(),
    use_worker_versioning=True,
):
    ...
```

- [ ] **Step 3: docker-compose** — pass `WORKER_BUILD_ID` from `$GITHUB_SHA` or local dev value.

- [ ] **Step 4: Commit**

```bash
git add src/worker.py tests/test_worker_versioning.py docker-compose.yml
git commit -m "feat(worker): enable Worker Versioning (Replay 2026 GA)

In-flight workflows are now pinned to the worker version that started
them. Deploys cannot break mid-run agents."
```

### Task 9.2: Sandbox snapshots every 5 iterations / 2 minutes

**Files:**
- Create: `src/activities/snapshot.py`
- Create: `tests/test_snapshot.py`
- Modify: `src/workflows/pr_autofix.py`
- Modify: `src/models.py` (add `SnapshotRef`)

- [ ] **Step 1: Failing test** — exercise `snapshot_sandbox` returning an S3 ref + `restore_sandbox` re-spawning a container from the snapshot.

(Implementation skeleton: `docker commit container snapshot:wf-id-{iter}` → `docker save | aws s3 cp - s3://bucket/snapshots/wf-id-{iter}.tar.gz`; restore is the reverse.)

- [ ] **Step 2: Implement `provision_sandbox` to optionally restore from a `SnapshotRef`** + add `snapshot_sandbox` activity called inside the iteration loop every 5 iterations or 2 min wall-time (track via `workflow.now()`).

- [ ] **Step 3: Commit**

```bash
git add src/activities/snapshot.py src/models.py src/workflows/pr_autofix.py tests/test_snapshot.py
git commit -m "feat(sandbox): periodic snapshot to S3 + restore on resume"
```

### Task 9.3: GC activity for orphan sandbox containers

**Files:**
- Create: `src/activities/cleanup_orphans.py`
- Create: `scripts/gc_orphans.py` (cron entry)

- [ ] **Step 1: Implement** an activity that lists all running containers matching `autofix-sbx-*`, queries Temporal for workflows with matching ids and `RUNNING` status, and removes the rest.

- [ ] **Step 2: Schedule** as a Temporal Schedule (every 15 min) or as a host-side cron via `scripts/gc_orphans.py`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(ops): GC orphan sandbox containers"
```

---

## Phase 10 — Daily CVE check + final hardening verification

**Goal:** Close the remaining items in the hardening checklist.

### Task 10.1: Daily CVE check activity

**Files:**
- Create: `src/activities/cve_check.py`
- Create: `tests/test_cve_check.py`

- [ ] **Step 1: Test stub** — given a fake advisory feed, the activity returns a list of CVEs that affect `claude-agent-sdk@<pinned>` or `@anthropic-ai/claude-code@<pinned>`.

- [ ] **Step 2: Implement** — fetch [GHSA feed](https://api.github.com/advisories?ecosystem=npm&affects=@anthropic-ai/claude-code) + the Python equivalent. Open an issue on the repo (via GitHub API) when any CVE has CVSS ≥ 7.

- [ ] **Step 3: Schedule** as a Temporal Schedule (daily at 09:00 UTC).

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(security): daily CVE check on pinned SDK versions"
```

### Task 10.2: Hardening checklist verification script

**Files:**
- Create: `scripts/hardening_check.py`

A self-test that reads the production hardening checklist (Common + Temporal-hosted sub-list) from the ADR and asserts each item is in effect:

- `ANTHROPIC_BASE_URL` set
- No sensitive paths mounted into `/work`
- `permissions allowed_tools` matches the ADR allowlist (Read/Edit/Grep/Glob/mcp__*)
- `permission_mode != "bypassPermissions"`
- OTel provider initialized
- S3 PayloadCodec wired if `AWS_S3_BUCKET` is set
- Worker Versioning enabled
- Repo allowlist not empty

CI integration: `pytest tests/test_hardening_e2e.py` invokes the script.

- [ ] **Commit**

```bash
git commit -m "chore: hardening checklist self-test script"
```

---

## Self-Review

**1. Spec coverage** — every Pattern C rule and every Production Hardening Checklist item is mapped:

| Rule / requirement | Phase / Task |
|---|---|
| Rule 1: credentials never in sandbox | Phase 4 (credential proxy + `__token` endpoints) |
| Rule 2: Activity = provision + dispatch + observe + teardown | Phase 5.3 (dispatch_into_sandbox) |
| Rule 3: sandbox lifecycle tied to workflow | already in PoC + Phase 5 inheritance |
| Rule 4: MCP servers outside sandbox by default | Phase 4.3 (proxy-mediated GitHub MCP) |
| Rule 5: OTel exporter outside sandbox | Phase 7 (Worker-side setup) |
| Rule 6: idempotency keys from (wf,iter,tool_use_id) | Phase 5.4 |
| Rule 7: HITL gate position depends on layer | Phase 6 (Update/Signal) + 6.3 (proxy gate) |
| CVE timeline mitigations (pinned base URL, sensitive paths) | Phase 1.3 |
| `options.sandbox` SDK-native | Phase 2.1 |
| `thefork-agent-plugins` shared format | Phase 3 |
| Arize/OpenInference observability | Phase 7 |
| External Payload Storage (S3) | Phase 8 |
| Worker Versioning | Phase 9.1 |
| Sandbox snapshot/fork | Phase 9.2 |
| Repo allowlist | Phase 1.1–1.2 |
| Daily CVE check | Phase 10.1 |

**2. Placeholder scan** — Phases 6.3, 9.2, 9.3, 10.1 contain implementation outlines rather than full code blocks. Reason: each involves either Docker-API streaming idiosyncrasies (snapshot/exec) or Temporal scheduling boilerplate that is verbose and not the architectural point. The plan flags them explicitly as "implementation skeleton" so an executor knows to flesh them out (with the cited references: `docker commit`, `Schedule.create_workflow`, `Schedule.spec`).

**3. Type consistency** — `SandboxHandle`, `FixPlan`, `WorkflowState`, `PRRef` are reused with the existing shapes (verified against `src/models.py`). `ApprovalRequest`/`ApprovalDecision`/`ApprovalState` are introduced in Phase 6.1 and referenced consistently in Phase 6.2.

---

## Open questions to resolve during execution

1. **Forward-proxy library choice** — `mitmproxy` (Python, MITM-capable) vs plain CONNECT tunnel with a `cryptography`-based local CA. The PoC takes the simpler path (CONNECT tunnel only); MitM for header-rewrite on HTTPS is parked as a follow-up.
2. **GitHub App vs PAT for the credential proxy** — the ADR's "short-lived token" pattern wants a GitHub App installation token (1-hour TTL) rather than a static fine-grained PAT. Phase 4 keeps PAT for simplicity but tags the upgrade as a follow-up.
3. **Snapshot storage cost ceiling** — every 5 iterations × N concurrent workflows × image size could grow S3 spend. Phase 9.2 adds an S3 lifecycle policy (delete after 24 h) — confirm with FinOps.
4. **docker-py `exec_start(socket=True)` stream API** — the `sock._sock` / `shutdown(1)` pattern in Phase 5.3 is the historically-working shape but the SDK's low-level socket attribute name has shifted across releases. Pin `docker>=7.0` (already in pyproject) and replace with an explicit `aiohttp` call to `/exec/{id}/start` if compatibility breaks. Confirm with an integration test before relying on it in production.
5. **Temporal `@workflow.update` return semantics** — the decorator supports an optional `.validator` sibling and the handler's return value is the value `WorkflowHandle.execute_update()` resolves to. Confirm the timeout behavior of the wait_condition vs. update timeout (the Update has its own deadline) when wiring HITL — they must be coherent.
6. **External Storage vs PayloadCodec for >2 MB payloads** — Temporal also exposes an `external_storage` slot on the DataConverter for blob spill, distinct from a custom PayloadCodec. The plan uses PayloadCodec because the API is simpler and our threshold (10 KB) is below the 2 MB Event-History ceiling; revisit if multi-MB transcripts become common.
