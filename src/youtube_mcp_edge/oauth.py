"""OAuth Resource Server support for the edge (Stage 3).

This module adds an optional OAuth 2.0 access-token validation path to the
existing edge, alongside the Stage-1 static Bearer path. It does NOT build an
Authorization Server; it only *consumes* an external AS's
issuer / discovery / JWKS metadata and validates the access tokens that
OAuth-capable MCP clients (Claude, LibreChat) present.

Primary validation path (per docs/MULTI_CLIENT_MCP_STAGE_2_5_AUTH_SERVER.md §6):
JWT access tokens signed with an asymmetric algorithm (RS256/384/512,
ES256/384/512), verified locally against the issuer's JWKS. Opaque-token
introspection (RFC 7662) is NOT implemented here; it is a future/optional
extension point (see ``OAuthTokenValidator`` interface notes).

Deterministic auth dispatch (per design decision §7): a Bearer token that
begins with the configured STATIC namespace prefix (default ``ytsk_``) is
validated by the static store ONLY; every other Bearer token is validated by
the OAuth validator ONLY. There is NO cross-domain fallback and NO token-shape
(or "JWT-looking") heuristic.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from jwt import api_jwk
from jwt import exceptions as jwt_exceptions

from .settings import EdgeSettings

logger = logging.getLogger("youtube_mcp_edge.oauth")

# RFC 9728 metadata well-known path (served by the Resource Server / edge).
PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
# Authorization Server metadata (RFC 8414) and OIDC discovery paths tried in
# order when the operator does not pin an explicit JWKS URL.
_AS_METADATA_PATHS = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
)

# Algorithms we accept from a remote issuer key. NEVER accept `none` or
# symmetric HMAC (HS*) using a remotely-supplied public key (algorithm-confusion
# / key-confusion risk). ES256K (secp256k1) is rejected as non-standard.
_ALLOWED_ALGORITHMS = {
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
    "PS256", "PS384", "PS512",
}


class OAuthValidationError(Exception):
    """Raised when an OAuth access token is invalid.

    ``kind`` is a stable machine-readable discriminator used by the edge to pick
    the correct HTTP status + WWW-Authenticate response.
    """

    kind: str

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class MissingOAuthTokenError(OAuthValidationError):
    def __init__(self) -> None:
        super().__init__("missing_token", "Missing access token.")


# ---------------------------------------------------------------------------
# Discovery / JWKS fetch (bounded, SSRF-aware)
# ---------------------------------------------------------------------------


class _JwksProvider:
    """Fetches and caches an AS's JWKS with kid-rotation safety.

    Only fetches from the configured issuer origin (or an operator-pinned
    https JWKS URL). http is allowed only for loopback (hostless dev/test);
    everything else must be https. Client-supplied request headers are never
    used to build these URLs (no forwarded-header trust / no request-driven
    SSRF): URLs come only from static config, not from incoming requests.
    """

    def __init__(self, settings: EdgeSettings, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._owns_client = http_client is None
        self._jwks_url: str | None = None
        self._jwks_accessed: float = 0.0
        self._jwks_ttl: float = 300.0  # refresh window (seconds)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _assert_fetchable(self, url: str) -> None:
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise OAuthValidationError("bad_jwks_url", "JWKS URL is not a fetchable http(s) URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            # Fail closed: never fetch keys over plaintext http to a remote host.
            raise OAuthValidationError("bad_jwks_url", "JWKS URL must be https (http allowed only on loopback)")

    async def _resolve_jwks_url(self) -> str:
        """Return the JWKS URL, resolving it from issuer discovery if not pinned.

        The URL is cached so discovery is performed at most once per process
        (bounded; a JWKS refresh does not re-run discovery).
        """
        if self._jwks_url is not None:
            return self._jwks_url
        pinned = self._settings.oauth_jwks_url
        if pinned:
            self._assert_fetchable(pinned)
            self._jwks_url = pinned
            return pinned

        issuer = self._settings.oauth_issuer
        if not issuer:
            raise OAuthValidationError("bad_issuer", "No OAuth issuer configured.")
        origin = issuer.rstrip("/")
        discovered: str | None = None
        for path in _AS_METADATA_PATHS:
            url = origin + path
            self._assert_fetchable(url)
            try:
                resp = await self._client.get(url)
                if resp.status_code != 200:
                    continue
                doc = resp.json()
            except Exception:  # noqa: BLE001 — try next path
                continue
            jwks_uri = (doc or {}).get("jwks_uri")
            if jwks_uri:
                # The discovered jwks_uri must be https (or loopback http).
                parsed = httpx.URL(jwks_uri)
                if parsed.scheme == "https" or (
                    parsed.scheme == "http" and parsed.host in {"127.0.0.1", "localhost", "::1"}
                ):
                    discovered = jwks_uri
                    break
        if discovered is None:
            raise OAuthValidationError(
                "bad_discovery", "Could not resolve jwks_uri from issuer metadata."
            )
        self._jwks_url = discovered
        return discovered

    async def get_jwks(self, force_refresh: bool = False) -> api_jwk.PyJWKSet:
        """Return a parsed PyJWKSet, refreshing when stale or forced."""
        import time as _t

        jwks_url = await self._resolve_jwks_url()
        now = _t.monotonic()
        cached = getattr(self, "_cached_jwks", None)
        if cached is not None and not force_refresh and (now - self._jwks_accessed) < self._jwks_ttl:
            return cached
        self._assert_fetchable(jwks_url)
        try:
            resp = await self._client.get(jwks_url)
            if resp.status_code != 200:
                raise OAuthValidationError("jwks_unavailable", f"JWKS fetch returned {resp.status_code}")
            data = resp.json()
        except OAuthValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OAuthValidationError("jwks_unavailable", f"JWKS fetch failed: {type(exc).__name__}") from exc
        try:
            key_set = api_jwk.PyJWKSet.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            raise OAuthValidationError("bad_jwks", f"Invalid JWKS document: {type(exc).__name__}") from exc
        self._cached_jwks = key_set
        self._jwks_accessed = _t.monotonic()
        return key_set


# ---------------------------------------------------------------------------
# Token validator
# ---------------------------------------------------------------------------


class OAuthTokenValidator(Protocol):
    """Extension point for future token-validation backends.

    The Stage-3 primary implementation is ``JwtAccessTokenValidator``. A future
    RFC 7662 introspection validator could implement the same signature and be
    selected by configuration, enabling opaque-token support without changing
    the caller.
    """

    def validate(self, token: str) -> dict[str, Any]:
        """Return validated claims, or raise OAuthValidationError."""
        ...


@dataclass
class ValidationResult:
    ok: bool
    claims: dict[str, Any] = field(default_factory=dict)
    error: OAuthValidationError | None = None
    # The OAuth path used: "jwt" (or future "introspection").
    method: str = "jwt"


class OAuthAuthenticator:
    """Highest-level OAuth access-token validation for the edge.

    Handles: JWKS discovery/refresh, kid-based key selection, strict claim
    validation (issuer, aud, exp, nbf, scope, algorithm allow-list), and returns
    a structured ValidationResult so the caller can map failures to the correct
    401/403 + WWW-Authenticate response.
    """

    def __init__(self, settings: EdgeSettings, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._jwks = _JwksProvider(settings, http_client=http_client)

    async def aclose(self) -> None:
        await self._jwks.aclose()

    @property
    def configured(self) -> bool:
        return self._settings.oauth_enabled

    async def validate(self, token: str) -> ValidationResult:
        if not self._settings.oauth_enabled:
            return ValidationResult(ok=False, method="jwt",
                                    error=OAuthValidationError("oauth_disabled", "OAuth not enabled"))
        try:
            claims = await self._verify_jwt(token)
        except OAuthValidationError as exc:
            return ValidationResult(ok=False, method="jwt", error=exc)
        return ValidationResult(ok=True, claims=claims, method="jwt")

    async def _select_key(self, header: dict[str, Any], retry: bool = True) -> api_jwk.PyJWK:
        kid = header.get("kid")
        alg = header.get("alg")
        if alg is None or str(alg).upper() not in _ALLOWED_ALGORITHMS:
            if str(alg).upper() in {"HS256", "HS384", "HS512", "none"}:
                raise OAuthValidationError("algorithm_forbidden", f"Unsupported algorithm {alg!r}")
            raise OAuthValidationError("algorithm_forbidden", f"Unknown/unsupported algorithm {alg!r}")

        jwks = await self._jwks.get_jwks()
        key = self._find_key(jwks, kid)
        if key is None and retry:
            # kid unknown -> the signer likely rotated; force a fresh JWKS and
            # select once more before giving up (rotation tolerance).
            jwks = await self._jwks.get_jwks(force_refresh=True)
            key = self._find_key(jwks, kid)
        if key is None:
            raise OAuthValidationError("key_not_found", "No matching signing key in JWKS (kid mismatch).")
        return key

    @staticmethod
    def _find_key(jwks: api_jwk.PyJWKSet, kid: str | None) -> api_jwk.PyJWK | None:
        if kid is not None:
            for key in jwks.keys:
                if key.key_id == kid:
                    return key
            return None
        # No kid in header: acceptable ONLY if the set has exactly one key
        # (unambiguous). Multiple keys + no kid -> refuse (key confusion).
        if len(jwks.keys) == 1:
            return jwks.keys[0]
        return None

    async def _verify_jwt(self, token: str) -> dict[str, Any]:
        import jwt as pyjwt

        # 1) Parse (unverified) header for kid/alg selection.
        try:
            header = pyjwt.get_unverified_header(token)
        except Exception as exc:  # noqa: BLE001
            raise OAuthValidationError("malformed_token", "Could not parse JWT header.") from exc

        key = await self._select_key(header)

        # 2) Cryptographically verify + validate claims.
        options = {
            "verify_signature": True,
            "verify_exp": True,
            "verify_nbf": True,
            "verify_iat": True,
            "verify_iss": True,
            "verify_aud": self._settings.oauth_audience is not None,
            "verify_sub": False,
            "require_exp": True,
        }
        try:
            claims = pyjwt.decode(
                token,
                key=key,
                algorithms=sorted(_ALLOWED_ALGORITHMS),
                issuer=self._settings.oauth_issuer,
                audience=self._settings.oauth_audience,
                leeway=self._settings.oauth_clock_skew_seconds,
                options=options,  # type: ignore[arg-type]  # dict[str,bool] is accepted at runtime
            )
        except jwt_exceptions.ExpiredSignatureError as exc:
            raise OAuthValidationError("token_expired", "Access token has expired.") from exc
        except jwt_exceptions.ImmatureSignatureError as exc:
            raise OAuthValidationError("token_not_yet_valid", "Access token not valid yet (nbf).") from exc
        except (jwt_exceptions.InvalidIssuerError,) as exc:
            raise OAuthValidationError("bad_issuer", "Access token issuer mismatch.") from exc
        except jwt_exceptions.InvalidAudienceError as exc:
            raise OAuthValidationError("bad_audience", "Access token audience mismatch.") from exc
        except jwt_exceptions.InvalidAlgorithmError as exc:
            raise OAuthValidationError("algorithm_forbidden", "Access token algorithm was rejected.") from exc
        except jwt_exceptions.InvalidSignatureError as exc:
            raise OAuthValidationError("bad_signature", "Access token signature invalid.") from exc
        except jwt_exceptions.MissingRequiredClaimError as exc:
            raise OAuthValidationError("malformed_token", "Access token missing a required claim.") from exc
        except jwt_exceptions.PyJWTError as exc:
            raise OAuthValidationError("malformed_token", f"Access token rejected: {type(exc).__name__}") from exc

        # 3) Required scope check (optional).
        required = self._settings.oauth_required_scope
        if required:
            scopes = str(claims.get("scope", "")).split()
            if required not in scopes:
                raise OAuthValidationError("insufficient_scope",
                                           f"Missing required scope {required!r}.")

        return claims

    # ---- RFC 9728 protected-resource metadata ------------------------------

    @property
    def metadata_url(self) -> str:
        """Absolute URL of the protected-resource metadata document."""
        configured = self._settings.oauth_metadata_url
        if configured:
            return configured
        resource = self._settings.oauth_resource_identifier
        import urllib.parse

        parsed = urllib.parse.urlparse(resource)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else resource
        return origin + PROTECTED_RESOURCE_METADATA_PATH

    def protected_resource_metadata(self) -> dict[str, Any]:
        """RFC 9728 protected-resource metadata for the MCP resource."""
        resource = self._settings.oauth_resource_identifier
        doc: dict[str, Any] = {
            "resource": resource,
            "authorization_servers": list(self._settings.oauth_authorization_servers),
            "scopes_supported": list(self._settings.oauth_scopes_supported),
            "bearer_methods_supported": ["header"],
        }
        required = self._settings.oauth_required_scope
        if required:
            doc["scopes_required"] = [required]
        return doc

    def www_authenticate(self, *, challenge: str = "resource_metadata") -> str:
        """WWW-Authenticate header for the OAuth discovery handshake."""
        if challenge == "resource_metadata":
            url = self.metadata_url
            return f'Bearer resource_metadata="{url}"'
        # fallback: plain Bearer realm challenge
        return 'Bearer realm="youtube-mcp"'


__all__ = [
    "OAuthAuthenticator",
    "OAuthValidationError",
    "MissingOAuthTokenError",
    "ValidationResult",
    "OAuthTokenValidator",
    "PROTECTED_RESOURCE_METADATA_PATH",
]