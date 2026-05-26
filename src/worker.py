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
from src.observability.otel import setup_otel


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Boot OTel before the Worker so Anthropic / Claude Agent SDK calls
    # made by the activities are auto-instrumented (Pattern-C
    # observability — feeds Arize via OpenInference).
    setup_otel(os.environ.get("ARIZE_PROJECT", "agent-temporal-dev"))
    client = await Client.connect(
        os.environ.get("TEMPORAL_TARGET", "localhost:7233"),
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
            ],
            activity_executor=activity_executor,
        ):
            logging.info("worker listening on task queue %s", task_queue)
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
