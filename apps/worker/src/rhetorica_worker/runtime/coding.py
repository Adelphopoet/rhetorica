"""Thin dedicated coding-worker entrypoint."""

import asyncio

from rhetorica_backend.domain.coding.bootstrap import build_coding_lifecycle
from rhetorica_backend.domain.coding.settings import CodingSettings
from rhetorica_backend.domain.coding.worker import run_coding_worker
from temporalio.client import Client


async def main() -> None:
    settings = CodingSettings()
    lifecycle, repository = build_coding_lifecycle(settings)
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await run_coding_worker(client=client, lifecycle=lifecycle, repository=repository)


if __name__ == "__main__":
    asyncio.run(main())
