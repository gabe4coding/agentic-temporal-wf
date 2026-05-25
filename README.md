# agent-temporal — PR Autofix PoC

PoC of an agentic workflow on **Temporal + Pydantic AI** that auto-fixes
GitHub Pull Requests (review comments, lint, tests).

See [design doc](docs/superpowers/specs/2026-05-25-temporal-pydantic-pr-autofix-design.md).

## Prerequisites

- Docker + docker-compose
- A GitHub fine-grained PAT with PR read/write + checks write on a playground repo
- An Anthropic API key
- A way to expose `localhost:8000` to GitHub (smee.io or cloudflared)

## Local run

1. `cp .env.example .env` and fill in the values.
2. `docker compose up --build`
3. Temporal UI: <http://localhost:8233>
4. Gateway: <http://localhost:8000/webhook>

## Wiring the webhook

Two easy options:

### Option A — smee.io

```bash
npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8000/webhook
```

Then in your playground repo, add a webhook pointing at the smee URL,
content type `application/json`, secret = `GITHUB_WEBHOOK_SECRET` from `.env`,
events: PR, Issue comments, PR review comments, Check suites.

### Option B — cloudflared

```bash
cloudflared tunnel --url http://localhost:8000
```

Same webhook configuration, target = the cloudflared URL.

## Manual smoke test

1. Open a PR on your playground repo with a deliberate lint violation
   (e.g. `import os` unused).
2. The gateway receives `pull_request.opened`, starts the workflow.
3. The worker logs show the agent iteration; the PR gets a status comment.
4. Check the Temporal UI to inspect the workflow run.

## Running tests

```bash
uv run pytest -v
```

## Limitations (PoC)

- Single Python repo; not language-agnostic.
- We run pytest inside the worker container — fine for a playground, not for
  arbitrary code.
- One rolling status comment per PR, no auto-merge.
- No observability beyond worker logs + Temporal UI.
