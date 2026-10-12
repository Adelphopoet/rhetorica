from __future__ import annotations

import pytest
from rhetorica_backend.infra.security.oauth import (
    normalize_oauth_scopes,
    pkce_s256_challenge,
    validate_redirect_uri,
    verify_pkce,
)


def test_pkce_s256_uses_rfc7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert pkce_s256_challenge(verifier) == challenge
    assert verify_pkce(verifier, challenge)


def test_redirect_uri_allows_https_and_loopback_only() -> None:
    assert validate_redirect_uri("https://client.example/callback")
    assert validate_redirect_uri("http://127.0.0.1:39123/callback")
    assert validate_redirect_uri("http://localhost:39123/callback")
    with pytest.raises(ValueError, match="invalid_redirect_uri"):
        validate_redirect_uri("http://client.example/callback")
    with pytest.raises(ValueError, match="invalid_redirect_uri"):
        validate_redirect_uri("https://client.example/callback#fragment")


def test_scopes_reject_unknown_capabilities() -> None:
    assert normalize_oauth_scopes("mcp:read mcp:execute") == ("mcp:read", "mcp:execute")
    assert normalize_oauth_scopes("mcp:execute") == ("mcp:read", "mcp:execute")
    with pytest.raises(ValueError, match="invalid_scope"):
        normalize_oauth_scopes("mcp:read admin:all")
