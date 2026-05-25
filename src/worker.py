import asyncio
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
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[PRAutofixWorkflow],
        activities=[prepare_workdir, cleanup_workdir, post_status],
    ):
        logging.info("worker listening on task queue %s", task_queue)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
