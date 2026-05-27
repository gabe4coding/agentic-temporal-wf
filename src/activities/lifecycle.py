import os
import shutil
import subprocess
from pathlib import Path

import httpx
from temporalio import activity

from src.models import FixPlan, PRRef, WorkflowState
from src.tools._workdir import workdir_root


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


# Local bot identity for autofix commits. Repo-scoped (not --global) so it
# can't leak into other tools the worker might invoke. Uses Anthropic's
# canonical noreply address so commits are recognizably from Claude.
_AUTOFIX_BOT_NAME = "Claude"
_AUTOFIX_BOT_EMAIL = "noreply@anthropic.com"


def _prepare_workdir_at(
    *, target: Path, clone_url: str, head_ref: str, head_sha: str
) -> None:
    """Idempotent clone-or-fetch + repo-local git identity for the bot.

    Optimised for fast cold starts:
      - --depth=1               : no history, just the tip
      - --single-branch         : only fetch refs for `head_ref`
      - --branch <head_ref>     : land directly on the PR branch
      - --filter=blob:none      : partial clone, fetch blobs on demand
                                  (huge win on Go/Node monorepos)

    Falls back to a normal shallow clone if the PR comes from a fork
    (head_ref not on the target repo). Then the explicit fetch/reset
    below pulls the right tip.

    Sets user.name / user.email at the repo level so `git commit` from
    the repo MCP tool succeeds. Without this, `git commit` refuses with
    "Author identity unknown" inside a fresh container.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").is_dir():
        target.mkdir(exist_ok=True)
        fast = subprocess.run(
            [
                "git", "clone",
                "--depth=1",
                "--single-branch",
                "--branch", head_ref,
                "--filter=blob:none",
                clone_url, ".",
            ],
            cwd=target, capture_output=True, check=False,
        )
        if fast.returncode != 0:
            # Fallback for forks / missing branch: minimal shallow clone
            # of the default branch; the fetch below will pick up the PR.
            _run(
                ["git", "clone", "--depth=1", "--filter=blob:none",
                 clone_url, "."],
                target,
            )
    _run(["git", "config", "user.name", _AUTOFIX_BOT_NAME], target)
    _run(["git", "config", "user.email", _AUTOFIX_BOT_EMAIL], target)
    _run(["git", "fetch", "--depth=1", "origin", head_ref], target)
    # Hard-reset the working tree before checkout so leftover uncommitted
    # edits from a previous failed iteration don't make checkout refuse.
    # (The volume persists across worker restarts; without this reset, a
    # prior iteration that failed after applying edits leaves the workdir
    # dirty and the next prepare_workdir would crash on checkout.)
    _run(["git", "reset", "--hard", "FETCH_HEAD"], target)
    _run(["git", "checkout", "-B", head_ref, "FETCH_HEAD"], target)


def _cleanup_workdir_at(workdir_parent: Path) -> None:
    if workdir_parent.exists():
        shutil.rmtree(workdir_parent)


def _clone_url(pr: PRRef) -> str:
    token = os.environ["GITHUB_TOKEN"]
    return f"https://x-access-token:{token}@github.com/{pr.owner}/{pr.repo}.git"


@activity.defn
def prepare_workdir(pr: PRRef) -> None:
    workflow_id = activity.info().workflow_id
    target = workdir_root(workflow_id)
    _prepare_workdir_at(
        target=target,
        clone_url=_clone_url(pr),
        head_ref=pr.head_ref,
        head_sha=pr.head_sha,
    )


@activity.defn
def cleanup_workdir(pr: PRRef) -> None:
    workflow_id = activity.info().workflow_id
    workdir_parent = workdir_root(workflow_id).parent
    _cleanup_workdir_at(workdir_parent)


@activity.defn
async def post_status(state: WorkflowState, plan: FixPlan) -> WorkflowState:
    """Update (or create) the status comment and Check Run on the PR.

    Returns the updated state with comment/check_run ids filled in.
    """
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    pr = state.pr
    body = _render_status_markdown(state, plan)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Comment: create or update in place
        if state.posted_status_comment_id is None:
            r = await client.post(
                f"https://api.github.com/repos/{pr.owner}/{pr.repo}/issues/{pr.number}/comments",
                headers=headers,
                json={"body": body},
            )
            r.raise_for_status()
            state.posted_status_comment_id = r.json()["id"]
        else:
            r = await client.patch(
                f"https://api.github.com/repos/{pr.owner}/{pr.repo}/issues/comments/{state.posted_status_comment_id}",
                headers=headers,
                json={"body": body},
            )
            r.raise_for_status()

        # Check Run: create new (best-effort).
        #
        # GitHub's check-runs API requires a GitHub App identity — PATs
        # (classic or fine-grained) get a 403 here. The status comment
        # above is the primary feedback channel; the check run is bonus
        # for installations that authenticate via App. Swallow common
        # failures so the activity completes and the workflow makes
        # progress on the comment side.
        conclusion = {
            "applied_fix": "success",
            "no_action_needed": "neutral",
            "blocked": "failure",
        }[plan.action]
        try:
            r = await client.post(
                f"https://api.github.com/repos/{pr.owner}/{pr.repo}/check-runs",
                headers=headers,
                json={
                    "name": "AutoFix",
                    "head_sha": pr.head_sha,
                    "status": "completed",
                    "conclusion": conclusion,
                    "output": {"title": "AutoFix", "summary": plan.summary},
                },
            )
            if r.status_code == 201:
                state.last_check_run_id = r.json()["id"]
            elif r.status_code in (403, 404, 422):
                # Auth identity can't write checks, or repo doesn't support
                # them — silently skip. Leave last_check_run_id unset.
                pass
            else:
                r.raise_for_status()
        except httpx.HTTPError:
            # Network / parse failures shouldn't fail the whole iteration.
            pass

    return state


def _render_status_markdown(state: WorkflowState, plan: FixPlan) -> str:
    lines = [
        f"### 🤖 AutoFix — iteration {state.iterations}",
        f"**Action:** `{plan.action}`",
        "",
        plan.summary,
    ]
    if plan.commit_sha:
        lines += ["", f"Commit: `{plan.commit_sha[:7]}`"]
    if plan.blocking_reason:
        lines += ["", f"**Blocked because:** {plan.blocking_reason}"]
    return "\n".join(lines)
