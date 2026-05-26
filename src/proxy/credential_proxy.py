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
exercised by tests/test_credential_proxy_forward.py (Docker integration,
Phase 4.2).
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request


logger = logging.getLogger(__name__)


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


# ---------- HITL gate (Pattern-C rule 7) ----------
#
# Routes that change real state and therefore require a human approval
# before the proxy forwards them. Patterns are matched against
# {method} {host}{path}. Keep the list tight — every entry is one human
# interrupt per call.
_GATED_ROUTES: list[re.Pattern[str]] = [
    # GitHub: pushing to a ref, deleting a ref, creating a release.
    re.compile(r"^POST api\.github\.com/repos/[^/]+/[^/]+/git/refs$"),
    re.compile(r"^DELETE api\.github\.com/repos/[^/]+/[^/]+/git/refs/.+$"),
    re.compile(r"^POST api\.github\.com/repos/[^/]+/[^/]+/releases$"),
    # Branch protection / collaborators / settings.
    re.compile(
        r"^PUT api\.github\.com/repos/[^/]+/[^/]+/branches/[^/]+/protection$"
    ),
    re.compile(r"^PUT api\.github\.com/repos/[^/]+/[^/]+/collaborators/.+$"),
]


def gated_route_matches(method: str, host: str, path: str) -> bool:
    """Return True iff (method, host, path) is on the HITL gate list."""
    line = f"{method.upper()} {host.lower()}{path}"
    return any(p.search(line) for p in _GATED_ROUTES)


async def _request_approval(
    workflow_id: str,
    *,
    method: str,
    host: str,
    path: str,
    temporal_target: str | None = None,
):
    """Issue the Workflow Update for HITL approval and return the
    decision.

    The Temporal client is imported lazily so unit tests that don't go
    through this path don't pay the import cost. If
    `temporal_target` is None, falls back to the env var
    `TEMPORAL_TARGET`. Returns the ApprovalDecision-shaped dict the
    workflow handler resolves to."""
    from temporalio.client import Client

    from src.models import ApprovalRequest

    target = temporal_target or os.environ["TEMPORAL_TARGET"]
    client = await Client.connect(target)
    handle = client.get_workflow_handle(workflow_id)
    req = ApprovalRequest(
        approval_id=uuid.uuid4().hex,
        tool_name=f"{method.upper()} {host}{path}",
        tool_input={"host": host, "path": path, "method": method.upper()},
        iteration=0,
    )
    decision = await handle.execute_update("request_tool_approval", req)
    return decision


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

    @app.post("/__forward")
    async def forward(req: Request):
        """Stub forward endpoint exercising the gate.

        Real HTTPS CONNECT tunneling is Open Question #1. For HITL, the
        path that matters is: extract (method, host, path) from the
        request body, check `gated_route_matches`, and if it gates,
        block on the Workflow Update before forwarding. The body is a
        JSON object: `{host, method, path, workflow_id}`."""
        body = await req.json()
        host = (body.get("host") or "").lower()
        method = (body.get("method") or "GET").upper()
        path = body.get("path") or "/"
        wf_id = body.get("workflow_id")
        if host not in allowed and not any(host.endswith(f".{a}") for a in allowed):
            raise HTTPException(status_code=403, detail=f"host {host} not allowed")
        if gated_route_matches(method, host, path):
            if not wf_id:
                raise HTTPException(
                    status_code=428,
                    detail="gated route requires X-TheFork-Workflow-Id (workflow_id)",
                )
            decision = await _request_approval(
                wf_id, method=method, host=host, path=path
            )
            if not getattr(decision, "allowed", False):
                raise HTTPException(
                    status_code=403,
                    detail=f"denied by human: {getattr(decision, 'reason', '')}",
                )
        return {
            "would_forward": True,
            "host": host,
            "method": method,
            "path": path,
            "injected": _injection_for(
                host, github_token=github_token, anthropic_key=anthropic_key
            ),
        }

    @app.get("/__token/{name}")
    async def token(name: str):
        """Per-iteration short-lived credential fetch.

        The sandbox calls this endpoint at iteration start; the secret
        lives only in the github-mcp-server child process's env, reaped
        at iteration end. Static PAT today; GitHub App installation
        token (1h TTL) is the documented follow-up (Open Question #2).
        """
        if name == "github":
            return {"token": github_token, "ttl_s": 600}
        if name == "anthropic":
            return {"token": anthropic_key, "ttl_s": 600}
        raise HTTPException(status_code=404, detail=f"unknown credential {name!r}")

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
