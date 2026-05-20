from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from rhetorica_backend.infra.secrets.stores import (
    AwsSecretsManagerSecretStore,
    EncryptedFileSecretStore,
    EnvSecretStore,
    SecretStore,
)


@lru_cache(maxsize=1)
def get_secret_store() -> SecretStore:
    backend = os.environ.get("RHETORICA_SECRET_STORE_BACKEND", "encrypted_file").strip().lower()

    if backend == "aws_secrets_manager":
        return AwsSecretsManagerSecretStore(
            prefix=os.environ.get("RHETORICA_SECRET_STORE_AWS_PREFIX", "rhetorica"),
            region_name=os.environ.get("RHETORICA_SECRET_STORE_AWS_REGION"),
        )

    if backend == "env":
        return EnvSecretStore(
            prefix=os.environ.get("RHETORICA_SECRET_STORE_ENV_PREFIX", "RHETORICA_SECRET_")
        )

    file_path = os.environ.get("RHETORICA_SECRET_STORE_FILE_PATH")
    if not file_path:
        file_path = str(Path.cwd() / ".rhetorica" / "secrets.enc.json")
    return EncryptedFileSecretStore(
        file_path=file_path,
        master_key=os.environ.get("RHETORICA_SECRET_STORE_MASTER_KEY"),
    )
