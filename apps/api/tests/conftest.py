from __future__ import annotations

import pytest
from rhetorica_api.app.auth import _user_repository
from rhetorica_api.app.settings import settings


@pytest.fixture(autouse=True)
def disable_api_auth_by_default():
    previous_auth_enabled = settings.auth_enabled
    previous_workspace_id = settings.auth_default_workspace_id
    previous_bootstrap_secret = settings.auth_bootstrap_login_secret
    previous_bootstrap_enabled = settings.auth_bootstrap_enabled
    previous_jwt_secret = settings.auth_jwt_secret
    previous_local_admin_login = settings.auth_local_admin_login
    previous_local_admin_password = settings.auth_local_admin_password
    previous_local_admin_display_name = settings.auth_local_admin_display_name

    settings.auth_enabled = False
    settings.auth_default_workspace_id = "default"
    settings.auth_bootstrap_enabled = False
    settings.auth_bootstrap_login_secret = "test-bootstrap-secret-000000000000"
    settings.auth_jwt_secret = "test-jwt-secret-0000000000000000"
    settings.auth_local_admin_login = ""
    settings.auth_local_admin_password = ""
    settings.auth_local_admin_display_name = "Platform Admin"
    _user_repository.cache_clear()
    try:
        yield
    finally:
        settings.auth_enabled = previous_auth_enabled
        settings.auth_default_workspace_id = previous_workspace_id
        settings.auth_bootstrap_enabled = previous_bootstrap_enabled
        settings.auth_bootstrap_login_secret = previous_bootstrap_secret
        settings.auth_jwt_secret = previous_jwt_secret
        settings.auth_local_admin_login = previous_local_admin_login
        settings.auth_local_admin_password = previous_local_admin_password
        settings.auth_local_admin_display_name = previous_local_admin_display_name
        _user_repository.cache_clear()
