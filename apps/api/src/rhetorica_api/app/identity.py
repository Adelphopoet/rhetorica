from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.infra.postgres.user_repository import (
    UserAccountItem,
    UserAccountRepository,
)

from rhetorica_api.app.auth import AuthContext, ensure_workspace_access, get_current_auth_context
from rhetorica_api.app.settings import settings


def get_db_runtime():
    """Get database runtime for direct repository access."""
    _, db_runtime = build_execution_service(settings.database_url)
    db_runtime.create_schema()
    return db_runtime


def resolve_current_user(
    workspace_id: str,
    auth_context: AuthContext | None = None,
) -> UserAccountItem:
    """Resolve the current user from auth context or dev bootstrap fallback."""
    if settings.auth_enabled:
        current_auth = auth_context or get_current_auth_context()
        ensure_workspace_access(workspace_id, current_auth)
        return current_auth.user
    return UserAccountRepository(db=get_db_runtime()).ensure_bootstrap_user(workspace_id)
