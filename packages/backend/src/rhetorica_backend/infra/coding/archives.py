"""Fail-closed archive validation shared by snapshot and candidate transfers."""

from __future__ import annotations

import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import PurePosixPath


class UnsafeArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ArchivePolicy:
    max_files: int = 20_000
    max_file_bytes: int = 20 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedArchive:
    digest: str
    size_bytes: int
    paths: tuple[str, ...]


class SafeArchive:
    """Accept regular repository files only; links and device entries are never followed."""

    @staticmethod
    def validate(payload: bytes, *, policy: ArchivePolicy) -> ValidatedArchive:
        paths: list[str] = []
        total = 0
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) > policy.max_files:
                raise UnsafeArchiveError("archive_file_count_exceeded")
            for member in members:
                path = SafeArchive.normalize_path(member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise UnsafeArchiveError("archive_non_regular_entry")
                if member.size > policy.max_file_bytes:
                    raise UnsafeArchiveError("archive_file_size_exceeded")
                total += member.size
                if total > policy.max_total_bytes:
                    raise UnsafeArchiveError("archive_total_size_exceeded")
                paths.append(path)
        return ValidatedArchive(
            digest=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            paths=tuple(paths),
        )

    @staticmethod
    def normalize_path(value: str) -> str:
        if not value or "\x00" in value or "\\" in value or value.startswith("/"):
            raise UnsafeArchiveError("archive_path_invalid")
        path = PurePosixPath(value)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise UnsafeArchiveError("archive_path_invalid")
        return path.as_posix()

    @staticmethod
    def extract(payload: bytes, *, target: str, policy: ArchivePolicy) -> ValidatedArchive:
        from pathlib import Path

        target_path = Path(target).resolve()
        target_path.mkdir(parents=True, exist_ok=True)
        validated = SafeArchive.validate(payload, policy=policy)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            for member in archive.getmembers():
                relative = SafeArchive.normalize_path(member.name)
                destination = (target_path / relative).resolve()
                if target_path not in destination.parents:
                    raise UnsafeArchiveError("archive_path_invalid")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise UnsafeArchiveError("archive_entry_unreadable")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
        return validated
