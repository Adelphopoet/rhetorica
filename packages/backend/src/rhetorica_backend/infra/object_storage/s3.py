"""Bounded-memory S3/MinIO object storage adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from rhetorica_backend.domain.files.models import StoredObject


@dataclass(frozen=True)
class S3StorageConfig:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    secure: bool = False


class S3ObjectStorage:
    _PART_BYTES = 8 * 1024 * 1024

    def __init__(self, config: S3StorageConfig) -> None:
        self._bucket = config.bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name=config.region,
            use_ssl=config.secure,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )
        self._transfer_config = TransferConfig(
            multipart_threshold=self._PART_BYTES,
            multipart_chunksize=self._PART_BYTES,
            max_concurrency=2,
            max_io_queue=4,
            io_chunksize=256 * 1024,
            use_threads=True,
        )

    def upload_fileobj(
        self,
        *,
        key: str,
        fileobj: BinaryIO,
        size_bytes: int,
        media_type: str,
    ) -> None:
        del size_bytes
        self._client.upload_fileobj(
            Fileobj=fileobj,
            Bucket=self._bucket,
            Key=key,
            ExtraArgs={"ContentType": media_type},
            Config=self._transfer_config,
        )

    def open(self, key: str) -> StoredObject:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return StoredObject(
            body=response["Body"],
            size_bytes=int(response.get("ContentLength") or 0),
            media_type=str(response.get("ContentType") or "application/octet-stream"),
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def create_download_url(self, key: str, *, expires_seconds: int = 300) -> str:
        return str(self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_seconds,
        ))
