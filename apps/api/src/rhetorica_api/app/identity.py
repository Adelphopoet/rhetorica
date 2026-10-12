from rhetorica_backend.domain.execution import build_execution_service
from rhetorica_backend.infra.postgres.user_repository import (
    UserAccountItem,
    UserAccountRepository,
)
from rhetorica_backend.infra.postgres.workspace_repository import WorkspaceRepository

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
        return current_auth.user.for_workspace(workspace_id)
    db_runtime = get_db_runtime()
    user = UserAccountRepository(db=db_runtime).ensure_bootstrap_user(workspace_id)
    WorkspaceRepository(
        db=db_runtime,
        default_workspace_id=settings.auth_default_workspace_id,
    ).ensure_home_membership(user_id=user.id)
    return user.for_workspace(workspace_id)
