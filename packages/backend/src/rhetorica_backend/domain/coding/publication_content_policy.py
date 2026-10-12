"""Fail-closed path, secret, binary and size gates for GitLab publication."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from rhetorica_backend.contracts.coding import CodingReasonCode, GitLabWriteScope
from rhetorica_backend.domain.coding.publication_diff import FileChange
from rhetorica_backend.infra.coding.archives import ArchivePolicy, UnsafeArchiveError

_PEM = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")
_GLPAT = re.compile(rb"glpat-[A-Za-z0-9_\-]{20,}")
_GHP = re.compile(rb"ghp_[A-Za-z0-9]{20,}")
_AKIA = re.compile(rb"AKIA[0-9A-Z]{16}")
_SECRET_ASSIGNMENT = re.compile(
    rb"(?i)(?:^|[\s{,])(?:password|secret|api[_-]?token|api[_-]?key|access[_-]?token)\s*[:=]\s*\S+"
)
_ASSIGNMENT_SUFFIXES = {".yml", ".yaml", ".env", ".ini", ".toml", ".json", ".py", ".cfg"}

DENY_PATTERNS = (
    ".git",
    ".git/*",
    "*/.git",
    "*/.git/*",
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    ".gitlab/*",
    "*gitlab-ci*.yml",
    "*gitlab-ci*.yaml",
    "profiles.yml",
    "*/profiles.yml",
    ".env",
    ".env.*",
    "*/.env",
    "*/.env.*",
    "credentials.yml",
    "*/credentials.yml",
    "credentials.yaml",
    "*/credentials.yaml",
    "id_rsa",
    "*/id_rsa",
    "*.pem",
    "target/*",
    "*/target/*",
    "*.parquet",
    "exports/*",
    "*/exports/*",
    "dumps/*",
    "*/dumps/*",
    "*.dump",
)


class PublicationDenied(ValueError):
    def __init__(
        self,
        reason_code: CodingReasonCode | str,
        *,
        detail: str | None = None,
        receipt: Any | None = None,
    ) -> None:
        self.reason_code = (
            reason_code
            if isinstance(reason_code, CodingReasonCode)
            else CodingReasonCode(reason_code)
        )
        self.detail = detail
        self.receipt = receipt
        super().__init__(detail or self.reason_code.value)


@dataclass(frozen=True)
class ContentPolicyDecision:
    allowed: bool
    detail: str | None = None


class DbtPublicationDenyList:
    """Hard excludes for a dbt profile, independent of allowed_paths."""

    def reason_for(self, path: str, *, denied_paths: Sequence[str] = ()) -> str | None:
        normalized = path.strip().strip("/")
        if not normalized:
            return "publication_path_empty"
        if normalized in {item.strip().strip("/") for item in denied_paths}:
            return f"publication_path_forbidden:{normalized}"
        for pattern in DENY_PATTERNS:
            if fnmatch.fnmatch(normalized, pattern):
                return f"publication_path_forbidden:{normalized}"
        return None


class PublicationSecretScanner:
    def reason_for(self, *, path: str, content: bytes) -> str | None:
        if b"\x00" in content:
            return f"publication_binary_forbidden:{path}"
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return f"publication_binary_forbidden:{path}"
        if (
            _PEM.search(content)
            or _GLPAT.search(content)
            or _GHP.search(content)
            or _AKIA.search(content)
        ):
            return f"publication_secret_detected:{path}"
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in _ASSIGNMENT_SUFFIXES and _SECRET_ASSIGNMENT.search(content):
            return f"publication_secret_detected:{path}"
        return None


class PublicationContentPolicy:
    def __init__(
        self,
        *,
        deny_list: DbtPublicationDenyList | None = None,
        scanner: PublicationSecretScanner | None = None,
        archive_policy: ArchivePolicy | None = None,
    ) -> None:
        self._deny_list = deny_list or DbtPublicationDenyList()
        self._scanner = scanner or PublicationSecretScanner()
        self._archive_policy = archive_policy or ArchivePolicy()

    def validate(
        self,
        changes: Sequence[FileChange],
        *,
        changed_paths: Sequence[str],
        scopes: Sequence[GitLabWriteScope],
        unexpected_paths: Sequence[str] = (),
        denied_paths: Sequence[str] = (),
    ) -> None:
        if unexpected_paths:
            joined = ", ".join(sorted(unexpected_paths))
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED,
                detail=f"publication_undeclared_path:{joined}",
            )
        if not changes:
            raise PublicationDenied(
                CodingReasonCode.POLICY_DENIED,
                detail="publication_empty_diff",
            )
        allowed_paths = [path for scope in scopes for path in scope.allowed_paths]
        declared = {item.strip().strip("/") for item in changed_paths if item.strip()}
        total = 0
        for change in changes:
            for path in change.involved_paths():
                if path not in declared:
                    raise PublicationDenied(
                        CodingReasonCode.POLICY_DENIED,
                        detail=f"publication_undeclared_path:{path}",
                    )
                deny_reason = self._deny_list.reason_for(path, denied_paths=denied_paths)
                if deny_reason:
                    raise PublicationDenied(CodingReasonCode.POLICY_DENIED, detail=deny_reason)
                if not _path_allowed(path, allowed_paths):
                    raise PublicationDenied(
                        CodingReasonCode.POLICY_DENIED,
                        detail=f"publication_path_not_in_scope:{path}",
                    )
            if change.content_bytes is None:
                continue
            if len(change.content_bytes) > self._archive_policy.max_file_bytes:
                raise UnsafeArchiveError("archive_file_size_exceeded")
            total += len(change.content_bytes)
            if total > self._archive_policy.max_total_bytes:
                raise UnsafeArchiveError("archive_total_size_exceeded")
            secret_reason = self._scanner.reason_for(
                path=change.file_path, content=change.content_bytes
            )
            if secret_reason:
                raise PublicationDenied(CodingReasonCode.POLICY_DENIED, detail=secret_reason)


def _path_allowed(path: str, allowed_paths: Sequence[str]) -> bool:
    return any(path == allowed or path.startswith(f"{allowed}/") for allowed in allowed_paths)
