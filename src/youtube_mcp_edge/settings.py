"""Edge configuration from environment — minimal, env/secret oriented.

Only what Stage 0/1 needs is defined. OAuth / TLS / certificate settings are
deliberately NOT present yet (docs/MULTI_CLIENT_MCP_PHASE1_ARCHITECTURE.md).
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

        return cls(
            upstream_url=upstream,
            host=host,
            port=port,
            static_auth_enabled=static_auth_enabled,
            static_tokens=static_tokens,
            max_request_bytes=max_bytes,
            upstream_timeout_seconds=timeout,
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
) -> EdgeSettings:
    return EdgeSettings(
        upstream_url=upstream_url,
        host=host,
        port=port,
        static_auth_enabled=static_auth_enabled,
        static_tokens=tuple(static_tokens or ()),
        max_request_bytes=max_request_bytes,
        upstream_timeout_seconds=upstream_timeout_seconds,
    )


# Public re-export so `from youtube_mcp_edge.settings import EdgeSettings` reads
# naturally.
__all__ = ["EdgeSettings", "make_settings"]