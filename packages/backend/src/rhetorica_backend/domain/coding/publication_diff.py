"""Normalize a candidate tree against the captured GitLab base SHA."""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from rhetorica_backend.infra.coding.archives import ArchivePolicy, SafeArchive, UnsafeArchiveError

SKIP_NAMES = {".git", ".rhetorica"}
ChangeAction = Literal["create", "update", "delete", "move"]


@dataclass(frozen=True)
class FileChange:
    action: ChangeAction
    file_path: str
    previous_path: str | None = None
    content: str | None = None
    content_bytes: bytes | None = None

    def involved_paths(self) -> tuple[str, ...]:
        if self.previous_path and self.previous_path != self.file_path:
            return (self.file_path, self.previous_path)
        return (self.file_path,)

    def gitlab_action(self) -> dict[str, str]:
        if self.action == "delete":
            return {"action": "delete", "file_path": self.file_path}
        payload = {
            "action": self.action,
            "file_path": self.file_path,
            "content": self.content or "",
        }
        if self.action == "move" and self.previous_path:
            payload["previous_path"] = self.previous_path
        return payload


@dataclass(frozen=True)
class PublicationDiff:
    changes: tuple[FileChange, ...]
    unexpected_paths: tuple[str, ...]
    tree_digest: str


def tree_from_candidate_archive(
    payload: bytes, *, policy: ArchivePolicy | None = None
) -> dict[str, bytes]:
    return _tree_from_archive(payload, strip_common_prefix=False, policy=policy)


def tree_from_gitlab_archive(
    payload: bytes, *, policy: ArchivePolicy | None = None
) -> dict[str, bytes]:
    return _tree_from_archive(payload, strip_common_prefix=True, policy=policy)


def diff_trees(
    *,
    base: Mapping[str, bytes],
    candidate: Mapping[str, bytes],
    changed_paths: list[str],
) -> PublicationDiff:
    declared = {item.strip().strip("/") for item in changed_paths if item.strip()}
    base_paths = set(base)
    candidate_paths = set(candidate)
    added = sorted(candidate_paths - base_paths)
    removed = sorted(base_paths - candidate_paths)
    updated = sorted(path for path in base_paths & candidate_paths if base[path] != candidate[path])
    unexpected = tuple(
        sorted(path for path in (*added, *removed, *updated) if path not in declared)
    )

    remaining_added = {path: candidate[path] for path in added if path in declared}
    remaining_removed = {path: base[path] for path in removed if path in declared}
    changes: list[FileChange] = []
    for old_path in sorted(remaining_removed):
        old_payload = remaining_removed[old_path]
        match = next(
            (
                new_path
                for new_path in sorted(remaining_added)
                if remaining_added[new_path] == old_payload
            ),
            None,
        )
        if match is None:
            continue
        changes.append(
            FileChange(
                action="move",
                file_path=match,
                previous_path=old_path,
                content=_decode_text(match, remaining_added[match]),
                content_bytes=remaining_added[match],
            )
        )
        del remaining_added[match]
        del remaining_removed[old_path]

    for path in sorted(remaining_added):
        payload = remaining_added[path]
        changes.append(
            FileChange(
                action="create",
                file_path=path,
                content=_decode_text(path, payload),
                content_bytes=payload,
            )
        )
    for path in sorted(p for p in updated if p in declared):
        payload = candidate[path]
        changes.append(
            FileChange(
                action="update",
                file_path=path,
                content=_decode_text(path, payload),
                content_bytes=payload,
            )
        )
    for path in sorted(remaining_removed):
        changes.append(FileChange(action="delete", file_path=path))

    digest_source = "\n".join(
        f"{item.action}:{item.previous_path or ''}:{item.file_path}:"
        f"{hashlib.sha256(item.content_bytes or b'').hexdigest()}"
        for item in changes
    ).encode()
    return PublicationDiff(
        changes=tuple(changes),
        unexpected_paths=unexpected,
        tree_digest=hashlib.sha256(digest_source).hexdigest(),
    )


def _tree_from_archive(
    payload: bytes,
    *,
    strip_common_prefix: bool,
    policy: ArchivePolicy | None,
) -> dict[str, bytes]:
    archive_policy = policy or ArchivePolicy()
    SafeArchive.validate(payload, policy=archive_policy)
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        prefix = (
            _common_archive_prefix(member.name for member in members) if strip_common_prefix else ""
        )
        for member in members:
            relative = _strip_prefix(member.name, prefix) if prefix else member.name
            path = SafeArchive.normalize_path(relative)
            if _skipped(path):
                continue
            source = archive.extractfile(member)
            if source is None:
                raise UnsafeArchiveError("archive_entry_unreadable")
            data = source.read(archive_policy.max_file_bytes + 1)
            if len(data) > archive_policy.max_file_bytes:
                raise UnsafeArchiveError("archive_file_size_exceeded")
            files[path] = data
    return files


def _skipped(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and parts[0] in SKIP_NAMES


def _common_archive_prefix(paths: Iterable[str]) -> str:
    values = [PurePosixPath(path).parts for path in paths]
    if not values or not values[0]:
        return ""
    first = values[0][0]
    return first if all(parts and parts[0] == first for parts in values) else ""


def _strip_prefix(path: str, prefix: str) -> str:
    if not prefix:
        return path
    marker = f"{prefix}/"
    return path[len(marker) :] if path.startswith(marker) else path


def _decode_text(path: str, payload: bytes) -> str:
    if b"\x00" in payload:
        raise UnsafeArchiveError(f"archive_binary_entry:{path}")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeArchiveError(f"archive_binary_entry:{path}") from exc
