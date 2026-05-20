from __future__ import annotations

from celery import (
    Celery,  # type: ignore[import-untyped]
    signals,  # type: ignore[import-untyped]
)
from rhetorica_backend.infra.logging import bind_context, clear_context


def build_celery_app(
    *,
    broker_url: str,
    result_backend: str,
) -> Celery:
    app = Celery(
        "rhetorica",
        broker=broker_url,
        backend=result_backend,
    )
    app.conf.update(
        task_track_started=True,
        result_extended=True,
        result_expires=3600,
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
    )

    @signals.task_prerun.connect(sender=app)
    def _bind_task_context(
        task_id=None,
        task=None,
        args=None,
        kwargs=None,
        **_: object,
    ) -> None:
        clear_context()
        bind_context(
            celery_task_id=task_id,
            celery_task_name=getattr(task, "name", None),
            celery_task_args=args,
            celery_task_kwargs=kwargs,
        )

    @signals.task_postrun.connect(sender=app)
    def _clear_task_context(**_: object) -> None:
        clear_context()

    return app
