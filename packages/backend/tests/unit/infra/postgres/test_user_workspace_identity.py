from datetime import UTC, datetime

from rhetorica_backend.infra.postgres.user_repository import UserAccountItem


def test_workspace_identity_preserves_rate_limit_and_home_workspace():
    now = datetime.now(UTC)
    user = UserAccountItem(
        user_id="user",
        workspace_id="home",
        login="analyst",
        password_hash=None,
        role="analyst",
        is_active=True,
        auth_provider="local",
        auth_subject="analyst",
        email=None,
        display_name=None,
        api_rate_limit_per_hour=37,
        is_bootstrap_user=False,
        created_at=now,
        updated_at=now,
        last_seen_at=None,
    )
    scoped = user.for_workspace("analytics")
    assert scoped.workspace_id == "analytics"
    assert scoped.home_workspace_id == user.workspace_id == "home"
    assert scoped.api_rate_limit_per_hour == 37
    assert scoped.id == user.id
