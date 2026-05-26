"""OpenInference OTel wiring.

Two supported backends, selected at runtime:

1. **Arize Phoenix (self-hosted, local).** When `PHOENIX_COLLECTOR_ENDPOINT`
   is set (e.g. `http://phoenix:6006/v1/traces` from inside docker-compose
   or `http://localhost:6006/v1/traces` from the host), spans go to the
   local Phoenix UI. Zero credentials required.

2. **Arize cloud.** When both `ARIZE_SPACE_ID` and `ARIZE_API_KEY` are
   set, falls back to `arize.otel.register(...)`.

If neither is configured, OTel is silently disabled and `setup_otel`
returns None — local dev without observability still boots.

**Important architectural note.** The Claude Agent SDK runs the agent
loop inside the sandbox container, not in the Worker. This module must
be initialized in the *agent_runner* process to capture
`claude_agent_sdk.query()` and tool-call spans. Calling it from the
Worker process won't surface anything in Phoenix/Arize because the SDK
code there never executes."""
from __future__ import annotations

import logging
import os
from typing import Any


logger = logging.getLogger(__name__)


_PROVIDER: Any = None


def _build_phoenix_provider(project_name: str, endpoint: str):
    from openinference.semconv.resource import ResourceAttributes
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    resource = Resource.create({ResourceAttributes.PROJECT_NAME: project_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    )
    return provider


def _build_arize_provider(project_name: str):
    from arize.otel import register

    return register(
        space_id=os.environ["ARIZE_SPACE_ID"],
        api_key=os.environ["ARIZE_API_KEY"],
        project_name=project_name,
    )


def setup_otel(project_name: str):
    """Initialize the OTel TracerProvider exporting to Phoenix or Arize.

    Idempotent — returns the existing provider on second call. Returns
    None (and logs) if no backend is configured."""
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER

    phoenix_endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
    space_id = os.environ.get("ARIZE_SPACE_ID")
    api_key = os.environ.get("ARIZE_API_KEY")

    if phoenix_endpoint:
        _PROVIDER = _build_phoenix_provider(project_name, phoenix_endpoint)
        backend = f"Phoenix ({phoenix_endpoint})"
    elif space_id and api_key:
        _PROVIDER = _build_arize_provider(project_name)
        backend = "Arize cloud"
    else:
        logger.info(
            "OTel not initialized: no PHOENIX_COLLECTOR_ENDPOINT and "
            "no ARIZE_SPACE_ID / ARIZE_API_KEY"
        )
        return None

    # Deferred to avoid import cost when no backend is configured.
    from openinference.instrumentation.claude_agent_sdk import (
        ClaudeAgentSDKInstrumentor,
    )
    from opentelemetry import trace

    trace.set_tracer_provider(_PROVIDER)
    ClaudeAgentSDKInstrumentor().instrument(tracer_provider=_PROVIDER)
    logger.info("OTel initialized: project=%s backend=%s", project_name, backend)
    return _PROVIDER
