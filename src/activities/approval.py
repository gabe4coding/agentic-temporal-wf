"""HITL notification activity.

For the PoC, posts a comment on the PR with the approval request. In
production this fan-outs to Slack / Backstage / the dedicated approval UI."""
from __future__ import annotations

import os

import httpx
from temporalio import activity

from src.models import ApprovalRequest


def _render_approval_comment(req: ApprovalRequest) -> str:
    return (
        f"**AutoFix needs approval** for `{req.tool_name}` "
        f"(approval_id `{req.approval_id}`).\n\n"
        f"```\n{req.tool_input}\n```\n\n"
        f"Reply with `/autofix approve {req.approval_id}` or "
        f"`/autofix deny {req.approval_id} <reason>` to resolve."
    )


@activity.defn
async def notify_human_for_approval(
    pr_owner: str, pr_repo: str, pr_number: int, req: ApprovalRequest
) -> None:
    token = os.environ["GITHUB_TOKEN"]
    body = _render_approval_comment(req)
    async with httpx.AsyncClient(timeout=30.0) as c:
        response = await c.post(
            f"https://api.github.com/repos/{pr_owner}/{pr_repo}/issues/{pr_number}/comments",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"body": body},
        )
        response.raise_for_status()
