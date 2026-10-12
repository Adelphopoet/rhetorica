"""Coding Temporal worker composition and transactional-outbox delivery."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from rhetorica_backend.domain.coding.activities import CodingActivities
from rhetorica_backend.domain.coding.lifecycle import CodingLifecycleService
from rhetorica_backend.domain.coding.temporal_workflow import CodingWorkflow
from rhetorica_backend.infra.postgres.coding_run_repository import CodingRunRepository
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodingWorkerSettings:
    temporal_task_queue: str = "rhetorica-coding-v1"
    worker_concurrency: int = 4


async def dispatch_coding_outbox(
    *,
    client: Client,
    repository: CodingRunRepository,
    settings: CodingWorkerSettings,
    poll_interval_seconds: float = 2.0,
) -> None:
    """Deliver committed commands; a duplicate Temporal start is harmless."""

    while True:
        for entry in await asyncio.to_thread(repository.pending_outbox):
            try:
                workflow_id = repository.workflow_id(entry.run_id)
                if entry.kind == "start":
                    with contextlib.suppress(WorkflowAlreadyStartedError):
                        await client.start_workflow(
                            CodingWorkflow.run,
                            entry.run_id,
                            id=workflow_id,
                            task_queue=settings.temporal_task_queue,
                            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                        )
                elif entry.kind == "cancel":
                    handle = client.get_workflow_handle(workflow_id)
                    await handle.cancel()
                await asyncio.to_thread(
                    repository.record_outbox_attempt,
                    entry=entry,
                    delivered=True,
                )
            except Exception as exc:  # delivery keeps retrying from its durable row
                logger.warning(
                    "Coding outbox delivery failed: %s (%s)", entry.id, type(exc).__name__
                )
                await asyncio.to_thread(
                    repository.record_outbox_attempt,
                    entry=entry,
                    delivered=False,
                    error=type(exc).__name__,
                )
        await asyncio.sleep(poll_interval_seconds)


async def run_coding_worker(
    *,
    client: Client,
    lifecycle: CodingLifecycleService,
    repository: CodingRunRepository,
    settings: CodingWorkerSettings | None = None,
) -> None:
    """Run independently from the existing scenario task queue and worker."""

    effective = settings or CodingWorkerSettings()
    activities = CodingActivities(lifecycle)
    worker = Worker(
        client,
        task_queue=effective.temporal_task_queue,
        workflows=[CodingWorkflow],
        activities=[
            activities.prepare,
            activities.coordinate,
            activities.develop,
            activities.qa,
            activities.publish,
            activities.cancel,
            activities.fail,
        ],
        max_concurrent_activities=effective.worker_concurrency,
        graceful_shutdown_timeout=timedelta(seconds=30),
    )
    async with worker:
        await dispatch_coding_outbox(
            client=client,
            repository=repository,
            settings=effective,
        )
