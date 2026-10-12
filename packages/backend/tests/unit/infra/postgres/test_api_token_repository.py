from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rhetorica_backend.infra.postgres.api_token_repository import (
    PersonalAccessTokenRepository,
)
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.postgres.user_repository import UserAccountRepository
from rhetorica_backend.infra.security.api_tokens import issue_api_token
from rhetorica_backend.infra.security.passwords import hash_password


def test_api_token_repository_keeps_digest_only_and_slides_expiry(tmp_path) -> None:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/tokens.db")
    runtime.create_schema()
    user = UserAccountRepository(db=runtime).create_local_user(
        workspace_id="default",
        login="analyst",
        password_hash=hash_password("password-123"),
        role="analyst",
    )
    issued_at = datetime(2026, 9, 1, tzinfo=UTC)
    issued = issue_api_token(pepper="test-pepper")
    repository = PersonalAccessTokenRepository(db=runtime)
    item = repository.create(
        user_id=user.id,
        workspace_id=user.workspace_id,
        name="MCP",
        token_prefix=issued.prefix,
        token_digest=issued.digest,
        scopes=("mcp:read", "mcp:execute"),
        lifetime_days=90,
        auto_renew=True,
        now=issued_at,
    )

    assert item.expires_at == issued_at + timedelta(days=90)
    assert issued.token not in repr(item)
    authenticated = repository.find_for_authentication(
        token_prefix=issued.prefix,
        token_digest=issued.digest,
        now=issued_at + timedelta(days=10),
    )
    assert authenticated is not None
    renewed = repository.record_successful_use(
        token_id=item.id,
        now=issued_at + timedelta(days=10),
    )
    assert renewed is not None
    assert renewed.expires_at == issued_at + timedelta(days=100)
