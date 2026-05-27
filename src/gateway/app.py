import hashlib
import hmac
import os
import re
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from temporalio.common import WorkflowIDReusePolicy

from src.models import GitHubEvent, PRRef
from src.repo_allowlist import RepoAllowlist, RepoDenied
from src.activities.lifecycle import AUTOFIX_COMMIT_TRAILER
from src.workflows.pr_autofix import PRAutofixWorkflow


CommitMessageFetcher = Callable[[str, str, str], Awaitable[str]]
_APPROVAL_COMMAND = re.compile(
    r"^\s*/autofix\s+(approve|deny)\s+([a-f0-9]{16,64})(?:\s+(.+))?\s*$",
    re.IGNORECASE,
)


async def _fetch_commit_message(owner: str, repo: str, sha: str) -> str:
    """Default GitHub API fetcher for a commit's full message. Returns ""
    on any failure (auth missing, 404, network) — callers treat empty as
    'unable to determine, fall through to normal processing'."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token or not sha:
        return ""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}",
                headers=headers,
            )
            if r.status_code != 200:
                return ""
            return r.json().get("commit", {}).get("message", "") or ""
    except (httpx.HTTPError, ValueError):
        return ""


def _verify(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, signature)


def _project_event(
    event: str, payload: dict, delivery_id: str
) -> tuple[PRRef, GitHubEvent] | None:
    if event == "pull_request":
        action = payload.get("action")
        kind = {"opened": "pr_opened", "synchronize": "pr_synchronize"}.get(action)
        if not kind:
            return None
        pr_node = payload["pull_request"]
        pr = PRRef(
            owner=payload["repository"]["owner"]["login"],
            repo=payload["repository"]["name"],
            number=pr_node["number"],
            head_sha=pr_node["head"]["sha"],
            head_ref=pr_node["head"]["ref"],
        )
    elif event in ("issue_comment", "pull_request_review_comment", "check_suite"):
        # PoC: extract minimal PR identity if present
        if event == "issue_comment":
            issue = payload.get("issue", {})
            if "pull_request" not in issue:
                return None
            # GitHub's issue_comment payload does not include the PR head SHA/ref.
            # For the PoC we drop these events at the gateway and rely on
            # pull_request.synchronize to (re)deliver the head info. The agent will
            # also re-read PR state via the GitHub MCP toolset on each iteration.
            return None
        elif event == "pull_request_review_comment":
            pr_node = payload.get("pull_request", {})
            kind = "review_comment"
            pr = PRRef(
                owner=payload["repository"]["owner"]["login"],
                repo=payload["repository"]["name"],
                number=pr_node["number"],
                head_sha=pr_node["head"]["sha"],
                head_ref=pr_node["head"]["ref"],
            )
        else:  # check_suite
            action = payload.get("action")
            if action != "completed":
                return None
            kind = "check_suite_completed"
            cs = payload["check_suite"]
            prs = cs.get("pull_requests") or []
            if not prs:
                return None
            pr_node = prs[0]
            pr = PRRef(
                owner=payload["repository"]["owner"]["login"],
                repo=payload["repository"]["name"],
                number=pr_node["number"],
                head_sha=cs["head_sha"],
                head_ref=cs["head_branch"],
            )
    else:
        return None

    return pr, GitHubEvent(kind=kind, delivery_id=delivery_id, payload=payload)


def create_app(
    *,
    temporal_client: Any,
    webhook_secret: str,
    task_queue: str = "pr-autofix",
    fetch_commit_message: CommitMessageFetcher = _fetch_commit_message,
) -> FastAPI:
    app = FastAPI(title="PR Autofix Gateway")

    @app.post("/webhook")
    async def webhook(
        request: Request,
        x_github_event: str = Header(...),
        x_github_delivery: str = Header(...),
        x_hub_signature_256: str | None = Header(default=None),
    ):
        body = await request.body()
        if not _verify(webhook_secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="bad signature")

        import json as _json
        payload = _json.loads(body) if body else {}

        if x_github_event == "issue_comment" and payload.get("action") == "created":
            issue = payload.get("issue") or {}
            command = _APPROVAL_COMMAND.match((payload.get("comment") or {}).get("body", ""))
            if command and "pull_request" in issue:
                approvers = {
                    value.strip()
                    for value in os.environ.get("APPROVER_LOGINS", "").split(",")
                    if value.strip()
                }
                login = ((payload.get("comment") or {}).get("user") or {}).get("login", "")
                if login not in approvers:
                    raise HTTPException(status_code=403, detail="approval author not authorized")
                owner = payload["repository"]["owner"]["login"]
                repo = payload["repository"]["name"]
                try:
                    RepoAllowlist.from_env().check(owner, repo)
                except RepoDenied as e:
                    raise HTTPException(status_code=403, detail=str(e))
                action, approval_id, reason = command.groups()
                handle = temporal_client.get_workflow_handle(
                    f"pr-autofix-{owner}-{repo}-{issue['number']}"
                )
                await handle.signal(
                    "submit_approval_decision",
                    {
                        "approval_id": approval_id,
                        "allowed": action.lower() == "approve",
                        "reason": reason or "",
                    },
                )
                return Response(status_code=202)
        projected = _project_event(x_github_event, payload, x_github_delivery)
        if projected is None:
            return Response(status_code=204)

        pr, event = projected

        try:
            RepoAllowlist.from_env().check(pr.owner, pr.repo)
        except RepoDenied as e:
            raise HTTPException(status_code=403, detail=str(e))

        # Self-trigger guard: a pull_request.synchronize event whose head
        # commit was authored by the autofix bot itself would otherwise
        # spawn a fresh workflow that has nothing new to fix and just
        # posts another "no_action_needed" status. We detect by the
        # AUTOFIX_COMMIT_TRAILER stamped into the commit message.
        if event.kind == "pr_synchronize":
            head_message = await fetch_commit_message(pr.owner, pr.repo, pr.head_sha)
            if AUTOFIX_COMMIT_TRAILER in head_message:
                return Response(status_code=204)

        wf_id = f"pr-autofix-{pr.owner}-{pr.repo}-{pr.number}"
        await temporal_client.start_workflow(
            PRAutofixWorkflow.run,
            pr,
            id=wf_id,
            task_queue=task_queue,
            start_signal="on_event",
            start_signal_args=[event],
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        return Response(status_code=202)

    return app


def build_default_app() -> FastAPI:
    """Entry point for uvicorn: builds the real client lazily."""
    import asyncio
    from temporalio.client import Client

    async def _client() -> Client:
        return await Client.connect(os.environ.get("TEMPORAL_TARGET", "localhost:7233"))

    secret = os.environ["GITHUB_WEBHOOK_SECRET"]
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "pr-autofix")
    client = asyncio.run(_client())
    return create_app(
        temporal_client=client, webhook_secret=secret, task_queue=task_queue
    )


app = build_default_app() if os.environ.get("GATEWAY_BOOT") == "1" else None  # uvicorn imports this
