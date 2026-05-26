FROM python:3.12-slim

ARG CLAUDE_CODE_VERSION=2.1.150

# Pattern-C: the sandbox image carries the Claude Code CLI, the Python
# Agent SDK, the agent_runner entrypoint and the org plugin bundle. It
# does NOT carry the worker code or any TheFork credentials — those
# live in the trusted Worker process. The hard isolation boundary
# (cap_drop=ALL + no-new-privileges) is applied at container creation
# by provision_sandbox, not by the in-container uid.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates curl \
    bubblewrap socat \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# bubblewrap + socat are required by ClaudeAgentOptions.sandbox to
# activate filesystem/network isolation at the L1/L2 layer (Dec 2025
# SDK). Without them the SDK silently degrades to "no sandbox" — Docker
# remains the only boundary (L0).

# Pin Claude Code CLI to a post-CVE-2025-66479 release.
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

RUN pip install --no-cache-dir \
    ruff pytest claude-agent-sdk pydantic httpx \
    openinference-instrumentation-claude-agent-sdk \
    opentelemetry-sdk opentelemetry-exporter-otlp

# agent_runner module + the modules it imports (models, tools, agents).
# COPY at /opt and add to PYTHONPATH so `python -m src.agent_runner.main`
# resolves the package without needing the full worker codebase.
COPY src/__init__.py /opt/src/__init__.py
COPY src/agent_runner /opt/src/agent_runner
COPY src/agents /opt/src/agents
COPY src/models.py /opt/src/models.py
COPY src/tools /opt/src/tools
COPY src/observability /opt/src/observability
COPY plugins /plugins

# /work is the per-workflow workdir mounted from the host's
# /tmp/autofix-{workflow_id}/repo path (via the Worker's volumes_from).
WORKDIR /work

ENV PYTHONPATH=/opt
