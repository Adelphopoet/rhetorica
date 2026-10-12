"""Build the same immutable diff manifest for finished and interrupted work."""

import hashlib
from datetime import UTC, datetime

from rhetorica_backend.contracts.coding import CandidateManifest
from rhetorica_backend.domain.coding.publication_diff import tree_from_candidate_archive


class CodingCandidateManifestFactory:
    def create(self, *, run, base: bytes, payload: bytes, artifact_id: str) -> CandidateManifest:
        before, after = tree_from_candidate_archive(base), tree_from_candidate_archive(payload)
        return CandidateManifest(
            artifact_id=artifact_id,
            workspace_id=run.workspace_id,
            owner_user_id=run.owner_user_id,
            run_id=run.id,
            digest=hashlib.sha256(payload).hexdigest(),
            base_sha=run.repository.base_sha,
            changed_paths=sorted(
                p for p in before.keys() | after.keys() if before.get(p) != after.get(p)
            ),
            size_bytes=len(payload),
            created_at=datetime.now(UTC),
        )
