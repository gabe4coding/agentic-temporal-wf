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
    proxy = os.environ.get("CREDENTIAL_PROXY_URL", "http://credential-proxy:8443")
    async with httpx.AsyncClient(timeout=5.0) as c:
        token_resp = await c.get(f"{proxy}/__token/github")
        token_resp.raise_for_status()
        token = token_resp.json()["token"]
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
