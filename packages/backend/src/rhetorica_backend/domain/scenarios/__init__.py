"""Lazy public API: deterministic workflows must not import I/O adapters."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rhetorica_backend.domain.scenarios.service import ScenarioCatalogService


def __getattr__(name: str) -> Any:
    if name == "ScenarioCatalogService":
        from rhetorica_backend.domain.scenarios.service import ScenarioCatalogService

        return ScenarioCatalogService
    raise AttributeError(name)


__all__ = ["ScenarioCatalogService"]
