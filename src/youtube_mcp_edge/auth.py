"""Static Bearer authentication for the edge (Stage 1).

Validates `Authorization: Bearer <token>` at the edge before forwarding. Design
notes:

- **Constant-time comparison.** Stored credentials are held as SHA-256 digests.
  The presented token is hashed and compared with ``hmac.compare_digest`` against
  every stored digest (fixed 32-byte inputs, so no usable length/timing signal).
- **Multiple tokens / rotation.** The store holds a tuple of credentials; a request
  is valid if its token matches ANY stored digest. Rotation = configure the new
  token alongside the old one, then remove the old one once callers have migrated.
- **No per-user identity, no database.** Just a fixed set of shared static
  credentials, matching the Phase 1 static-compat design.
- **Namespace-aware for future OAuth coexistence.** Stage 1 only accepts static
  credentials via ``Authorization: Bearer``. To leave room for an unambiguous
  dispatch strategy once OAuth bearer tokens are added, an operator SHOULD use a
  distinct token prefix namespace (for example ``ytsk_``) that OAuth-issued
  opaque/JWT tokens will never collide with. This module does NOT try to classify
  tokens by shape (no "JWT-looking means OAuth" heuristic) — it only validates the
  credential is a well-formed opaque ``token68`` and checks constant-time
  membership in the static store. The OAuth-vs-static dispatch decision is
  deliberately left to Phase 2.
"""

from __future__ import annotations

import hashlib
import hmac
import re

# RFC 6750 token68 grammar: printable non-space characters excluding the delimiter,
# roughly [A-Za-z0-9-._~+/]+=* . We forbid internal whitespace and control chars.
# Note: this is intentionally STRICTER than RFC 6750's full token68 set (we do not
# accept characters such as `!%&'()*+,;=?@[]^`{|}~`). That is safe (no bypass) and
# keeps generated tokens unambiguous; keep new tokens within the allowed set.
_TOKEN68_RE = re.compile(r"^[A-Za-z0-9\-._~+/]+=*$")


class MalformedAuthorizationError(ValueError):
    """The Authorization header is present but not a well-formed Bearer token."""


def parse_bearer_header(value: str | None) -> str:
    """Extract the opaque token from an `Authorization: Bearer <token>` header.

    Raises MalformedAuthorizationError for a present-but-invalid header (missing
    scheme, empty/multipart token, control chars, edge whitespace). Returns the
    token string on success.

    A missing header is NOT an error here — it just means "no credential"; the
    caller decides the 401. (Callers should treat an empty/missing header the same
    as no credential.)
    """
    if value is None:
        raise MalformedAuthorizationError("missing Authorization header")
    # Scheme is case-insensitive per RFC 7235/6750 usage. Must be exactly
    # "Bearer" followed by a space (or tabs) and a token.
    match = re.fullmatch(r"[Bb][Ee][Aa][Rr][Ee][Rr][ \t]+(\S+)", value)
    if not match:
        raise MalformedAuthorizationError("malformed Authorization header")
    token = match.group(1)
    if not _TOKEN68_RE.fullmatch(token):
        # token contains characters outside token68 (e.g. internal spaces,
        # control chars, or a string that isn't a single token).
        raise MalformedAuthorizationError("malformed bearer token")
    return token


class StaticTokenStore:
    """Constant-time membership check over a fixed set of static tokens."""

    def __init__(self, tokens: tuple[str, ...]) -> None:
        self._digests: tuple[bytes, ...] = tuple(
            hashlib.sha256(token.encode("utf-8")).digest() for token in tokens
        )

    @property
    def count(self) -> int:
        return len(self._digests)

    def is_empty(self) -> bool:
        return not self._digests

    def validate(self, token: str) -> bool:
        """Return True if `token` matches any configured static credential.

        Constant-time comparison over fixed-size SHA-256 digests. To avoid any
        timing difference based on which digest matches, every stored digest is
        compared and the results are OR-ed (no early exit). The number of
        comparisons is bounded by the (small, operator-controlled) token count.
        """
        presented = hashlib.sha256(token.encode("utf-8")).digest()
        # Evaluate EVERY comparison (no short-circuit on compare_digest itself)
        # so timing reflects only the (constant) number of configured tokens,
        # never which token matched. `any()` runs only after all compares fire.
        results = [
            hmac.compare_digest(presented, stored) for stored in self._digests
        ]
        return any(results)


class StaticAuthenticator:
    """Highest-level Stage 1 auth check: parse -> validate membership."""

    def __init__(self, tokens: tuple[str, ...]) -> None:
        self._store = StaticTokenStore(tokens)

    @property
    def configured(self) -> bool:
        return not self._store.is_empty()

    def authenticate(self, authorization_header: str | None) -> bool:
        """Return True if the request carries a valid static Bearer credential.

        Never logs or exposes the token. A missing or invalid header, or a token
        not in the store, all return False (caller maps to 401).
        """
        if not self.configured:
            return False
        try:
            token = parse_bearer_header(authorization_header)
        except MalformedAuthorizationError:
            return False
        return self._store.validate(token)


__all__ = [
    "StaticTokenStore",
    "StaticAuthenticator",
    "MalformedAuthorizationError",
    "parse_bearer_header",
]