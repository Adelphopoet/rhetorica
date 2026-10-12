from rhetorica_backend.infra.security.api_tokens import validate_api_token_request


def test_execute_scope_implies_mcp_read() -> None:
    assert validate_api_token_request(
        scopes=["mcp:execute"],
        lifetime_days=90,
    ) == ("mcp:read", "mcp:execute")


def test_implied_scope_preserves_other_scope_order() -> None:
    assert validate_api_token_request(
        scopes=["api:read", "mcp:execute"],
        lifetime_days=90,
    ) == ("api:read", "mcp:read", "mcp:execute")
