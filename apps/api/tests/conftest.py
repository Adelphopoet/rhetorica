from __future__ import annotations

import pytest
from rhetorica_api.app.auth import _api_token_repository, _user_repository
from rhetorica_api.app.oauth import oauth_repository, oauth_service
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
    previous_keycloak_enabled = settings.auth_keycloak_enabled
    previous_keycloak_issuer_url = settings.auth_keycloak_issuer_url
    previous_keycloak_client_id = settings.auth_keycloak_client_id
    previous_keycloak_admin_groups = settings.auth_keycloak_admin_groups
    previous_keycloak_analyst_groups = settings.auth_keycloak_analyst_groups
    previous_keycloak_require_mapped_group = settings.auth_keycloak_require_mapped_group
    previous_public_url = settings.rhetorica_public_url
    previous_public_api_url = settings.rhetorica_public_api_url
    previous_oauth_pepper = settings.auth_oauth_token_pepper

    settings.auth_enabled = False
    settings.auth_default_workspace_id = "default"
    settings.auth_bootstrap_enabled = False
    settings.auth_bootstrap_login_secret = "test-bootstrap-secret-000000000000"
    settings.auth_jwt_secret = "test-jwt-secret-0000000000000000"
    settings.auth_local_admin_login = ""
    settings.auth_local_admin_password = ""
    settings.auth_local_admin_display_name = "Platform Admin"
    settings.auth_keycloak_enabled = False
    settings.auth_keycloak_issuer_url = ""
    settings.auth_keycloak_client_id = ""
    settings.auth_keycloak_admin_groups = ""
    settings.auth_keycloak_analyst_groups = ""
    settings.auth_keycloak_require_mapped_group = True
    settings.rhetorica_public_url = "http://localhost:3000"
    settings.rhetorica_public_api_url = "http://localhost:8000"
    settings.auth_oauth_token_pepper = "test-oauth-pepper-0000000000000000"
    _user_repository.cache_clear()
    _api_token_repository.cache_clear()
    oauth_repository.cache_clear()
    oauth_service.cache_clear()
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
        settings.auth_keycloak_enabled = previous_keycloak_enabled
        settings.auth_keycloak_issuer_url = previous_keycloak_issuer_url
        settings.auth_keycloak_client_id = previous_keycloak_client_id
        settings.auth_keycloak_admin_groups = previous_keycloak_admin_groups
        settings.auth_keycloak_analyst_groups = previous_keycloak_analyst_groups
        settings.auth_keycloak_require_mapped_group = previous_keycloak_require_mapped_group
        settings.rhetorica_public_url = previous_public_url
        settings.rhetorica_public_api_url = previous_public_api_url
        settings.auth_oauth_token_pepper = previous_oauth_pepper
        _user_repository.cache_clear()
        _api_token_repository.cache_clear()
        oauth_repository.cache_clear()
        oauth_service.cache_clear()
