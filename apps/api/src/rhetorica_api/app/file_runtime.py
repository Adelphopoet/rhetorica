"""Composition root for file artifact adapters."""

from functools import lru_cache

from rhetorica_backend.domain.files import ArtifactPolicy, ArtifactPolicyConfig, ArtifactService
from rhetorica_backend.infra.object_storage import (
    ObjectStorageSqlResultSnapshotStore,
    S3ObjectStorage,
    S3StorageConfig,
)
from rhetorica_backend.infra.observability.file_telemetry import (
    OpenTelemetryFileTelemetry,
)
from rhetorica_backend.infra.postgres.file_artifact_repository import FileArtifactRepository

from rhetorica_api.app.identity import get_db_runtime
from rhetorica_api.app.settings import settings


@lru_cache(maxsize=1)
def get_file_telemetry() -> OpenTelemetryFileTelemetry:
    return OpenTelemetryFileTelemetry()


@lru_cache(maxsize=1)
def get_object_storage() -> S3ObjectStorage:
    return S3ObjectStorage(
        S3StorageConfig(
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            secure=settings.s3_secure,
        )
    )


@lru_cache(maxsize=1)
def get_sql_result_snapshot_store() -> ObjectStorageSqlResultSnapshotStore:
    return ObjectStorageSqlResultSnapshotStore(
        storage=get_object_storage(),
        max_rows=settings.file_sql_export_max_rows,
        max_bytes=settings.sql_result_snapshot_max_bytes,
        max_cell_bytes=settings.sql_result_snapshot_max_cell_bytes,
        max_row_bytes=settings.sql_result_snapshot_max_row_bytes,
    )


@lru_cache(maxsize=1)
def get_artifact_service() -> ArtifactService:
    policy = ArtifactPolicy(
        ArtifactPolicyConfig(
            max_input_bytes=settings.file_max_input_bytes,
            max_files_per_turn=settings.file_max_files_per_turn,
            max_turn_bytes=settings.file_max_turn_bytes,
            max_output_bytes=settings.file_max_output_bytes,
            max_session_output_bytes=settings.file_max_session_output_bytes,
            user_quota_bytes=settings.file_user_quota_bytes,
            workspace_quota_bytes=settings.file_workspace_quota_bytes,
        )
    )
    return ArtifactService(
        repository=FileArtifactRepository(get_db_runtime()),
        storage=get_object_storage(),
        policy=policy,
        telemetry=get_file_telemetry(),
    )
