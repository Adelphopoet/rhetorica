from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

MCP_OAUTH_SCOPES = frozenset({"mcp:read", "mcp:execute"})
PUBLIC_CLIENT_AUTH_METHOD = "none"
_OPAQUE_SECRET_PATTERN = re.compile(
    r"^(rh_(?:otx|oac|oat|ort|ocs)_[0-9a-f]{32})\.([A-Za-z0-9_-]{32,})$"
)
_PKCE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_PKCE_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True)
class IssuedOAuthSecret:
    value: str
    prefix: str
    digest: str


@dataclass(frozen=True)
class OAuthClientMetadata:
    client_id: str
    client_name: str
    client_uri: str | None
    redirect_uris: tuple[str, ...]
    grant_types: tuple[str, ...]
    response_types: tuple[str, ...]
    token_endpoint_auth_method: str
    source: str


def issue_oauth_secret(*, kind: str, pepper: str) -> IssuedOAuthSecret:
    markers = {
        "transaction": "otx",
        "code": "oac",
        "access": "oat",
        "refresh": "ort",
        "client_secret": "ocs",
    }
    marker = markers.get(kind)
    if marker is None:
        raise ValueError("unsupported_oauth_secret_kind")
    if not pepper:
        raise ValueError("oauth_token_pepper_required")
    prefix = f"rh_{marker}_{uuid4().hex}"
    value = f"{prefix}.{secrets.token_urlsafe(32)}"
    return IssuedOAuthSecret(value=value, prefix=prefix, digest=digest_oauth_secret(value, pepper=pepper))


def oauth_secret_prefix(value: str, *, kind: str | None = None) -> str | None:
    match = _OPAQUE_SECRET_PATTERN.fullmatch(value)
    if match is None:
        return None
    prefix = match.group(1)
    if kind is not None:
        expected = {
            "transaction": "rh_otx_",
            "code": "rh_oac_",
            "access": "rh_oat_",
            "refresh": "rh_ort_",
            "client_secret": "rh_ocs_",
        }.get(kind)
        if expected is None or not prefix.startswith(expected):
            return None
    return prefix


def digest_oauth_secret(value: str, *, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def verify_secret_digest(value: str, expected_digest: str, *, pepper: str) -> bool:
    return hmac.compare_digest(digest_oauth_secret(value, pepper=pepper), expected_digest)


def normalize_oauth_scopes(scope: str | None) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys((scope or "mcp:read mcp:execute").split()))
    if not requested or set(requested) - MCP_OAUTH_SCOPES:
        raise ValueError("invalid_scope")
    if "mcp:execute" in requested and "mcp:read" not in requested:
        requested = ("mcp:read", *requested)
    return requested


def oauth_registration_source_digest(source: str, *, pepper: str) -> str:
    """Pseudonymize a DCR network source without retaining the raw address."""
    normalized = source.strip().lower() or "unknown"
    return hmac.new(
        pepper.encode(),
        f"dcr-source:{normalized}".encode(),
        hashlib.sha256,
    ).hexdigest()


def validate_redirect_uri(value: str) -> str:
    if len(value) > 2048:
        raise ValueError("invalid_redirect_uri")
    parsed = urlsplit(value)
    if parsed.fragment or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("invalid_redirect_uri")
    if parsed.scheme == "https":
        return value
    if parsed.scheme != "http":
        raise ValueError("invalid_redirect_uri")
    hostname = parsed.hostname.lower()
    if hostname == "localhost":
        return value
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return value
    except ValueError:
        pass
    raise ValueError("invalid_redirect_uri")


def validate_client_registration(data: dict[str, Any]) -> OAuthClientMetadata:
    redirect_values = data.get("redirect_uris")
    if not isinstance(redirect_values, list) or not 1 <= len(redirect_values) <= 20:
        raise ValueError("invalid_client_metadata")
    redirect_uris = tuple(
        dict.fromkeys(validate_redirect_uri(str(value)) for value in redirect_values)
    )
    grant_types = tuple(data.get("grant_types") or ["authorization_code", "refresh_token"])
    response_types = tuple(data.get("response_types") or ["code"])
    auth_method = str(data.get("token_endpoint_auth_method") or PUBLIC_CLIENT_AUTH_METHOD)
    if "authorization_code" not in grant_types or set(grant_types) - {
        "authorization_code",
        "refresh_token",
    }:
        raise ValueError("unsupported_grant_type")
    if response_types != ("code",):
        raise ValueError("unsupported_response_type")
    if auth_method not in {"none", "client_secret_basic", "client_secret_post"}:
        raise ValueError("invalid_client_metadata")
    client_name = str(data.get("client_name") or "MCP client").strip()
    if not client_name or len(client_name) > 255:
        raise ValueError("invalid_client_metadata")
    client_uri_value = data.get("client_uri")
    client_uri = str(client_uri_value) if client_uri_value else None
    if client_uri:
        validate_https_url(client_uri)
    return OAuthClientMetadata(
        client_id=str(data.get("client_id") or ""),
        client_name=client_name,
        client_uri=client_uri,
        redirect_uris=redirect_uris,
        grant_types=grant_types,
        response_types=response_types,
        token_endpoint_auth_method=auth_method,
        source="dcr",
    )


def validate_https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("https_url_required")
    return value


def pkce_s256_challenge(verifier: str) -> str:
    if not _PKCE_VERIFIER_PATTERN.fullmatch(verifier):
        raise ValueError("invalid_code_verifier")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def validate_pkce_challenge(challenge: str, method: str) -> str:
    if method != "S256" or not _PKCE_CHALLENGE_PATTERN.fullmatch(challenge):
        raise ValueError("invalid_request")
    return challenge


def verify_pkce(verifier: str, challenge: str) -> bool:
    try:
        actual = pkce_s256_challenge(verifier)
    except ValueError:
        return False
    return secrets.compare_digest(actual, challenge)


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _resolve_public_addresses(hostname: str, port: int) -> frozenset[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    addresses = frozenset(str(info[4][0]) for info in infos)
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise ValueError("unsafe_client_metadata_address")
    return addresses


async def resolve_client_id_metadata(
    client_id: str,
    *,
    timeout_seconds: float = 5.0,
    max_bytes: int = 64 * 1024,
) -> OAuthClientMetadata:
    """Resolve a CIMD without redirects, environment proxies, or private network access."""
    validate_https_url(client_id)
    parsed = urlsplit(client_id)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("invalid_client_metadata")
    port = parsed.port or 443
    before = await _resolve_public_addresses(hostname, port)
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"Accept": "application/json"},
    ) as client, client.stream("GET", client_id) as response:
        if response.status_code != 200:
            raise ValueError("client_metadata_unavailable")
        network_stream = response.extensions.get("network_stream")
        if network_stream is not None and hasattr(network_stream, "get_extra_info"):
            server_address = network_stream.get_extra_info("server_addr")
            if (
                isinstance(server_address, tuple)
                and server_address
                and not _is_public_ip(str(server_address[0]))
            ):
                raise ValueError("unsafe_client_metadata_address")
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            raise ValueError("invalid_client_metadata")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("client_metadata_too_large")
            chunks.append(chunk)
    after = await _resolve_public_addresses(hostname, port)
    if not before & after:
        raise ValueError("client_metadata_dns_changed")
    try:
        payload = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_client_metadata") from exc
    if not isinstance(payload, dict) or payload.get("client_id") != client_id:
        raise ValueError("invalid_client_metadata")
    metadata = validate_client_registration(payload)
    if metadata.token_endpoint_auth_method != PUBLIC_CLIENT_AUTH_METHOD:
        raise ValueError("invalid_client_metadata")
    return OAuthClientMetadata(
        client_id=client_id,
        client_name=metadata.client_name,
        client_uri=metadata.client_uri,
        redirect_uris=metadata.redirect_uris,
        grant_types=metadata.grant_types,
        response_types=metadata.response_types,
        token_endpoint_auth_method=metadata.token_endpoint_auth_method,
        source="cimd",
    )
