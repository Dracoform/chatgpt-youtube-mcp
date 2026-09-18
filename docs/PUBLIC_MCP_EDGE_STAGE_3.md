# Public Remote MCP edge — Stage 3 (OAuth Resource Server)

Status: Stage 3 of the multi-client MCP work. This stage adds **optional OAuth
2.0 Resource Server support** to the existing public edge, letting
OAuth-capable MCP clients (Claude, LibreChat) connect alongside the existing
static-Bearer and local/tunnel paths.

> The **Authorization Server is NOT built here** and is **not a hard
> dependency**. It is a separate service (Keycloak by default, Zitadel or any
> OIDC/OAuth2 AS as an alternative) that the edge only *consumes* via its
> issuer discovery + JWKS. Static-only and tunnel-only deployments need none of
> it.

## Topology

```
OAuth-capable client (Claude / LibreChat)
        │  Bearer access token (JWT, issued by the AS)
        ▼
Public MCP Edge  [ auth dispatch ]
  - OAuth Resource Server
  - JWT/JWKS validation
  - RFC 9728 protected-resource metadata
        │
        ▼  private HTTP
Private MCP Core  (unchanged, loopback-only)

Authorization Server (separate) ──► issuer discovery + JWKS (consumed by edge)

Static clients (DeepSeek Harness, LibreChat static)
        │  Authorization: Bearer ytsk_...
        ▼
same Public MCP Edge  (static validator only)

OpenAI Secure MCP Tunnel ──► same Private MCP Core (unchanged, direct)
```

## Auth dispatch (deterministic)

Both static credentials and OAuth access tokens arrive as
`Authorization: Bearer <token>`. Dispatch is **deterministic** and **never falls
through between domains**:

- token begins with the reserved **static namespace prefix** (`EDGE_STATIC_TOKEN_PREFIX`,
  default `ytsk_`) → validated by the **static store only** (constant-time). Invalid ⇒
  `401`; it is **never** retried as OAuth.
- any other Bearer token → validated by the **OAuth validator only**. Invalid ⇒
  `401`/`403`; it is **never** retried as static.
- **no** `Authorization` header:
  - OAuth enabled → `401` + OAuth discovery `WWW-Authenticate` (lets
    Claude/LibreChat begin the OAuth flow).
  - static-only → `401` + static Bearer challenge (unchanged).
  - neither → local no-auth, allowed (unchanged).

The classification is purely the reserved prefix — **no** "JWT-looking means
OAuth" heuristic, **no** token-shape guessing. An OAuth-issued token must never
carry the reserved static prefix (guaranteed by the AS / operator policy).

In **static-only deployments** the OAuth domain does not exist, so any Bearer
token is evaluated by the static validator exactly as before — preserving
existing static Bearer behavior and DeepSeek Harness compatibility.

## Configuration

Additive and optional. `EDGE_OAUTH_ENABLED` defaults to `false`; when false,
the edge behaves exactly as Stages 1–2. `EdgeSettings.from_env()` **fails
closed** on invalid/incomplete OAuth config (e.g. enabled without issuer or
without a resource identifier).

| Variable | Default | Notes |
|---|---|---|
| `EDGE_OAUTH_ENABLED` | `false` | Master switch. Off ⇒ static/local behavior unchanged |
| `EDGE_OAUTH_ISSUER` | *(required if enabled)* | AS issuer URL (absolute http(s)); fail-closed if missing when enabled |
| `EDGE_OAUTH_JWKS_URL` | *(auto from issuer discovery)* | Optional override; else discovered from RFC 8414 / OIDC metadata |
| `EDGE_OAUTH_AUDIENCE` | *(optional)* | Expected `aud`/resource identifier. When set, enforced strictly. **SHOULD set** to harden against token-spraying |
| `EDGE_OAUTH_REQUIRED_SCOPE` | *(optional)* | Required scope every accepted access token must carry; else `403 insufficient_scope` |
| `EDGE_OAUTH_ALGORITHMS` | `RS256,RS384,RS512,ES256,ES384,ES512` | Accepted signature algorithms (asymmetric only; `none`/HS* always rejected) |
| `EDGE_OAUTH_CLOCK_SKEW_SECONDS` | `5.0` | Leeway for `exp`/`nbf`/`iat` validation |
| `EDGE_OAUTH_RESOURCE_IDENTIFIER` | *(required if enabled)* | Public protected MCP URL (RFC 9728 `resource`); fail-closed if missing when enabled |
| `EDGE_OAUTH_METADATA_URL` | *(derived)* | Absolute URL of the RFC 9728 metadata; default = origin + `/.well-known/oauth-protected-resource` |
| `EDGE_OAUTH_AUTHORIZATION_SERVERS` | *(default = issuer)* | `authorization_servers` list advertised in metadata |
| `EDGE_OAUTH_SCOPES_SUPPORTED` | *(empty)* | `scopes_supported` advertised in metadata |
| `EDGE_STATIC_TOKEN_PREFIX` | `ytsk_` | Reserved static namespace used for dispatch |

## JWT / JWKS validation

Primary validation path (per Stage-2.5 ADR §6): **JWT access tokens** verified
locally against the issuer's JWKS.

- Signature verified against the key selected by `kid` from the issuer JWKS
  (unknown `kid` → force a JWKS refresh, then fail closed → key-rotation safe).
- `kid` missing with a multi-key JWKS → refused (key confusion prevented).
- Strict claims: `iss == issuer`, `aud` (when configured), `exp`, `nbf`, `iat`,
  required `exp`, required scope (when configured).
- Algorithm allow-list (asymmetric only); unsigned (`alg:none`) and HMAC (HS*)
  rejected → no algorithm confusion.
- JWKS fetched over **https** (http only allowed on loopback) from config/issuer
  URLs only — URLs are never built from client-supplied headers (no request-
  driven SSRF, no forwarded-header trust).
- Established maintained library: **PyJWT** (`pyjwt[crypto]`, via `mcp`'s tree,
  declared explicitly in the `edge` extra).
- **JWT/JWKS only in Stage 3.** RFC 7662 opaque-token introspection is **not**
  implemented; a clearly-defined `OAuthTokenValidator` interface (see
  `src/youtube_mcp_edge/oauth.py`) is the extension point for a future optional
  introspection validator.

## Metadata / discovery behavior

- `GET /.well-known/oauth-protected-resource` (RFC 9728) — served only when
  OAuth is enabled (else `404`). Returns `resource`,
  `authorization_servers`, `scopes_supported`/`scopes_required`, and
  `bearer_methods_supported: ["header"]`.
- On an unauthenticated `/mcp` request with OAuth enabled, the edge returns
  `401` + `WWW-Authenticate: Bearer resource_metadata="<metadata-url>"` — the
  MCP discovery handshake Claude/LibreChat use to begin OAuth.
- On an invalid OAuth token: `401` + `Bearer error="invalid_token",
  resource_metadata="..."`.
- On missing required scope: `403` + `Bearer error="insufficient_scope",
  scope="<required>"`.
- Static-only deployments advertise no OAuth metadata (unchanged).

## Coexistence modes (A–G all supported)

| Mode | config | behavior |
|---|---|---|
| A static only | `EDGE_STATIC_AUTH_ENABLED=true`, OAuth off | Stage-1 behavior, any Bearer → static validator |
| B OAuth only | static off, `EDGE_OAUTH_ENABLED=true` | Bearer (non-`ytsk_*`) → JWT/JWKS; `ytsk_*` rejected (never OAuth) |
| C static + OAuth | both on | dispatch by prefix; no cross-domain fallback |
| D tunnel + static | tunnel to core + static edge | independent |
| E tunnel + OAuth | tunnel to core + OAuth edge | independent |
| F tunnel + static + OAuth | all three | all independent; core shared, stateless |
| G local no-auth | both off | allowed (unchanged) |

No mode silently weakens another; the core stays private; the tunnel always
targets the core directly (never rerouted through the edge).

## Keycloak reference setup (Stage-3 E2E)

Keycloak is the **reference implementation** used for Stage-3 verification only
and is **not a hard project dependency**.

1. Run a Keycloak and create a realm + a **public (PKCE)** OAuth client +
   a user (see the committed `docs/` and the Stage-3 E2E harness in the repo
   history / scratch notes for exact admin-API calls).
2. Configure the edge:
   ```
   EDGE_OAUTH_ENABLED=true
   EDGE_OAUTH_ISSUER=https://auth.example.org/realms/<realm>
   EDGE_OAUTH_RESOURCE_IDENTIFIER=https://mcp.example.org/mcp
   EDGE_OAUTH_AUDIENCE=account            # match the Keycloak-realm token audience
   EDGE_OAUTH_REQUIRED_SCOPE=             # set if your client issues it
   ```
   (JWKS is auto-discovered from the issuer metadata; set
   `EDGE_OAUTH_JWKS_URL` only to override.)
3. Run the edge (`docker compose --profile edge-public up` or
   `uv run youtube-mcp-edge` + SSL). OAuth-capable clients point at
   `https://…/mcp`; static clients keep `Authorization: Bearer ytsk_...`.

## Claude / LibreChat status

- **Protocol-level validation completed** (Stage 3): discovery metadata
  (RFC 9728), the `401` + `WWW-Authenticate` handshake, PKCE-compatible AS
  setup, JWT access-token acceptance, wrong-issuer/audience/expiry/scope
  rejection, and real MCP client `tools/list` + live transcript calls all pass
  against real containers + real Keycloak.
- **Real Claude / claude.ai connector E2E**: **not run** (requires a live
  claude.ai account + a public endpoint). The implemented discovery/metadata/401
  sequence matches the documented Claude requirements; a real connector
  integration remains a pending account-gated test.
- **Real LibreChat OAuth-backed E2E**: **not run** on this host. LibreChat's
  static/`apiKey` path was validated in Stage 2; its OAuth (non-static) flow
  against this edge is validated at the protocol/MCP-client level, and a live
  LibreChat instance test remains pending.

## Security assumptions & limitations

- The edge never forwards the `Authorization` header to the core and never logs
  credentials (verified in real-container logs).
- JWT validation is strict; `algorithm`, `issuer`, `aud`, `exp`/`nbf`, and
  `scope` are enforced, and no unsigned/HMAC is accepted.
- JWKS/discovery endpoints are fetched from config/issuer only, over https
  (loopback http for local dev). No client-supplied `X-Forwarded-*` or other
  headers are trusted for URL construction.
- `EDGE_OAUTH_AUDIENCE` is **optional by design** (not all ASes populate `aud`
  the same way); operators SHOULD set it. When unset, `aud` is not asserted —
  issuer + signature + scope remain enforced. This is a documented trade-off,
  not a bypass.
- Required-scope enforcement is only active when `EDGE_OAUTH_REQUIRED_SCOPE` is
  configured.
- Opaque-token **introspection is not implemented** (future/optional extension).
- If the JWKS endpoint is temporarily unreachable at validation time, the edge
  returns `401` (fail-safe), never fail-open; it does not crash at startup for a
  transient AS outage (first-token validation triggers a refresh).

## Tests

Stage-3 test coverage (`tests/test_oauth.py`, 29 tests) uses a fake RSA AS over
httpx `MockTransport`:
dispatch (valid/invalid static, valid/invalid OAuth, no cross-domain),
missing-header discovery handshake, JWT claims (bad sig, wrong issuer, wrong
audience, expired, not-before, missing scope), algorithm security (alg:none,
HS256, malformed, kid-confusion), metadata endpoint + 404-when-OAuth-off,
coexistence (static-only, OAuth-only, local no-auth), and OAuth settings
fail-closed.

## Out of scope / later phases

- RFC 7662 opaque-token introspection (extension point exists).
- Capability matrix and generator capability branching: implemented as of Stage 4
  (`docs/MULTI_CLIENT_DEPLOYMENT_GUIDE.md`).
- Real claude.ai / live LibreChat connector E2E (account/public-endpoint gated).
- Certificate automation / ACME (as in Stage 2).