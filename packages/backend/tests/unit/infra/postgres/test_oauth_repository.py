from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from rhetorica_backend.infra.postgres.oauth_repository import OAuthRepository
from rhetorica_backend.infra.postgres.session import DatabaseRuntime
from rhetorica_backend.infra.security.oauth import (
    OAuthClientMetadata,
    issue_oauth_secret,
)

PEPPER = "oauth-test-pepper-0000000000000000"
RESOURCE = "https://api.example.com/mcp"


def _repository(tmp_path: Path) -> OAuthRepository:
    runtime = DatabaseRuntime(f"sqlite:///{tmp_path}/oauth.db")
    runtime.create_schema()
    return OAuthRepository(runtime, pepper=PEPPER)


def _client(client_id: str = "rh_oc_test") -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_id=client_id,
        client_name="Test MCP",
        client_uri="https://client.example.com",
        redirect_uris=("https://client.example.com/callback",),
        grant_types=("authorization_code", "refresh_token"),
        response_types=("code",),
        token_endpoint_auth_method="none",
        source="dcr",
    )


def _approved_code(repo: OAuthRepository, *, now: datetime):
    transaction = issue_oauth_secret(kind="transaction", pepper=PEPPER)
    repo.create_consent(
        transaction=transaction,
        client=_client(),
        redirect_uri="https://client.example.com/callback",
        scopes=("mcp:read", "mcp:execute"),
        state="state-1",
        code_challenge="a" * 43,
        resource=RESOURCE,
        lifetime=timedelta(minutes=10),
        now=now,
    )
    code = issue_oauth_secret(kind="code", pepper=PEPPER)
    approved = repo.approve_consent(
        transaction_token=transaction.value,
        code=code,
        user_id="user-1",
        workspace_id="workspace-1",
        code_lifetime=timedelta(minutes=5),
        now=now,
    )
    assert approved is not None
    return code


def test_consent_and_code_are_expiring_one_time_secrets(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    code = _approved_code(repo, now=now)

    consumed = repo.consume_code(
        code_token=code.value,
        client_id="rh_oc_test",
        redirect_uri="https://client.example.com/callback",
        resource=RESOURCE,
        code_challenge="a" * 43,
        now=now + timedelta(minutes=1),
    )
    assert consumed is not None
    assert repo.consume_code(
        code_token=code.value,
        client_id="rh_oc_test",
        redirect_uri="https://client.example.com/callback",
        resource=RESOURCE,
        code_challenge="a" * 43,
        now=now + timedelta(minutes=1),
    ) is None

    expired = _approved_code(repo, now=now)
    assert repo.consume_code(
        code_token=expired.value,
        client_id="rh_oc_test",
        redirect_uri="https://client.example.com/callback",
        resource=RESOURCE,
        code_challenge="a" * 43,
        now=now + timedelta(minutes=6),
    ) is None


def test_refresh_rotates_pair_and_replay_revokes_family(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    code_secret = _approved_code(repo, now=now)
    code = repo.consume_code(
        code_token=code_secret.value,
        client_id="rh_oc_test",
        redirect_uri="https://client.example.com/callback",
        resource=RESOURCE,
        code_challenge="a" * 43,
        now=now,
    )
    assert code is not None
    first_access = issue_oauth_secret(kind="access", pepper=PEPPER)
    first_refresh = issue_oauth_secret(kind="refresh", pepper=PEPPER)
    pair = repo.issue_token_pair(
        code=code,
        access_token=first_access,
        refresh_token=first_refresh,
        access_lifetime=timedelta(hours=1),
        refresh_lifetime=timedelta(days=30),
        now=now,
    )
    assert repo.find_access_token(
        access_token=pair.access_token,
        resource=RESOURCE,
        now=now,
    ) is not None

    second_access = issue_oauth_secret(kind="access", pepper=PEPPER)
    second_refresh = issue_oauth_secret(kind="refresh", pepper=PEPPER)
    status, rotated = repo.rotate_refresh_token(
        refresh_token_value=pair.refresh_token,
        client_id="rh_oc_test",
        resource=RESOURCE,
        requested_scopes=("mcp:read",),
        access_token=second_access,
        refresh_token=second_refresh,
        access_lifetime=timedelta(hours=1),
        now=now + timedelta(minutes=2),
    )
    assert status == "ok"
    assert rotated is not None
    assert rotated.grant.scopes == ("mcp:read",)
    assert repo.find_access_token(
        access_token=pair.access_token,
        resource=RESOURCE,
        now=now + timedelta(minutes=2),
    ) is None

    replay_status, replay_pair = repo.rotate_refresh_token(
        refresh_token_value=pair.refresh_token,
        client_id="rh_oc_test",
        resource=RESOURCE,
        requested_scopes=None,
        access_token=issue_oauth_secret(kind="access", pepper=PEPPER),
        refresh_token=issue_oauth_secret(kind="refresh", pepper=PEPPER),
        access_lifetime=timedelta(hours=1),
        now=now + timedelta(minutes=3),
    )
    assert replay_status == "replayed"
    assert replay_pair is None
    assert repo.find_access_token(
        access_token=rotated.access_token,
        resource=RESOURCE,
        now=now + timedelta(minutes=3),
    ) is None


def test_revocation_by_access_token_revokes_refresh_family(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    code_secret = _approved_code(repo, now=now)
    code = repo.consume_code(
        code_token=code_secret.value,
        client_id="rh_oc_test",
        redirect_uri="https://client.example.com/callback",
        resource=RESOURCE,
        code_challenge="a" * 43,
        now=now,
    )
    assert code is not None
    pair = repo.issue_token_pair(
        code=code,
        access_token=issue_oauth_secret(kind="access", pepper=PEPPER),
        refresh_token=issue_oauth_secret(kind="refresh", pepper=PEPPER),
        access_lifetime=timedelta(hours=1),
        refresh_lifetime=timedelta(days=30),
        now=now,
    )

    assert repo.revoke_token_family(
        token_value=pair.access_token,
        client_id="rh_oc_test",
        now=now + timedelta(minutes=1),
    )
    status, rotated = repo.rotate_refresh_token(
        refresh_token_value=pair.refresh_token,
        client_id="rh_oc_test",
        resource=RESOURCE,
        requested_scopes=None,
        access_token=issue_oauth_secret(kind="access", pepper=PEPPER),
        refresh_token=issue_oauth_secret(kind="refresh", pepper=PEPPER),
        access_lifetime=timedelta(hours=1),
        now=now + timedelta(minutes=2),
    )
    assert status == "invalid"
    assert rotated is None


def test_cleanup_removes_only_expired_records_and_inactive_clients(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    old = now - timedelta(days=120)

    repo.register_client(
        metadata=_client("unused-client"),
        client_id="unused-client",
        client_secret=None,
        registration_source_digest="a" * 64,
        now=old,
    )
    repo.register_client(
        metadata=_client("expired-client"),
        client_id="expired-client",
        client_secret=None,
        registration_source_digest="b" * 64,
        now=old,
    )
    transaction = issue_oauth_secret(kind="transaction", pepper=PEPPER)
    repo.create_consent(
        transaction=transaction,
        client=_client("expired-client"),
        redirect_uri="https://client.example.com/callback",
        scopes=("mcp:read", "mcp:execute"),
        state=None,
        code_challenge="a" * 43,
        resource=RESOURCE,
        lifetime=timedelta(minutes=10),
        now=old,
    )
    code_secret = issue_oauth_secret(kind="code", pepper=PEPPER)
    approved = repo.approve_consent(
        transaction_token=transaction.value,
        code=code_secret,
        user_id="user-1",
        workspace_id="workspace-1",
        code_lifetime=timedelta(minutes=5),
        now=old,
    )
    assert approved is not None
    code = repo.consume_code(
        code_token=code_secret.value,
        client_id="expired-client",
        redirect_uri="https://client.example.com/callback",
        resource=RESOURCE,
        code_challenge="a" * 43,
        now=old,
    )
    assert code is not None
    repo.issue_token_pair(
        code=code,
        access_token=issue_oauth_secret(kind="access", pepper=PEPPER),
        refresh_token=issue_oauth_secret(kind="refresh", pepper=PEPPER),
        access_lifetime=timedelta(hours=1),
        refresh_lifetime=timedelta(days=30),
        now=old,
    )

    repo.register_client(
        metadata=_client("active-client"),
        client_id="active-client",
        client_secret=None,
        registration_source_digest="c" * 64,
        now=old,
    )
    active_transaction = issue_oauth_secret(kind="transaction", pepper=PEPPER)
    repo.create_consent(
        transaction=active_transaction,
        client=_client("active-client"),
        redirect_uri="https://client.example.com/callback",
        scopes=("mcp:read",),
        state=None,
        code_challenge="a" * 43,
        resource=RESOURCE,
        lifetime=timedelta(minutes=10),
        now=now,
    )

    result = repo.cleanup_expired(
        invalid_before=now - timedelta(hours=24),
        inactive_client_before=now - timedelta(days=90),
    )

    assert result.transactions == 1
    assert result.codes == 1
    assert result.grants == 1
    assert result.clients == 2
    assert repo.get_client(client_id="unused-client") is None
    assert repo.get_client(client_id="expired-client") is None
    assert repo.get_client(client_id="active-client") is not None


def test_touch_client_preserves_recent_activity(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    old = datetime(2026, 5, 1, tzinfo=UTC)
    used_at = datetime(2026, 9, 2, tzinfo=UTC)
    repo.register_client(
        metadata=_client("returning-client"),
        client_id="returning-client",
        client_secret=None,
        registration_source_digest="d" * 64,
        now=old,
    )

    assert repo.touch_client(client_id="returning-client", now=used_at)
    stored = repo.get_client(client_id="returning-client")
    assert stored is not None
    assert stored.last_used_at == used_at
