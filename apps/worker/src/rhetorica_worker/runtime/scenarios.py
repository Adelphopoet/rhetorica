"""Thin entrypoint for the dedicated workflow worker."""

import asyncio

from rhetorica_backend.domain.scenarios.worker import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
