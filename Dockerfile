FROM python:3.12-slim

# git + node (for npx github-mcp-server)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen || uv sync --no-dev

COPY src/ ./src/

# Worker entrypoint by default; gateway overrides via `command:` in compose
CMD ["uv", "run", "python", "-m", "src.worker"]
