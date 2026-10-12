from __future__ import annotations

import io
import tarfile

import pytest
from rhetorica_backend.infra.coding.archives import ArchivePolicy, SafeArchive, UnsafeArchiveError


def _archive(name: str, payload: bytes = b"select 1") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def test_safe_archive_extracts_regular_files(tmp_path) -> None:
    payload = _archive("models/orders.sql")

    result = SafeArchive.extract(
        payload, target=str(tmp_path / "repo"), policy=ArchivePolicy(max_total_bytes=1024)
    )

    assert result.paths == ("models/orders.sql",)
    assert (tmp_path / "repo/models/orders.sql").read_text() == "select 1"


@pytest.mark.parametrize("name", ["../secret", "/etc/passwd", "models\\x.sql"])
def test_safe_archive_rejects_traversal(name: str) -> None:
    with pytest.raises(UnsafeArchiveError, match="archive_path_invalid"):
        SafeArchive.validate(_archive(name), policy=ArchivePolicy())


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_safe_archive_rejects_links_and_devices(kind: bytes) -> None:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("models/escape.sql")
        info.type = kind
        info.linkname = "../../secret"
        archive.addfile(info)

    with pytest.raises(UnsafeArchiveError, match="archive_non_regular_entry"):
        SafeArchive.validate(output.getvalue(), policy=ArchivePolicy())


def test_safe_archive_rejects_oversize_payload() -> None:
    with pytest.raises(UnsafeArchiveError, match="archive_file_size_exceeded"):
        SafeArchive.validate(
            _archive("large.bin", b"x" * 11), policy=ArchivePolicy(max_file_bytes=10)
        )
