import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.gateway.app import create_app


WEBHOOK_SECRET = "shh"


def _sign(body: bytes) -> str:
    mac = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


@pytest.fixture
def client_and_temporal():
    fake_client = AsyncMock()
    # Default fetcher returns "" so the self-trigger guard is a no-op
    # unless a test wants to exercise it explicitly.
    async def empty_fetcher(owner: str, repo: str, sha: str) -> str:
        return ""

    app = create_app(
        temporal_client=fake_client,
        webhook_secret=WEBHOOK_SECRET,
        fetch_commit_message=empty_fetcher,
    )
    return TestClient(app), fake_client


def test_rejects_bad_signature(client_and_temporal):
    client, _ = client_and_temporal
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "abc",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
        content=b"{}",
    )
    assert r.status_code == 401


def test_drops_unhandled_event_kind(client_and_temporal):
    client, fake = client_and_temporal
    body = b"{}"
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "release",
            "X-GitHub-Delivery": "abc",
            "X-Hub-Signature-256": _sign(body),
        },
        content=body,
    )
    assert r.status_code == 204
    fake.start_workflow.assert_not_called()


def test_pull_request_opened_starts_workflow(client_and_temporal):
    client, fake = client_and_temporal
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 42,
            "head": {"sha": "abc1234", "ref": "feature-x"},
            "base": {"repo": {"owner": {"login": "o"}, "name": "r"}},
        },
        "repository": {"owner": {"login": "o"}, "name": "r"},
    }
    body = json.dumps(payload).encode()
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-1",
            "X-Hub-Signature-256": _sign(body),
        },
        content=body,
    )
    assert r.status_code == 202
    fake.start_workflow.assert_awaited_once()
    kwargs = fake.start_workflow.call_args.kwargs
    assert kwargs["id"] == "pr-autofix-o-r-42"
    assert kwargs["start_signal"] == "on_event"


def test_pr_synchronize_from_autofix_bot_is_dropped():
    """Self-trigger guard: synchronize whose head commit message contains
    the AUTOFIX_COMMIT_TRAILER must not start a new workflow."""
    fake_client = AsyncMock()
    from src.tools._local_repo_impl import AUTOFIX_COMMIT_TRAILER

    async def autofix_fetcher(owner: str, repo: str, sha: str) -> str:
        return f"fix: something\n\n{AUTOFIX_COMMIT_TRAILER}"

    app = create_app(
        temporal_client=fake_client,
        webhook_secret=WEBHOOK_SECRET,
        fetch_commit_message=autofix_fetcher,
    )
    client = TestClient(app)
    payload = {
        "action": "synchronize",
        "pull_request": {
            "number": 7,
            "head": {"sha": "deadbeef", "ref": "feature-x"},
            "base": {"repo": {"owner": {"login": "o"}, "name": "r"}},
        },
        "repository": {"owner": {"login": "o"}, "name": "r"},
    }
    body = json.dumps(payload).encode()
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d-self",
            "X-Hub-Signature-256": _sign(body),
        },
        content=body,
    )
    assert r.status_code == 204
    fake_client.start_workflow.assert_not_called()


def test_pr_synchronize_from_human_still_starts_workflow():
    """Self-trigger guard must not drop legitimate human-pushed syncs."""
    fake_client = AsyncMock()

    async def human_fetcher(owner: str, repo: str, sha: str) -> str:
        return "fix: real human change"

    app = create_app(
        temporal_client=fake_client,
        webhook_secret=WEBHOOK_SECRET,
        fetch_commit_message=human_fetcher,
    )
    client = TestClient(app)
    payload = {
        "action": "synchronize",
        "pull_request": {
            "number": 7,
            "head": {"sha": "beefcafe", "ref": "feature-x"},
            "base": {"repo": {"owner": {"login": "o"}, "name": "r"}},
        },
        "repository": {"owner": {"login": "o"}, "name": "r"},
    }
    body = json.dumps(payload).encode()
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d-human",
            "X-Hub-Signature-256": _sign(body),
        },
        content=body,
    )
    assert r.status_code == 202
    fake_client.start_workflow.assert_awaited_once()


def test_issue_comment_on_pr_is_dropped(client_and_temporal):
    """PoC limitation: issue_comment payload doesn't include head SHA, drop it."""
    client, fake = client_and_temporal
    payload = {
        "action": "created",
        "issue": {"number": 7, "pull_request": {"url": "..."}},
        "comment": {"body": "please fix"},
        "repository": {"owner": {"login": "o"}, "name": "r"},
    }
    import json
    body = json.dumps(payload).encode()
    import hmac, hashlib
    sig = "sha256=" + hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/webhook",
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-GitHub-Delivery": "d",
            "X-Hub-Signature-256": sig,
        },
        content=body,
    )
    assert r.status_code == 204
    fake.start_workflow.assert_not_called()
