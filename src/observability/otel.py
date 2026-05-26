"""OpenInference + Arize OTel wiring.

Loaded once at worker boot. Claude Agent SDK calls (both `query()` and
`ClaudeSDKClient` sessions) are auto-instrumented as AGENT spans, with
tool calls becoming TOOL child spans.

Upstream signatures verified against:
- arize-otel: arize.otel.register(space_id, api_key, project_name) -> TracerProvider
- openinference-instrumentation-claude-agent-sdk:
  ClaudeAgentSDKInstrumentor().instrument(tracer_provider=...)
"""
from __future__ import annotations

import logging
import os
from typing import Any


logger = logging.getLogger(__name__)


_PROVIDER: Any = None


def setup_otel(project_name: str):
    """Initialize the OTel TracerProvider exporting to Arize.

    Idempotent — returns the existing provider on second call. Returns
    None (and logs) if `ARIZE_SPACE_ID` / `ARIZE_API_KEY` are missing,
    so local dev without Arize credentials still boots."""
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    space_id = os.environ.get("ARIZE_SPACE_ID")
    api_key = os.environ.get("ARIZE_API_KEY")
    if not space_id or not api_key:
        logger.info(
            "OTel not initialized: ARIZE_SPACE_ID / ARIZE_API_KEY not set"
        )
        return None
    # Imports deferred so test-time collection doesn't pay the cost.
    from arize.otel import register
    from openinference.instrumentation.claude_agent_sdk import (
        ClaudeAgentSDKInstrumentor,
    )
    from opentelemetry import trace

    _PROVIDER = register(
        space_id=space_id,
        api_key=api_key,
        project_name=project_name,
    )
    trace.set_tracer_provider(_PROVIDER)
    ClaudeAgentSDKInstrumentor().instrument(tracer_provider=_PROVIDER)
    logger.info("OTel initialized: project=%s", project_name)
    return _PROVIDER
