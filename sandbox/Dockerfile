FROM python:3.12-slim

# The sandbox image is intentionally minimal: it must be able to clone a
# repo, run the linters/tests the agent invokes via exec_in_sandbox, and
# nothing else. It does NOT carry the worker code, Claude SDK, or any
# TheFork credentials.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ruff pytest

# Note: the sandbox runs as root inside the container to match the
# worker's uid (the worker also runs as root and chowns workdir files
# accordingly). The hard isolation boundary is cap_drop=ALL +
# no-new-privileges applied at container creation, not the in-container
# uid. Production: align worker + sandbox to a non-root uid together.
WORKDIR /work
