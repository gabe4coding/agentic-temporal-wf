"""Sandbox snapshot/restore activities (Pattern-C rule 4).

Approach for the PoC: `docker commit` the running sandbox container to
a local image tag. The resulting SnapshotRef carries the tag so a
follow-up `provision_sandbox` can restore by passing the same image to
`containers.run`. S3 spill (and the `aws s3 cp` round-trip) is
deferred — `SnapshotRef.s3_bucket`/`s3_key` stay None for the local
PoC; see plan Open Question #3 for cost considerations."""
from __future__ import annotations

import logging

import docker
from temporalio import activity

from src.models import SandboxHandle, SnapshotRef


logger = logging.getLogger(__name__)


def _snapshot_impl(handle: SandboxHandle, iteration: int) -> SnapshotRef:
    client = docker.from_env()
    container = client.containers.get(handle.container_id)
    tag = f"autofix-snap:{handle.container_id[:12]}-{iteration}"
    container.commit(repository="autofix-snap", tag=f"{handle.container_id[:12]}-{iteration}")
    return SnapshotRef(image_tag=tag, iteration=iteration)


def _restore_impl(ref: SnapshotRef, *, container_name: str) -> SandboxHandle:
    client = docker.from_env()
    container = client.containers.run(
        ref.image_tag,
        name=container_name,
        command=["sleep", "infinity"],
        detach=True,
    )
    return SandboxHandle(container_id=container.id, workdir="/work")


@activity.defn
def snapshot_sandbox(handle: SandboxHandle, iteration: int) -> SnapshotRef:
    return _snapshot_impl(handle, iteration)


@activity.defn
def restore_sandbox(ref: SnapshotRef, container_name: str) -> SandboxHandle:
    return _restore_impl(ref, container_name=container_name)


def should_snapshot(iteration: int, last_snapshot_iter: int) -> bool:
    """Cadence: every 5 iterations. (Two-minute wall-clock alternative
    is workflow-side — `workflow.now()` is available there but not
    here.)"""
    return iteration > 0 and (iteration - last_snapshot_iter) >= 5
