"""Infrastructure adapters for the governed coding runtime."""

from rhetorica_backend.infra.coding.archives import ArchivePolicy, SafeArchive
from rhetorica_backend.infra.coding.repository_snapshot import (
    PreparedRepositorySnapshot,
    TrustedRepositorySnapshotService,
)

__all__ = [
    "ArchivePolicy",
    "GitLabPublicationService",
    "PreparedRepositorySnapshot",
    "SafeArchive",
    "SignedS3CodingArtifactRelay",
    "TrustedRepositorySnapshotService",
]


def __getattr__(name: str) -> object:
    if name == "GitLabPublicationService":
        from rhetorica_backend.infra.coding.gitlab_publisher import GitLabPublicationService

        return GitLabPublicationService
    if name == "SignedS3CodingArtifactRelay":
        from rhetorica_backend.infra.coding.artifact_relay import SignedS3CodingArtifactRelay

        return SignedS3CodingArtifactRelay
    raise AttributeError(name)
