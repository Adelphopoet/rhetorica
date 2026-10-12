"""Object storage adapters."""

from rhetorica_backend.infra.object_storage.s3 import S3ObjectStorage, S3StorageConfig
from rhetorica_backend.infra.object_storage.sql_result_snapshots import (
    ObjectStorageSqlResultSnapshotStore,
)

__all__ = [
    "ObjectStorageSqlResultSnapshotStore",
    "S3ObjectStorage",
    "S3StorageConfig",
]
