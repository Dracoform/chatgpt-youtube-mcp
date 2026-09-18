"""Edge configuration from environment — minimal, env/secret oriented.

Only what Stage 0/1/2 needs is defined. OAuth / ACME / certificate-issuance
settings are deliberately NOT present (docs/MULTI_CLIENT_MCP_PHASE1_ARCHITECTURE.md
defers cert provisioning to a later phase).

Stage 2 adds TLS: the edge can terminate HTTPS itself with operator-provided
certificate/key files (`EDGE_TLS_CERT_FILE` / `EDGE_TLS_KEY_FILE`). If neither is
set, the edge serves plain HTTP — intended for (a) local/loopback use and (b) the
external-ingress path where an existing reverse proxy (Caddy/nginx/Traefik)
terminates TLS in front of the edge. Setting exactly one of the two is a
configuration error (fail closed).

Stage 3 adds optional OAuth Resource Server support: the edge validates
OAuth 2.0 access-token JWTs (JWT/JWKS) in addition to static Bearer credentials,
publishes RFC 9728 protected-resource metadata, and performs the MCP `401` +
`WWW-Authenticate` discovery handshake. OAuth is entirely optional: if
`EDGE_OAUTH_ENABLED` is false (default) the edge behaves exactly as static/local
Stages 1–2. The Authorization Server itself is a separate service; the edge only
consumes its issuer/JWKS/discovery metadata.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_flag(name: str, default: bool) -> bool:
    """Parse a boolean env var, failing closed on any unrecognized non-empty value.

    A typo such as `EDGE_STATIC_AUTH_ENABLED=tru` or `TRUE ` must not silently
    disable authentication. Unset -> default; empty -> default; recognized
    true/false -> value; anything else -> ValueError (fail closed).
    """
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be a boolean (true/false/1/0/yes/no/on/off); got {raw!r}"
    )


def _validate_upstream(value: str) -> str:
    """Validate the upstream URL (no relative, no ftp, etc.) and return it."""
    import urllib.parse

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            "EDGE_MCP_UPSTREAM_URL must be an absolute http(s) URL "
            "(e.g. http://youtube-mcp:8765/mcp or "
            "http://127.0.0.1:8765/mcp)"
        )
    return value


def _split_secrets(value: str) -> list[str]:
    """Split a newline- and/or comma-separated secret list, dropping empties.

    Secrets themselves may not contain the separators; callers are expected to
    generate/hold newline- or comma-free tokens.
    """
    result: list[str] = []
    for chunk in value.replace(",", "\n").split("\n"):
        token = chunk.strip()
        if token:
            result.append(token)
    return result


def _split_list(value: str) -> list[str]:
    """Split a comma-separated list of tokens (scopes, etc.), dropping empties."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _validate_http_url(value: str, name: str) -> str:
    """Validate an absolute http(s) URL and return it (fail closed)."""
    import urllib.parse

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            f"{name} must be an absolute http(s) URL; got {value!r}"
        )
    return value


@dataclass(frozen=True)
class EdgeSettings:
    # Private upstream MCP core (Streamable HTTP endpoint), e.g.
    # http://youtube-mcp:8765/mcp in Compose, or http://127.0.0.1:8765/mcp locally.
    upstream_url: str = "http://127.0.0.1:8765/mcp"
    # Edge bind host/port. Default loopback only: the edge is the future public
    # exposure point, but Stage 0/1 default local no-auth exposure is loopback.
    host: str = "127.0.0.1"
    port: int = 8766
    # Static Bearer auth. The only Stage 1 mechanism; X-API-Key is intentionally
    # not enabled by default.
    static_auth_enabled: bool = True
    # One or more static Bearer credentials (rotation = multiple tokens).
    static_tokens: tuple[str, ...] = ()
    # Request-body size cap (bytes) enforced at the edge before any tool work.
    max_request_bytes: int = 2 * 1024 * 1024  # 2 MiB
    # Timeout for the upstream request as a whole (seconds).
    upstream_timeout_seconds: float = 120.0
    # Optional TLS termination at the edge. Both must be set together:
    # - tls_cert_file: PEM-encoded certificate (chain) path.
    # - tls_key_file:  PEM-encoded private key path.
    # If both are set the edge serves HTTPS on `port`. If neither is set the edge
    # serves plain HTTP (local/loopback, or behind an external TLS-terminating
    # ingress). Setting exactly one of them raises (fail closed).
    tls_cert_file: str | None = None
    tls_key_file: str | None = None

    # ---- OAuth Resource Server (Stage 3, optional) -------------------------
    # When oauth_enabled is False (the default) the edge is unaffected and
    # behaves exactly as Stages 1–2 (static Bearer and/or local no-auth).
    oauth_enabled: bool = False
    # Authorization Server issuer URL (absolute http(s)). Required when enabled.
    oauth_issuer: str | None = None
    # Optional explicit JWKS URL; when unset the edge discovers it from the
    # issuer metadata (RFC 8414 / OIDC discovery). Both are absolute http(s).
    oauth_jwks_url: str | None = None
    # Expected audience/resource identifier for access tokens. When set, the
    # token's `aud` must include it (strict); when unset, audience is NOT
    # asserted (operators SHOULD set it to harden against token-spraying).
    oauth_audience: str | None = None
    # Required scope that every accepted access token must carry (optional).
    oauth_required_scope: str = ""
    # Allowed access-token signature algorithms (comma separated). AS-only:
    # never accept `none` or symmetric HMAC with a remote public key.
    oauth_algorithms: tuple[str, ...] = ("RS256", "RS384", "RS512",
                                         "ES256", "ES384", "ES512")
    # Clock-skew tolerance (seconds) applied to `exp`/`nbf`.
    oauth_clock_skew_seconds: float = 5.0
    # The protected resource identifier advertised in RFC 9728 metadata and
    # (when audience enforcement is desired) expected in the token's `aud`.
    # e.g. https://example.org/mcp
    oauth_resource_identifier: str = ""
    # Absolute URL where the RFC 9728 protected-resource metadata is exposed,
    # used in the 401 WWW-Authenticate pointer. When unset it is derived from
    # oauth_resource_identifier's origin + /.well-known/oauth-protected-resource.
    oauth_metadata_url: str = ""
    # List of AS identifiers (issuer URLs) to advertise in authorization_servers.
    oauth_authorization_servers: tuple[str, ...] = ()
    # Scopes to advertise in scopes_supported.
    oauth_scopes_supported: tuple[str, ...] = ()
    # Reserved namespace prefix that marks a Bearer token as STATIC (never OAuth).
    # OAuth-issued tokens are guaranteed not to carry this prefix; a colliding
    # issuers policy (external AS) must not mint tokens in this namespace.
    static_token_prefix: str = "ytsk_"

    @property
    def tls_enabled(self) -> bool:
        return self.tls_cert_file is not None and self.tls_key_file is not None

    @classmethod
    def from_env(cls) -> "EdgeSettings":
        host = os.getenv("EDGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
        try:
            port = int(os.getenv("EDGE_PORT", "8766"))
        except ValueError:
            port = 8766
        if not (0 < port < 65536):
            port = 8766

        upstream = os.getenv(
            "EDGE_MCP_UPSTREAM_URL", "http://127.0.0.1:8765/mcp"
        ).strip()
        if not upstream:
            # Fail closed on an unusable upstream rather than silently proxying
            # nowhere.
            raise ValueError("EDGE_MCP_UPSTREAM_URL must be a non-empty http(s) URL")
        upstream = _validate_upstream(upstream)

        static_auth_enabled = _bool_flag("EDGE_STATIC_AUTH_ENABLED", default=True)
        static_tokens = tuple(_split_secrets(os.getenv("EDGE_STATIC_TOKENS", "")))

        try:
            max_bytes = int(os.getenv("EDGE_MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
        except ValueError:
            max_bytes = 2 * 1024 * 1024
        if max_bytes < 1024:
            # A pathological tiny cap would break even the smallest tool call.
            max_bytes = 2 * 1024 * 1024

        try:
            timeout = float(os.getenv("EDGE_UPSTREAM_TIMEOUT_SECONDS", "120"))
        except ValueError:
            timeout = 120.0
        if timeout <= 0:
            timeout = 120.0

        tls_cert = (os.getenv("EDGE_TLS_CERT_FILE") or "").strip() or None
        tls_key = (os.getenv("EDGE_TLS_KEY_FILE") or "").strip() or None
        if (tls_cert is None) != (tls_key is None):
            raise ValueError(
                "EDGE_TLS_CERT_FILE and EDGE_TLS_KEY_FILE must be set together "
                "or both left unset; setting only one is a configuration error."
            )
        for label, path in (("cert", tls_cert), ("key", tls_key)):
            if path is not None and not os.path.isfile(path):
                raise ValueError(
                    f"EDGE_TLS_{'CERT' if label == 'cert' else 'KEY'}_FILE "
                    f"points to a file that does not exist or is not readable: {path!r}"
                )

        # ---- OAuth Resource Server (optional, fail closed) -----------------
        oauth_enabled = _bool_flag("EDGE_OAUTH_ENABLED", default=False)
        oauth_issuer = (os.getenv("EDGE_OAUTH_ISSUER") or "").strip() or None
        oauth_jwks_url = (os.getenv("EDGE_OAUTH_JWKS_URL") or "").strip() or None
        oauth_audience = (os.getenv("EDGE_OAUTH_AUDIENCE") or "").strip() or None
        oauth_required_scope = (os.getenv("EDGE_OAUTH_REQUIRED_SCOPE") or "").strip()
        oauth_resource_identifier = (
            os.getenv("EDGE_OAUTH_RESOURCE_IDENTIFIER") or ""
        ).strip()
        oauth_metadata_url = (
            os.getenv("EDGE_OAUTH_METADATA_URL") or ""
        ).strip()
        oauth_algorithms = tuple(
            _split_list(os.getenv("EDGE_OAUTH_ALGORITHMS", ""))
        ) or ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

        try:
            oauth_skew = float(
                os.getenv("EDGE_OAUTH_CLOCK_SKEW_SECONDS", "5.0")
            )
        except ValueError:
            oauth_skew = 5.0
        if oauth_skew < 0:
            oauth_skew = 5.0

        static_prefix = (os.getenv("EDGE_STATIC_TOKEN_PREFIX") or "ytsk_").strip()
        if not static_prefix:
            static_prefix = "ytsk_"

        if oauth_enabled:
            # Fail closed: issuer + resource identifier are required.
            if not oauth_issuer:
                raise ValueError(
                    "EDGE_OAUTH_ENABLED is set but EDGE_OAUTH_ISSUER is missing; "
                    "refusing to start an OAuth-enabled edge without an issuer."
                )
            oauth_issuer = _validate_http_url(oauth_issuer, "EDGE_OAUTH_ISSUER")
            if not oauth_resource_identifier:
                raise ValueError(
                    "EDGE_OAUTH_ENABLED is set but EDGE_OAUTH_RESOURCE_IDENTIFIER "
                    "is missing (the public protected MCP URL); refusing to start "
                    "an OAuth-enabled edge without a resource identifier."
                )
            oauth_resource_identifier = _validate_http_url(
                oauth_resource_identifier, "EDGE_OAUTH_RESOURCE_IDENTIFIER"
            )
            if oauth_jwks_url:
                oauth_jwks_url = _validate_http_url(oauth_jwks_url, "EDGE_OAUTH_JWKS_URL")

        authorization_servers = tuple(
            _split_list(
                os.getenv(
                    "EDGE_OAUTH_AUTHORIZATION_SERVERS",
                    oauth_issuer or "",
                )
            )
        )
        if not authorization_servers and oauth_issuer:
            authorization_servers = (oauth_issuer,)

        scopes_supported = tuple(
            _split_list(os.getenv("EDGE_OAUTH_SCOPES_SUPPORTED", ""))
        )

        return cls(
            upstream_url=upstream,
            host=host,
            port=port,
            static_auth_enabled=static_auth_enabled,
            static_tokens=static_tokens,
            max_request_bytes=max_bytes,
            upstream_timeout_seconds=timeout,
            tls_cert_file=tls_cert,
            tls_key_file=tls_key,
            oauth_enabled=oauth_enabled,
            oauth_issuer=oauth_issuer,
            oauth_jwks_url=oauth_jwks_url,
            oauth_audience=oauth_audience,
            oauth_required_scope=oauth_required_scope,
            oauth_algorithms=oauth_algorithms,
            oauth_clock_skew_seconds=oauth_skew,
            oauth_resource_identifier=oauth_resource_identifier,
            oauth_metadata_url=oauth_metadata_url,
            oauth_authorization_servers=authorization_servers,
            oauth_scopes_supported=scopes_supported,
            static_token_prefix=static_prefix,
        )


# Convenience alias used by tests and app to derive a known-good settings object.
def make_settings(
    *,
    upstream_url: str = "http://127.0.0.1:8765/mcp",
    host: str = "127.0.0.1",
    port: int = 8766,
    static_auth_enabled: bool = True,
    static_tokens: list[str] | None = None,
    max_request_bytes: int = 2 * 1024 * 1024,
    upstream_timeout_seconds: float = 120.0,
    tls_cert_file: str | None = None,
    tls_key_file: str | None = None,
    oauth_enabled: bool = False,
    oauth_issuer: str | None = None,
    oauth_jwks_url: str | None = None,
    oauth_audience: str | None = None,
    oauth_required_scope: str = "",
    oauth_algorithms: tuple[str, ...] | None = None,
    oauth_clock_skew_seconds: float = 5.0,
    oauth_resource_identifier: str = "",
    oauth_metadata_url: str = "",
    oauth_authorization_servers: list[str] | tuple[str, ...] | None = None,
    oauth_scopes_supported: list[str] | tuple[str, ...] | None = None,
    static_token_prefix: str = "ytsk_",
) -> EdgeSettings:
    if (tls_cert_file is None) != (tls_key_file is None):
        raise ValueError("tls_cert_file and tls_key_file must be set together or both unset")
    return EdgeSettings(
        upstream_url=upstream_url,
        host=host,
        port=port,
        static_auth_enabled=static_auth_enabled,
        static_tokens=tuple(static_tokens or ()),
        max_request_bytes=max_request_bytes,
        upstream_timeout_seconds=upstream_timeout_seconds,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
        oauth_enabled=oauth_enabled,
        oauth_issuer=oauth_issuer,
        oauth_jwks_url=oauth_jwks_url,
        oauth_audience=oauth_audience,
        oauth_required_scope=oauth_required_scope,
        oauth_algorithms=oauth_algorithms or ("RS256", "RS384", "RS512",
                                              "ES256", "ES384", "ES512"),
        oauth_clock_skew_seconds=oauth_clock_skew_seconds,
        oauth_resource_identifier=oauth_resource_identifier,
        oauth_metadata_url=oauth_metadata_url,
        oauth_authorization_servers=(
            tuple(oauth_authorization_servers or ())
            if oauth_authorization_servers
            else ((oauth_issuer,) if oauth_issuer else ())
        ),
        oauth_scopes_supported=tuple(oauth_scopes_supported or ()),
        static_token_prefix=static_token_prefix,
    )


# Public re-export so `from youtube_mcp_edge.settings import EdgeSettings` reads
# naturally.
__all__ = ["EdgeSettings", "make_settings"]