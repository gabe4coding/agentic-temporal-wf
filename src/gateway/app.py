import hashlib
import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from temporalio.common import WorkflowIDReusePolicy

from src.models import GitHubEvent, PRRef
from src.workflows.pr_autofix import PRAutofixWorkflow


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
            kind = "issue_comment"
            pr = PRRef(
                owner=payload["repository"]["owner"]["login"],
                repo=payload["repository"]["name"],
                number=issue["number"],
                head_sha=payload.get("pull_request", {}).get("head", {}).get("sha", ""),
                head_ref=payload.get("pull_request", {}).get("head", {}).get("ref", ""),
            )
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
    *, temporal_client: Any, webhook_secret: str, task_queue: str = "pr-autofix"
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
        projected = _project_event(x_github_event, payload, x_github_delivery)
        if projected is None:
            return Response(status_code=204)

        pr, event = projected
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
