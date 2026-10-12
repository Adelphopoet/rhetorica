"""Deterministic file validation and quota policy."""

from __future__ import annotations

import mimetypes
import unicodedata
import zipfile
from codecs import getincrementaldecoder
from dataclasses import dataclass
from pathlib import PurePath
from typing import BinaryIO, ClassVar


class ArtifactRejectedError(ValueError):
    """An upload violated an explicit artifact policy."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ArtifactPolicyConfig:
    max_input_bytes: int = 25 * 1024 * 1024
    max_files_per_turn: int = 5
    max_turn_bytes: int = 50 * 1024 * 1024
    max_output_bytes: int = 50 * 1024 * 1024
    max_session_output_bytes: int = 100 * 1024 * 1024
    user_quota_bytes: int = 1024 * 1024 * 1024
    workspace_quota_bytes: int = 10 * 1024 * 1024 * 1024
    max_ooxml_entries: int = 10_000
    max_ooxml_uncompressed_bytes: int = 250 * 1024 * 1024
    max_ooxml_compression_ratio: float = 100.0


class ArtifactPolicy:
    """Validates extension, declared MIME, signature and archive expansion."""

    _CANONICAL_MEDIA: ClassVar[dict[str, str]] = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".json": "application/json",
        ".parquet": "application/vnd.apache.parquet",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    _PLAIN_EXTENSIONS = frozenset({".txt", ".md", ".csv", ".tsv", ".json"})
    _OOXML_MARKERS: ClassVar[dict[str, str]] = {
        ".xlsx": "xl/workbook.xml",
        ".docx": "word/document.xml",
        ".pptx": "ppt/presentation.xml",
    }
    _GENERIC_MEDIA = frozenset({"application/octet-stream", "binary/octet-stream"})

    def __init__(self, config: ArtifactPolicyConfig | None = None) -> None:
        self.config = config or ArtifactPolicyConfig()

    def normalize_filename(self, filename: str) -> str:
        normalized = unicodedata.normalize("NFC", filename.strip())
        if (
            not normalized
            or len(normalized) > 255
            or "\x00" in normalized
            or "/" in normalized
            or "\\" in normalized
            or PurePath(normalized).name != normalized
            or normalized in {".", ".."}
        ):
            raise ArtifactRejectedError("invalid_filename", "Invalid file name")
        extension = PurePath(normalized).suffix.lower()
        if extension not in self._CANONICAL_MEDIA:
            raise ArtifactRejectedError("unsupported_format", "File format is not allowed")
        return normalized

    def validate_input_size(self, size_bytes: int) -> None:
        if size_bytes <= 0:
            raise ArtifactRejectedError("empty_file", "Empty files are not allowed")
        if size_bytes > self.config.max_input_bytes:
            raise ArtifactRejectedError("file_too_large", "Input file exceeds the configured limit")

    def validate_turn(self, sizes: list[int]) -> None:
        if len(sizes) > self.config.max_files_per_turn:
            raise ArtifactRejectedError("too_many_files", "Too many files in one turn")
        if sum(sizes) > self.config.max_turn_bytes:
            raise ArtifactRejectedError("turn_too_large", "Turn file size limit exceeded")

    def validate_quota(self, *, user_bytes: int, workspace_bytes: int, incoming: int) -> None:
        if user_bytes + incoming > self.config.user_quota_bytes:
            raise ArtifactRejectedError("user_quota_exceeded", "User file quota exceeded")
        if workspace_bytes + incoming > self.config.workspace_quota_bytes:
            raise ArtifactRejectedError("workspace_quota_exceeded", "Workspace file quota exceeded")

    def inspect(
        self,
        *,
        filename: str,
        declared_media_type: str | None,
        fileobj: BinaryIO,
        trusted_generated: bool = False,
    ) -> str:
        extension = PurePath(filename).suffix.lower()
        canonical = self._CANONICAL_MEDIA[extension]
        declared = (declared_media_type or "").split(";", 1)[0].strip().lower()
        guessed = (mimetypes.guess_type(filename)[0] or "").lower()
        allowed_declared = {canonical, guessed, *self._GENERIC_MEDIA}
        if extension in self._PLAIN_EXTENSIONS:
            allowed_declared.update({"text/plain", "text/markdown", "application/json"})
        if declared and declared not in allowed_declared:
            raise ArtifactRejectedError("mime_mismatch", "Declared MIME does not match extension")

        fileobj.seek(0)
        header = fileobj.read(16)
        if (extension == ".pdf" and not header.startswith(b"%PDF-")) or (extension == ".png" and header[:8] != b"\x89PNG\r\n\x1a\n") or (extension in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff")):
            self._magic_mismatch()
        elif extension == ".parquet":
            if not header.startswith(b"PAR1"):
                self._magic_mismatch()
            try:
                fileobj.seek(-4, 2)
            except OSError:
                self._magic_mismatch()
            if fileobj.read(4) != b"PAR1":
                self._magic_mismatch()
        elif extension in self._OOXML_MARKERS:
            self._validate_ooxml(
                fileobj,
                marker=self._OOXML_MARKERS[extension],
                trusted_generated=trusted_generated,
            )
        elif extension in self._PLAIN_EXTENSIONS:
            self._validate_utf8_text(fileobj)
        fileobj.seek(0)
        return canonical

    def _validate_ooxml(
        self,
        fileobj: BinaryIO,
        *,
        marker: str,
        trusted_generated: bool,
    ) -> None:
        fileobj.seek(0)
        try:
            with zipfile.ZipFile(fileobj) as archive:
                entries = archive.infolist()
                if len(entries) > self.config.max_ooxml_entries:
                    raise ArtifactRejectedError("archive_limit", "OOXML has too many entries")
                names = {entry.filename for entry in entries}
                if marker not in names or "[Content_Types].xml" not in names:
                    self._magic_mismatch()
                if any("vbaproject.bin" in name.lower() for name in names):
                    raise ArtifactRejectedError("macros_forbidden", "Macro-enabled Office files are forbidden")
                if not trusted_generated:
                    uncompressed = sum(entry.file_size for entry in entries)
                    compressed = sum(entry.compress_size for entry in entries)
                    if uncompressed > self.config.max_ooxml_uncompressed_bytes:
                        raise ArtifactRejectedError(
                            "archive_limit", "OOXML expands beyond the configured limit"
                        )
                    ratio = uncompressed / max(compressed, 1)
                    if ratio > self.config.max_ooxml_compression_ratio:
                        raise ArtifactRejectedError(
                            "archive_ratio", "OOXML compression ratio is unsafe"
                        )
        except zipfile.BadZipFile as exc:
            raise ArtifactRejectedError("magic_mismatch", "Invalid OOXML container") from exc
        finally:
            fileobj.seek(0)

    def _validate_utf8_text(self, fileobj: BinaryIO) -> None:
        decoder = getincrementaldecoder("utf-8")()
        fileobj.seek(0)
        try:
            while chunk := fileobj.read(1024 * 1024):
                if b"\x00" in chunk:
                    self._magic_mismatch()
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise ArtifactRejectedError(
                "invalid_text_encoding", "Text inputs must be UTF-8"
            ) from exc
        finally:
            fileobj.seek(0)

    @staticmethod
    def _magic_mismatch() -> None:
        raise ArtifactRejectedError("magic_mismatch", "File signature does not match extension")
