"""Temporal worker and transactional outbox delivery for admitted runs."""

import asyncio
import contextlib
import logging
from datetime import timedelta
from pathlib import Path

from rhetorica_backend.contracts.scenario_workflows import TERMINAL_RUN_STATUSES
from rhetorica_backend.domain.scenarios.bootstrap import build_scenario_runtime
from rhetorica_backend.domain.scenarios.settings import ScenarioSettings
from rhetorica_backend.domain.scenarios.temporal_workflow import ScenarioWorkflow
from rhetorica_backend.infra.postgres.scenario_activity_repository import ScenarioActivityRepository
from temporalio.client import Client, TLSConfig
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

logger = logging.getLogger(__name__)


async def dispatch_outbox(
    client: Client, repository: ScenarioActivityRepository, settings: ScenarioSettings
) -> None:
    while True:
        for entry in await asyncio.to_thread(repository.outbox):
            try:
                workflow_id = f"scenario/{entry.run_id}"
                if entry.kind == "start":
                    with contextlib.suppress(WorkflowAlreadyStartedError):
                        await client.start_workflow(
                            ScenarioWorkflow.run,
                            entry.run_id,
                            id=workflow_id,
                            task_queue=settings.temporal_task_queue,
                            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                        )
                else:
                    run = await asyncio.to_thread(repository.load_run, entry.run_id)
                    if run.status in TERMINAL_RUN_STATUSES:
                        await asyncio.to_thread(repository.acknowledge, entry.id)
                        continue
                    handle = client.get_workflow_handle(workflow_id)
                    if entry.kind == "approve":
                        await handle.signal(ScenarioWorkflow.approve, entry.payload)
                    elif entry.kind == "cancel":
                        await handle.cancel()
                await asyncio.to_thread(repository.acknowledge, entry.id)
            except Exception as exc:
                logger.warning(
                    "Scenario outbox delivery failed: %s (%s)", entry.id, type(exc).__name__
                )
                await asyncio.to_thread(repository.acknowledge, entry.id, type(exc).__name__)
        await asyncio.sleep(2)


async def run_worker() -> None:
    settings = ScenarioSettings()
    runtime = build_scenario_runtime(settings)
    tls = None
    if settings.temporal_tls_ca or settings.temporal_tls_cert:
        tls = TLSConfig(
            server_root_ca_cert=Path(settings.temporal_tls_ca).read_bytes()
            if settings.temporal_tls_ca
            else None,
            client_cert=Path(settings.temporal_tls_cert).read_bytes()
            if settings.temporal_tls_cert
            else None,
            client_private_key=Path(settings.temporal_tls_key).read_bytes()
            if settings.temporal_tls_key
            else None,
            domain=settings.temporal_tls_server_name or None,
        )
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace, tls=tls
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ScenarioWorkflow],
        activities=[
            runtime.activities.bootstrap,
            runtime.activities.step,
            runtime.activities.finish,
        ],
        max_concurrent_activities=settings.scenario_worker_concurrency,
        graceful_shutdown_timeout=timedelta(seconds=30),
    )
    async with worker:
        await dispatch_outbox(client, runtime.repository, settings)
