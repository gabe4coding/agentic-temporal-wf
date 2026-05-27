# agent-temporal - PR Autofix Platform PoC

This repository demonstrates one agent workload, PR autofix, on a reusable
Temporal-controlled execution boundary. The PR adapter receives GitHub
events, runs an agent against a workspace in an untrusted per-run sandbox,
and publishes changes only after human approval.

See the [architecture spec](pattern-c-architecture-spec.html) and the
[platform contract](docs/pattern-c-platform.md).

## Trust Boundary

- Temporal and the worker are trusted control-plane components.
- Each sandbox receives one writable mount, `/work`, containing only its run
  workspace. Repository tests execute there, including arbitrary PR code.
- A sandbox receives an opaque `RUN_CAPABILITY_TOKEN`, not a GitHub PAT or a
  real Anthropic API key.
- The trusted capability broker exposes a scoped remote MCP surface and an
  Anthropic relay. It resolves the token to the workflow-bound PR.
- `push_changes` is the sole approval-gated side effect. The trusted worker
  commits and pushes to the branch held in workflow state, not a sandbox
  supplied remote.

The PoC uses a fine-grained GitHub PAT in trusted services. A production
deployment should replace this with a GitHub App installation token.

## Configuration

Create `.env` from `.env.example` and configure:

- `GITHUB_TOKEN` and `ANTHROPIC_API_KEY`: trusted broker/worker credentials.
- `GITHUB_WEBHOOK_SECRET`: webhook signature verification.
- `ALLOWED_REPOS`: comma-separated `owner/repo` allowlist.
- `APPROVER_LOGINS`: comma-separated GitHub users permitted to approve pushes.
- `WORKSPACE_ROOT`: absolute host-visible path for trusted run workspaces.
- `BROKER_REGISTRATION_SECRET`: secret shared by worker and broker only.

Sandbox runtime settings are generated during provisioning:

```text
RUN_CAPABILITY_TOKEN=<opaque short-lived token>
ANTHROPIC_BASE_URL=http://capability-broker:8443/anthropic
CAPABILITY_MCP_URL=http://capability-broker:8443/mcp
```

## Local Run

```bash
cp .env.example .env
docker compose up --build
```

Temporal UI is at <http://localhost:8233>; the webhook gateway listens at
<http://localhost:8000/webhook>.

The Compose stack mounts `temporal-dynamicconfig.yaml`, which enables
Workflow Updates used by the approval gate. Without that setting,
`request_push_changes` cannot enter the pending-approval state.

Configure a GitHub webhook for pull requests, issue comments, pull request
review comments, and check suites, using `GITHUB_WEBHOOK_SECRET`. Issue
comments carry `/autofix approve` and `/autofix deny` commands; review
comments provide actionable agent input.

## Demo Flow

1. Open a PR in an allowlisted playground repository with a fixable failure.
2. The webhook starts `PRAutofixWorkflow`, which prepares a run workspace and
   provisions the `/work` sandbox.
3. The agent reads PR context through broker MCP, edits locally, and runs
   applicable local validation tools inside the sandbox.
4. The agent requests publication. The workflow posts an approval request.
5. An authorized reviewer comments `/autofix approve <approval_id>` or
   `/autofix deny <approval_id> <reason>`.
6. After approval, a trusted idempotent activity creates and pushes one
   commit stamped with `Autofix-Idempotency: <operation_key>`, and the
   workflow posts status.

## Real PR E2E

Start the stack and trigger an existing PR:

```bash
docker compose up -d --build --remove-orphans
uv run python scripts/trigger_webhook.py https://github.com/owner/repo/pull/123
```

For a publication-path test, add an actionable pull request review comment.
When the workflow posts an approval request, reply on the PR:

```text
/autofix approve <approval_id>
```

Expected outcome:

- The sandbox has one bind mount at `/work` and no Docker socket.
- The sandbox has `RUN_CAPABILITY_TOKEN`, but no `GITHUB_TOKEN` or real
  `ANTHROPIC_API_KEY`.
- The approved branch receives one commit with its deterministic
  `Autofix-Idempotency` trailer.
- Repeating the same publication request returns the recorded commit result
  without producing a second commit.

## Tests

```bash
uv run pytest -q
uv run ruff check src scripts tests
uv run python -m compileall -q src scripts tests
docker compose config --quiet
```

Docker smoke/integration tests are marked `integration` and require a built
sandbox image plus the compose services.

## Limitations

- PR autofix is the only implemented workload adapter.
- The broker capability registry is in memory for this PoC.
- GitHub authentication uses a trusted-service PAT rather than a GitHub App.
- The local Temporal service requires Workflow Updates enabled by
  `temporal-dynamicconfig.yaml`; managed deployments must enable the
  equivalent namespace/server capability.
- No automatic merge is performed.
