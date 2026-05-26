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

import os
from dataclasses import dataclass
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request


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
