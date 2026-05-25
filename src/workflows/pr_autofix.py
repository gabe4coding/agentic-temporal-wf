import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.models import (
        PRRef,
        GitHubEvent,
        WorkflowState,
        FixPlan,
    )
    from src.activities.lifecycle import (
        prepare_workdir,
        cleanup_workdir,
        post_status,
    )
    from src.activities.agent_iteration import run_agent_iteration


MAX_ITERATIONS = 5
IDLE_TIMEOUT = timedelta(minutes=30)


@workflow.defn(name="PRAutofixWorkflow")
class PRAutofixWorkflow:
    @workflow.init
    def __init__(self, init: PRRef | WorkflowState) -> None:
        self._state: WorkflowState = (
            init if isinstance(init, WorkflowState) else WorkflowState(pr=init)
        )

    @workflow.signal
    async def on_event(self, event: GitHubEvent) -> None:
        if event.delivery_id in self._state.processed_delivery_ids:
            return
        self._state.processed_delivery_ids.add(event.delivery_id)
        self._state.pending_events.append(event)

    @workflow.signal
    async def close(self) -> None:
        self._state.closed = True

    @workflow.query
    def get_state(self) -> WorkflowState:
        return self._state

    @workflow.run
    async def run(self, init: PRRef | WorkflowState) -> str:
        await workflow.execute_activity(
            prepare_workdir,
            self._state.pr,
            start_to_close_timeout=timedelta(minutes=5),
        )
        do_cleanup = True
        try:
            while self._state.iterations < MAX_ITERATIONS:
                try:
                    await workflow.wait_condition(
                        lambda: bool(self._state.pending_events) or self._state.closed,
                        timeout=IDLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    self._state.closed = True
                    break
                if self._state.closed and not self._state.pending_events:
                    break

                self._state.iterations += 1
                events_snapshot = list(self._state.pending_events)
                self._state.pending_events.clear()

                try:
                    plan: FixPlan = await workflow.execute_activity(
                        run_agent_iteration,
                        args=[self._state, events_snapshot],
                        start_to_close_timeout=timedelta(minutes=10),
                        heartbeat_timeout=timedelta(seconds=90),
                        retry_policy=RetryPolicy(
                            maximum_attempts=2,
                            initial_interval=timedelta(seconds=30),
                            backoff_coefficient=2.0,
                        ),
                    )
                except Exception as exc:
                    plan = FixPlan(
                        action="blocked",
                        summary="Agent iteration failed.",
                        blocking_reason=f"{type(exc).__name__}: {exc}",
                    )
                    self._state.closed = True

                self._apply_plan(plan)

                self._state = await workflow.execute_activity(
                    post_status,
                    args=[self._state, plan],
                    start_to_close_timeout=timedelta(seconds=60),
                )

                if workflow.info().is_continue_as_new_suggested():
                    do_cleanup = False
                    workflow.continue_as_new(self._state)
        finally:
            if do_cleanup:
                await workflow.execute_activity(
                    cleanup_workdir,
                    self._state.pr,
                    start_to_close_timeout=timedelta(minutes=2),
                )
        return f"done after {self._state.iterations} iterations"

    def _apply_plan(self, plan: FixPlan) -> None:
        self._state.processed_comment_ids |= set(plan.addressed_comment_ids)
