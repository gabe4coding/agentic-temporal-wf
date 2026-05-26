FROM python:3.12-slim

ARG TARGETARCH
ARG GITHUB_MCP_VERSION=v1.0.5

# System deps: git (clone/push), curl + ca (binary download), node (claude CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI — the claude-agent-sdk Python package shells out to this.
RUN npm install -g @anthropic-ai/claude-code

# Install the official GitHub MCP server (Go binary)
RUN case "${TARGETARCH:-amd64}" in \
        amd64) MCP_ARCH=x86_64 ;; \
        arm64) MCP_ARCH=arm64 ;; \
        *) MCP_ARCH=x86_64 ;; \
    esac \
    && curl -fsSL -o /tmp/mcp.tar.gz \
        "https://github.com/github/github-mcp-server/releases/download/${GITHUB_MCP_VERSION}/github-mcp-server_Linux_${MCP_ARCH}.tar.gz" \
    && tar -xzf /tmp/mcp.tar.gz -C /usr/local/bin/ github-mcp-server \
    && rm /tmp/mcp.tar.gz \
    && chmod +x /usr/local/bin/github-mcp-server

RUN pip install --no-cache-dir uv

WORKDIR /app

# Pattern-C hardening: refuse to ship sensitive paths in the image.
RUN test ! -e /root/.ssh && test ! -e /root/.aws \
    && test ! -e /root/.config/gcloud && test ! -e /root/.docker/config.json \
    || (echo "sensitive path leaked into image" && exit 1)

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen || uv sync --no-dev

COPY src/ ./src/

# Pattern-C plugin distribution: ship the org-level guardrails bundle
# into the worker image at a stable path. The Claude Agent SDK loads it
# via plugins=[{"type": "local", "path": "/plugins/tf-guardrails"}].
COPY plugins/ /plugins/
RUN chmod -R 555 /plugins

# Worker entrypoint by default; gateway overrides via `command:` in compose
CMD ["uv", "run", "python", "-m", "src.worker"]
