"""Trigger the autofix gateway by simulating a GitHub `pull_request.opened`
webhook for an existing PR.

Usage:
    uv run python scripts/trigger_webhook.py <pr-url>

Where <pr-url> is https://github.com/<owner>/<repo>/pull/<number>.

Reads GITHUB_TOKEN + GITHUB_WEBHOOK_SECRET from the local .env. Fetches
the live PR state from GitHub, builds a minimal pull_request payload
that satisfies _project_event(), signs it with the webhook secret, and
posts to http://localhost:8000/webhook.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx


def load_env(env_file: Path) -> None:
    """Tiny .env loader — no dependency on python-dotenv."""
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise SystemExit(f"not a PR url: {url!r}")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_pr(owner: str, repo: str, number: int, token: str) -> dict:
    r = httpx.get(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10.0,
    )
    if r.status_code != 200:
        raise SystemExit(
            f"GitHub API {r.status_code} for /repos/{owner}/{repo}/pulls/{number}: "
            f"{r.text[:200]}"
        )
    return r.json()


def build_payload(pr: dict) -> dict:
    """Minimal pull_request webhook payload that _project_event() accepts."""
    return {
        "action": "opened",
        "pull_request": {
            "number": pr["number"],
            "head": {
                "sha": pr["head"]["sha"],
                "ref": pr["head"]["ref"],
            },
        },
        "repository": {
            "name": pr["base"]["repo"]["name"],
            "owner": {"login": pr["base"]["repo"]["owner"]["login"]},
        },
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <pr-url>")

    load_env(Path(__file__).resolve().parent.parent / ".env")
    token = os.environ.get("GITHUB_TOKEN")
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not token or not secret:
        raise SystemExit("GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET must be set in .env")

    owner, repo, number = parse_pr_url(sys.argv[1])
    print(f"→ fetching PR state: {owner}/{repo}#{number}")
    pr = fetch_pr(owner, repo, number, token)
    print(f"  head: {pr['head']['ref']} @ {pr['head']['sha'][:7]}")

    payload = build_payload(pr)
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    delivery = str(uuid.uuid4())

    print(f"→ POST http://localhost:8000/webhook (delivery={delivery[:8]})")
    r = httpx.post(
        "http://localhost:8000/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig,
        },
        timeout=10.0,
    )
    print(f"← {r.status_code} {r.text or '(empty)'}")
    if r.status_code != 202:
        raise SystemExit(1)
    wf_id = f"pr-autofix-{owner}-{repo}-{number}"
    print(f"\nworkflow id: {wf_id}")
    print(f"temporal UI:  http://localhost:8233/namespaces/default/workflows/{wf_id}")


if __name__ == "__main__":
    main()
