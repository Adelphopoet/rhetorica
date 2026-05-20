from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, get_ident
from typing import Protocol

import boto3
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, InvalidToken


class SecretStoreError(RuntimeError):
    """Raised when secret storage operations fail."""


class SecretStore(Protocol):
    """Abstraction for secret persistence and retrieval."""

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        """Load a secret value by workspace and key."""

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        """Store or rotate secret value."""

    def delete_secret(self, workspace_id: str, secret_key: str) -> None:
        """Delete stored secret value."""


class InMemorySecretStore:
    """Process-local store useful for tests and isolated environments."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._data: dict[str, str] = {}

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        with self._lock:
            return self._data.get(self._compound_key(workspace_id, secret_key))

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        with self._lock:
            self._data[self._compound_key(workspace_id, secret_key)] = value

    def delete_secret(self, workspace_id: str, secret_key: str) -> None:
        with self._lock:
            self._data.pop(self._compound_key(workspace_id, secret_key), None)

    @staticmethod
    def _compound_key(workspace_id: str, secret_key: str) -> str:
        return f"{workspace_id}:{secret_key}"


class EnvSecretStore:
    """Read-first environment variable store with process-local write overlay."""

    def __init__(self, prefix: str = "RHETORICA_SECRET_") -> None:
        self._prefix = prefix
        self._lock = RLock()
        self._overlay: dict[str, str] = {}

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        key = self._env_key(workspace_id, secret_key)
        value = os.environ.get(key)
        if value is not None:
            return value
        with self._lock:
            return self._overlay.get(key)

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        key = self._env_key(workspace_id, secret_key)
        with self._lock:
            self._overlay[key] = value

    def delete_secret(self, workspace_id: str, secret_key: str) -> None:
        key = self._env_key(workspace_id, secret_key)
        with self._lock:
            self._overlay.pop(key, None)

    def _env_key(self, workspace_id: str, secret_key: str) -> str:
        raw = f"{workspace_id}_{secret_key}".upper()
        normalized = "".join(ch if ch.isalnum() else "_" for ch in raw)
        return f"{self._prefix}{normalized}"


class EncryptedFileSecretStore:
    """Encrypted file-based secret storage for local/self-hosted runtime."""

    def __init__(
        self,
        file_path: str | Path,
        master_key: str | None = None,
    ) -> None:
        self._file_path = Path(file_path)
        self._lock_file_path = self._file_path.with_suffix(f"{self._file_path.suffix}.lock")
        self._lock = RLock()
        key = master_key or self._load_or_create_key()
        self._fernet = Fernet(key.encode("utf-8"))

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        with self._lock:
            with self._acquire_file_lock():
                payload = self._load_payload()
            token = payload.get(self._compound_key(workspace_id, secret_key))
            if token is None:
                return None
            try:
                return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
            except InvalidToken as exc:
                raise SecretStoreError("encrypted_secret_corrupted") from exc

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        with self._lock, self._acquire_file_lock():
            payload = self._load_payload()
            encrypted = self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")
            payload[self._compound_key(workspace_id, secret_key)] = encrypted
            self._save_payload(payload)

    def delete_secret(self, workspace_id: str, secret_key: str) -> None:
        with self._lock, self._acquire_file_lock():
            payload = self._load_payload()
            payload.pop(self._compound_key(workspace_id, secret_key), None)
            self._save_payload(payload)

    def _load_payload(self) -> dict[str, str]:
        if not self._file_path.exists():
            return {}
        try:
            raw = self._file_path.read_text(encoding="utf-8").strip()
            if not raw:
                return {}
            loaded = json.loads(raw)
        except OSError as exc:
            raise SecretStoreError("secret_store_read_failed") from exc
        except json.JSONDecodeError as exc:
            raise SecretStoreError("secret_store_format_invalid") from exc

        if not isinstance(loaded, Mapping):
            raise SecretStoreError("secret_store_payload_invalid")

        result: dict[str, str] = {}
        for key, value in loaded.items():
            if isinstance(key, str) and isinstance(value, str):
                result[key] = value
        return result

    def _save_payload(self, payload: dict[str, str]) -> None:
        tmp_path = self._file_path.with_name(
            f"{self._file_path.name}.{os.getpid()}.{get_ident()}.tmp"
        )
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._file_path)
            os.chmod(self._file_path, 0o600)
        except OSError as exc:
            raise SecretStoreError("secret_store_write_failed") from exc
        finally:
            with contextlib.suppress(OSError):
                if tmp_path.exists():
                    tmp_path.unlink()

    def _load_or_create_key(self) -> str:
        key_file = self._file_path.with_suffix(f"{self._file_path.suffix}.key")
        if key_file.exists():
            try:
                key = key_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise SecretStoreError("secret_key_read_failed") from exc
            if not key:
                raise SecretStoreError("secret_key_empty")
            return key

        key = Fernet.generate_key().decode("utf-8")
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(key, encoding="utf-8")
            os.chmod(key_file, 0o600)
        except OSError as exc:
            raise SecretStoreError("secret_key_write_failed") from exc
        return key

    @contextmanager
    def _acquire_file_lock(self) -> Iterator[None]:
        self._lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_file_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _compound_key(workspace_id: str, secret_key: str) -> str:
        return f"{workspace_id}:{secret_key}"


class AwsSecretsManagerSecretStore:
    """AWS Secrets Manager-backed store for production deployments."""

    def __init__(
        self,
        prefix: str = "rhetorica",
        region_name: str | None = None,
    ) -> None:
        self._prefix = prefix.strip("/")
        self._client = boto3.client("secretsmanager", region_name=region_name)

    def get_secret(self, workspace_id: str, secret_key: str) -> str | None:
        name = self._name(workspace_id, secret_key)
        try:
            response = self._client.get_secret_value(SecretId=name)
        except self._client.exceptions.ResourceNotFoundException:  # type: ignore[attr-defined]
            return None
        except ClientError as exc:
            raise SecretStoreError(f"aws_get_secret_failed:{name}") from exc

        value = response.get("SecretString")
        if isinstance(value, str):
            return value
        return None

    def set_secret(self, workspace_id: str, secret_key: str, value: str) -> None:
        name = self._name(workspace_id, secret_key)
        try:
            self._client.put_secret_value(SecretId=name, SecretString=value)
        except self._client.exceptions.ResourceNotFoundException:  # type: ignore[attr-defined]
            pass
        except ClientError as exc:
            raise SecretStoreError(f"aws_put_secret_failed:{name}") from exc
        else:
            return

        try:
            self._client.create_secret(Name=name, SecretString=value)
        except ClientError as exc:
            raise SecretStoreError(f"aws_create_secret_failed:{name}") from exc

    def delete_secret(self, workspace_id: str, secret_key: str) -> None:
        name = self._name(workspace_id, secret_key)
        try:
            self._client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
        except self._client.exceptions.ResourceNotFoundException:  # type: ignore[attr-defined]
            return
        except ClientError as exc:
            raise SecretStoreError(f"aws_delete_secret_failed:{name}") from exc

    def _name(self, workspace_id: str, secret_key: str) -> str:
        return f"{self._prefix}/{workspace_id}/{secret_key}"
