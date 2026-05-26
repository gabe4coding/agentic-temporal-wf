FROM python:3.12-slim

ARG CLAUDE_CODE_VERSION=2.0.65

# Pattern-C: the sandbox image carries the Claude Code CLI, the Python
# Agent SDK, the agent_runner entrypoint and the org plugin bundle. It
# does NOT carry the worker code or any TheFork credentials — those
# live in the trusted Worker process. The hard isolation boundary
# (cap_drop=ALL + no-new-privileges) is applied at container creation
# by provision_sandbox, not by the in-container uid.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Pin Claude Code CLI to a post-CVE-2025-66479 release.
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

RUN pip install --no-cache-dir \
    ruff pytest claude-agent-sdk pydantic httpx

# agent_runner module + the modules it imports (models, tools, agents).
# COPY at /opt and add to PYTHONPATH so `python -m src.agent_runner.main`
# resolves the package without needing the full worker codebase.
COPY src/__init__.py /opt/src/__init__.py
COPY src/agent_runner /opt/src/agent_runner
COPY src/agents /opt/src/agents
COPY src/models.py /opt/src/models.py
COPY src/tools /opt/src/tools
COPY plugins /plugins

# /work is the per-workflow workdir mounted from the host's
# /tmp/autofix-{workflow_id}/repo path (via the Worker's volumes_from).
WORKDIR /work

ENV PYTHONPATH=/opt
