FROM python:3.12-slim

# The sandbox image is intentionally minimal: it must be able to clone a
# repo, run the linters/tests the agent invokes via exec_in_sandbox, and
# nothing else. It does NOT carry the worker code, Claude SDK, or any
# TheFork credentials.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ruff pytest

# Non-root user; matches the uid the worker uses to exec into the
# sandbox so cloned files have predictable ownership.
RUN useradd -m -u 1000 -s /bin/bash sandbox
USER 1000:1000
WORKDIR /work
