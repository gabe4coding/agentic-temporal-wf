import asyncio
import concurrent.futures
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from src.workflows.pr_autofix import PRAutofixWorkflow
from src.activities.lifecycle import (
    prepare_workdir,
    cleanup_workdir,
    post_status,
    push_changes,
)
from src.activities.agent_iteration import run_agent_iteration
from src.activities.sandbox import (
    provision_sandbox,
    exec_in_sandbox,
    pause_sandbox,
    resume_sandbox,
    teardown_sandbox,
)
from src.activities.approval import notify_human_for_approval
from src.activities.snapshot import snapshot_sandbox, restore_sandbox

# Note: setup_otel is intentionally NOT imported here. The Claude Agent
# SDK runs inside the sandbox container, not in this Worker process, so
# the OpenInference instrumentor must be initialized in agent_runner/main.py
# (its actual Python process). Calling it here would be a no-op.


def _build_id() -> str:
    """Worker Versioning (Replay 2026 GA): pin in-flight Workflows to
    the worker version that started them. Falls back to 'dev' so local
    runs don't need extra env. CI sets WORKER_BUILD_ID=$GITHUB_SHA."""
    return os.environ.get("WORKER_BUILD_ID", "dev")


def _use_versioning() -> bool:
    """Worker Versioning must be enabled at the namespace level (Temporal
    Cloud + `temporal operator namespace update --enable-worker-versioning`,
    or `--enable-build-id-based-versioning` on a self-hosted cluster).
    The bundled temporalio/auto-setup dev image does NOT enable it by
    default, so trying to register a versioned worker there fails with
    PermissionDenied. We opt-in via env; default off."""
    return os.environ.get("USE_WORKER_VERSIONING", "0") == "1"


def _data_converter():
    """Pattern-C: route Temporal payloads >10 KB to S3 if AWS_S3_BUCKET
    is set. Documented pattern is `dataclasses.replace()` on the default
    DataConverter — there's no `.with_payload_codec(...)` builder."""
    import dataclasses

    from temporalio.converter import DataConverter

    bucket = os.environ.get("AWS_S3_BUCKET")
    if not bucket:
        return DataConverter.default
    from src.payload_storage.s3_codec import S3PayloadCodec

    return dataclasses.replace(
        DataConverter.default, payload_codec=S3PayloadCodec(bucket=bucket)
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(
        os.environ.get("TEMPORAL_TARGET", "localhost:7233"),
        data_converter=_data_converter(),
    )
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "pr-autofix")
    # prepare_workdir and cleanup_workdir are sync activities — Temporal
    # requires an explicit executor for those. post_status and
    # run_agent_iteration are async and run on the event loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as activity_executor:
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[PRAutofixWorkflow],
            activities=[
                prepare_workdir,
                cleanup_workdir,
                post_status,
                push_changes,
                run_agent_iteration,
                # Per-workflow sandbox activities. Registered but not yet
                # wired into PRAutofixWorkflow — that integration is the
                # next step (replace prepare_workdir/cleanup_workdir).
                provision_sandbox,
                exec_in_sandbox,
                pause_sandbox,
                resume_sandbox,
                teardown_sandbox,
                notify_human_for_approval,
                snapshot_sandbox,
                restore_sandbox,
            ],
            activity_executor=activity_executor,
            build_id=_build_id(),
            use_worker_versioning=_use_versioning(),
        ):
            logging.info(
                "worker listening on task queue %s build_id=%s versioning=%s",
                task_queue, _build_id(), _use_versioning(),
            )
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
