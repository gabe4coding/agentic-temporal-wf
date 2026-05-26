"""GC for orphan sandbox containers.

Pattern-C failure mode: a Worker crashes mid-Activity, the per-workflow
sandbox container is left running. The Workflow Task re-queues on
another Worker which starts a fresh sandbox; the original orphan stays
behind until reaped.

`list_orphan_containers` returns containers matching the
`autofix-sbx-*` name pattern that don't have a corresponding RUNNING
workflow. The actual Temporal lookup is injected so the activity can
be unit-tested without a live Temporal client."""
from __future__ import annotations

import logging
from typing import Callable, Iterable

import docker
from temporalio import activity


logger = logging.getLogger(__name__)


_SANDBOX_NAME_PREFIX = "autofix-sbx-"


def _list_container_names(docker_client) -> list[tuple[str, str]]:
    """Return list of (container_id, container_name) for all
    `autofix-sbx-*` containers."""
    return [
        (c.id, c.name)
        for c in docker_client.containers.list(all=True)
        if c.name.startswith(_SANDBOX_NAME_PREFIX)
    ]


def list_orphan_containers(
    docker_client,
    is_workflow_running: Callable[[str], bool],
) -> list[tuple[str, str]]:
    """Return (container_id, container_name) pairs that look like
    autofix sandboxes but have no RUNNING workflow.

    `is_workflow_running(workflow_id)` is the Temporal lookup,
    injected so tests can stub it."""
    orphans: list[tuple[str, str]] = []
    for cid, name in _list_container_names(docker_client):
        wf_id = name[len(_SANDBOX_NAME_PREFIX):]
        # Naming convention from src/activities/sandbox.py:
        # `autofix-sbx-{workflow_id}` with non-alphanum coerced to '-'.
        # The reverse is not lossless — best effort.
        if not is_workflow_running(wf_id):
            orphans.append((cid, name))
    return orphans


def reap_orphans(docker_client, orphans: Iterable[tuple[str, str]]) -> int:
    """Stop and remove every container in `orphans`. Returns count."""
    count = 0
    for cid, name in orphans:
        try:
            c = docker_client.containers.get(cid)
            c.stop(timeout=5)
            c.remove(force=True)
            count += 1
            logger.info("reaped orphan sandbox %s (%s)", name, cid[:12])
        except Exception as e:
            logger.warning("failed to reap %s: %s", name, e)
    return count


async def _is_workflow_running_via_temporal(workflow_id: str) -> bool:
    """Probe Temporal Cloud for the workflow status. Returns True iff
    the workflow's most recent execution is RUNNING."""
    from temporalio.client import Client
    import os

    target = os.environ.get("TEMPORAL_TARGET", "localhost:7233")
    client = await Client.connect(target)
    try:
        desc = await client.get_workflow_handle(workflow_id).describe()
        return desc.status.name == "RUNNING"
    except Exception:
        return False


@activity.defn
async def gc_orphan_sandboxes() -> int:
    """Scheduled activity: list autofix-sbx-* containers, reap those
    whose workflow isn't RUNNING. Returns the number reaped."""
    client = docker.from_env()
    # Synchronous lookup wrapper for the list step.
    import asyncio

    def _check(wf_id: str) -> bool:
        return asyncio.run(_is_workflow_running_via_temporal(wf_id))

    orphans = list_orphan_containers(client, _check)
    return reap_orphans(client, orphans)
