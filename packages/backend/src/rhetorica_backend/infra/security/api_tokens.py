from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from uuid import uuid4

API_TOKEN_PREFIX = "rh_pat_"  # noqa: S105 - public token format marker
SUPPORTED_API_TOKEN_SCOPES = frozenset(
    {"api:read", "api:write", "mcp:read", "mcp:execute"}
)
SUPPORTED_API_TOKEN_LIFETIMES = frozenset({90, 180, 365})
_TOKEN_PATTERN = re.compile(r"^rh_pat_[0-9a-f]{32}\.[A-Za-z0-9_-]{32,}$")


@dataclass(frozen=True)
class IssuedApiToken:
    token: str
    prefix: str
    digest: str


def issue_api_token(*, pepper: str) -> IssuedApiToken:
    if not pepper:
        raise ValueError("api_token_pepper_required")
    prefix = f"{API_TOKEN_PREFIX}{uuid4().hex}"
    token = f"{prefix}.{secrets.token_urlsafe(32)}"
    return IssuedApiToken(
        token=token,
        prefix=prefix,
        digest=digest_api_token(token, pepper=pepper),
    )


def api_token_prefix(token: str) -> str | None:
    if not _TOKEN_PATTERN.fullmatch(token):
        return None
    return token.split(".", 1)[0]


def digest_api_token(token: str, *, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def validate_api_token_request(*, scopes: list[str], lifetime_days: int) -> tuple[str, ...]:
    normalized_scopes = tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))
    if not normalized_scopes:
        raise ValueError("api_token_scopes_required")
    unknown_scopes = set(normalized_scopes) - SUPPORTED_API_TOKEN_SCOPES
    if unknown_scopes:
        raise ValueError("unsupported_api_token_scope")
    if lifetime_days not in SUPPORTED_API_TOKEN_LIFETIMES:
        raise ValueError("unsupported_api_token_lifetime")
    if "mcp:execute" in normalized_scopes and "mcp:read" not in normalized_scopes:
        execute_index = normalized_scopes.index("mcp:execute")
        normalized_scopes = (
            *normalized_scopes[:execute_index],
            "mcp:read",
            *normalized_scopes[execute_index:],
        )
    return normalized_scopes
