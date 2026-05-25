import asyncio
import concurrent.futures
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin

from src.workflows.pr_autofix import PRAutofixWorkflow
from src.activities.lifecycle import (
    prepare_workdir,
    cleanup_workdir,
    post_status,
)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(
        os.environ.get("TEMPORAL_TARGET", "localhost:7233"),
        plugins=[PydanticAIPlugin()],
    )
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "pr-autofix")
    # prepare_workdir and cleanup_workdir are sync activities — Temporal requires
    # an explicit executor for those. post_status is async and routes through asyncio.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as activity_executor:
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[PRAutofixWorkflow],
            activities=[prepare_workdir, cleanup_workdir, post_status],
            activity_executor=activity_executor,
        ):
            logging.info("worker listening on task queue %s", task_queue)
            await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
