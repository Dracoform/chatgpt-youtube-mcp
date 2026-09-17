# Stage 2.5 — Authorization Server architecture decision (ADR)

Branch: `feat/multi-llm-public-mcp`
Date: 2026-09-17
Status: **DECISION (Stage 2.5)** — research completes. Stage 3 implementation
not begun.

This document decides *how* the existing public MCP edge (the OAuth **Resource
Server**, built in Stages 1–2) should integrate with a separate **Authorization
Server (AS)** so OAuth-capable MCP clients (Claude, LibreChat) can connect. It is
the architectural input for Stage 3.

Scope guardrails honoured (from the Stage 2.5 brief):
- The AS is **optional**: static-Bearer users, DeepSeek Harness, the OpenAI
  Secure MCP Tunnel, and local no-auth mode are untouched and never require it.
- We do **not** build our own identity provider unless research proves no
  existing self-hosted option satisfies the requirements. Research did **not**
  prove that — mature providers exist — so **no in-repo AS is built**.
- No OAuth is implemented here; no core/edge code changes beyond the committed
  Stage 2 state.

---

## 1. Verified client / protocol requirements

Sources: committed `docs/MULTI_CLIENT_MCP_REQUIREMENTS.md`,
`docs/MULTI_CLIENT_MCP_PHASE1_ARCHITECTURE.md` (§7, §9), MCP spec
2025-11-25 authorization model, and vendor docs/AS endpoints verified in this
stage (see §3). Distinguish *documented / protocol-expectation / implementation
choice / unknown*.

### 1.1 MCP OAuth / authorization model (protocol)
- Remote MCP servers protected by OAuth advertise a protected resource via
  **Protected Resource Metadata** (`RFC 9728`,
  `/.well-known/oauth-protected-resource`) served by the **Resource Server
  (our edge)**; the `resource` field must match the MCP URL; an
  `authorization_servers` list names the AS(es).
- On an unauthenticated request the resource returns **`401` +
  `WWW-Authenticate: Bearer resource_metadata="..."`** (discovery handshake);
  clients also probe the well-known path directly.
- Clients then perform **Authorization Code + PKCE (`S256`)** against the AS
  and present the resulting **access token** as `Authorization: Bearer <token>`
  to the resource. Status: protocol expectation.
- Token validation is the resource's choice: **JWT + JWKS** (resource does
  local crypto verification) or **opaque + introspection** (RFC 7662) or both.
  Status: implementation choice.
- Refresh via `refresh_token` grant; public clients rotate refresh tokens.
  Refresh tokens are REQUIRED for durable Claude/LibreChat sessions.
  Status: protocol expectation.

### 1.2 Claude (documented, 2026-09-16)
- Remote MCP surfaces: hosted `claude.ai` custom connector redirects to
  `https://claude.ai/api/mcp/auth_callback`; **Claude Code** uses the loopback
  (RFC 8252) flow.
- Auth types for a custom remote MCP: `oauth_dcr` (Dynamic Client
  Registration), `oauth_cimd` (Client ID Metadata Document), `static_headers`
  (Beta), `none`. DCR/CIMD are the ones that matter for a custom connector to
  a self-hosted public edge.
- Sends `code_challenge_method=S256` on every authorization request → AS **must**
  support S256.
- Reads discovery (token/authorization endpoints, scopes, DCR). Hosted Claude +
  Claude Code both auto-register a client when DCR is available.
- Refresh: reactive on `401` + proactive up to ~5 min before expiry; accepts
  `offline_access` scope to get refresh tokens. Waits up to 10 s for
  discovery/registration/token, 30 s for refresh. Status: documented.
- CIMD (for Claude Code) requires the AS to advertise
  `client_id_metadata_document_supported: true` and `"none"` in
  `token_endpoint_auth_methods_supported`. Status: documented; CIMD is **optional
  (MAY)**, NOT required for a working Claude connector (DCR or pre-registration
  suffice).

### 1.3 LibreChat (documented, 2026-09-16)
- `mcpServers` entry with optional `oauth` block; avoids OAuth entirely when a
  static `headers`/`apiKey` + `requiresOAuth: false` are set (verified in Stage 2).
- When OAuth is used: authorization-code + PKCE (`S256`), client
  discovery/DCR, token-endpoint discovery, refresh token handling, OBO (token
  exchange) where relevant. Status: documented.
- LibreChat treats a server as API-key/static authenticated and **skips OAuth
  auto-detection** when the admin/user API key is set (already leveraged in the
  Stage-2 static path).

### 1.4 DeepSeek Harness
- **No OAuth support** (verified against official source, Stage 2.5 candidate
  AS pass). Static `Authorization: Bearer <token>` headers on every request only.
  Must continue to work exactly as today against the same public edge.

### 1.5 Registration strategies (MUST / SHOULD / MAY — from Phase 1 §7.4)
- **Pre-registered OAuth client credentials**: operator creates a client on the
  AS. **[SHOULD]** — simple, stable baseline; no registration endpoint needed.
- **Dynamic Client Registration (DCR, RFC 7591)**: AS exposes
  `registration_endpoint`; clients auto-register. **[MAY]** — interoperability
  option (Claude's "register automatically" benefits; on busy directory-facing
  servers Claude warns DCR causes a new registration per fresh connection, so
  prefer CIMD/pre-registered there).
- **CIMD**: **[MAY]** — needed only if Claude Code is to use CIMD rather than
  DCR/pre-registration.
- **Requirement wording (Phase 1):** at least **one** registration mechanism
  compatible with the target client/deployment — we do **not** mandate the edge
  implement DCR + CIMD itself, and we do **not** require the AS to expose DCR.

### 1.6 Discovery / metadata summary
- Edge (RS): `/.well-known/oauth-protected-resource` (RFC 9728) + `401` +
  `WWW-Authenticate` handshake.
- AS: OIDC Discovery **and** RFC 8414
  (`/.well-known/oauth-authorization-server`) are both used by clients;
  verified present on Keycloak (empirical, §3).

---

## 2. Candidate evaluation (real self-hosted ASes)

Research performed by delegated source-gathering + a live empirical
Keycloak probe; sources are primary vendor docs/repos (dated 2026-09-17).
Full rubric in the raw findings (this is the decision layer).

| Candidate | Verdict | OIDC/OAuth2 | PKCE S256 | Refresh | JWKS | Introspect | DCR | Login UI + local users | License | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| **Keycloak** | **fits (default)** | full OIDC Provider | ✓ (verified) | ✓ | ✓ | ✓ | ✓ (OIDC DCR) | ✓ built-in | **Apache-2.0** | Empirically verified this stage incl RFC 8414. External DB (Postgres/MySQL) for prod; single official image. |
| **Zitadel** | **fits (alternative)** | full OIDC | ✓ | ✓ | ✓ auto-rotation | ✓ | ✓ (RFC 7591; docs explicitly cite MCP clients) | ✓ built-in | AGPL-3.0 | Docs: no RFC 8414 metadata endpoint (OIDC discovery only). Needs Postgres. Lighter footprint (~512 MB test). |
| **Ory Hydra** | maybe | OAuth2-only (no OIDC id-token identity mgmt) | ✓ | ✓ | ✓ `/.well-known/jwks.json` | ✓ | partial (create via API/CLI; self-service DCR not confirmed) | ✗ — needs Ory Kratos/Identities | Apache-2.0 | No built-in login/user store → higher integration burden for our case. |
| **Dex** | maybe | OIDC connector to upstream IdP | ✓ | ✓ | ✓ `/.well-known/keys` | ✓ `/token/introspect` | ✗ no DCR | ✗ upstream IdP only | Apache-2.0 | Connector model adds an upstream dependency; no DCR → poor Claude auto-register fit. |
| **Authentik** | unverified | (docs DNS unreachable in this env) | — | — | — | — | — | — | custom | Re-check needed; not selected without evidence. |
| **Authelia** | **eliminated** | not an AS — auth proxy/SSO fronting existing IdPs; does not itself issue OAuth tokens / JWKS / introspection for arbitrary resource servers | — | — | — | — | — | — | — | Elimiated on primary positioning docs. |
| Minimal custom AS | **eliminated** | no materially-better small verified option; building our own carries security/compliance risk vs mature providers | — | — | — | — | — | — | — | Phase-1 guardrail: don't build identity infrastructure ourselves. |

**Full rubric** (for every dimension listed in the brief) was produced by the
delegated research and is preserved in the live transcript
(`/home/hermy/.hermes/cache/delegation/live/deleg_c9c14557/task-0.log`) and
scratch notes; the table above is the decision-relevant subset.

---

## 3. Empirical probe (bounded, disposable) — Keycloak 25.0

Ran `quay.io/keycloak/keycloak:25.0` in `start-dev` (dev-file DB) published on
`127.0.0.1:8088`, then removed the container. Evidence (not docs):
- OIDC Discovery `/.well-known/openid-configuration` → HTTP 200 (issuer,
  authorization_endpoint, token_endpoint, registration_endpoint, jwks_uri,
  introspection_endpoint present).
- **RFC 8414** `/realms/{realm}/.well-known/oauth-authorization-server` →
  **HTTP 200** (the field the research subagent could not confirm from docs).
- `registration_endpoint` (OIDC DCR) present.
- `jwks_uri .../openid-connect/certs` → HTTP 200 (2917 bytes).
- Introspection `.../token/introspect` → HTTP 401 on anonymous POST (endpoint
  exists, requires client auth — expected).
- `code_challenge_methods_supported: ["plain","S256"]`.
- Grant types include `authorization_code`, `refresh_token`.
- `token_endpoint_auth_methods_supported` lacks `"none"` **by default** → for the
  optional CIMD public-client flow, `"none"` must be configured (Stage 3 config
  requirement; not required for DCR/pre-registration).

This confirms Keycloak satisfies the full MCP OAuth surface, including the RFC
8414 discovery path Claude/LibreChat rely on, with a single official image and
built-in login UI + user store.

---

## 4. Selected Authorization Server strategy

1. **Do not build an AS in-repo.** Mature self-hosted providers fully satisfy the
   requirements. The edge remains the OAuth **Resource Server**; the AS is a
   **separate, pluggable, externally-configured service** (per Phase 1 ADR 4).
2. **Provider-agnostic contract first.** The edge integrates with whatever AS via
   a standard, minimal, verifiable surface (see below). No proprietary coupling.
3. **Ship a recommended default AS** for the "packaged path" (see §5), but keep
   it **optional**: static-only and tunnel-only deployments never start it.

The edge's AS-facing contract (what Stage 3 must build against):
- **Discovery:** consume OIDC Discovery and/or RFC 8414 authorization-server
  metadata to locate issuer, authorization, token, JWKS, introspection, DCR
  endpoints.
- **Protected Resource Metadata:** edge serves RFC 9728
  (`/.well-known/oauth-protected-resource`) with `resource` = MCP URL and
  `authorization_servers` = configured AS issuer(s); edge returns the
  `401` + `WWW-Authenticate` handshake on unauthenticated `/mcp`.
- **Token validation:** **JWT + JWKS as the primary path** (see §6) — edge fetches
  JWKS from the AS, verifies `alg/kid/iss/aud/exp` locally. Optional
  RFC 7662 introspection as a provider fallback for opaque-token ASes.
- **Scopes:** edge advertises/accepts a fixed resource scope (e.g.
  `youtube-mcp`) per Phase 1; operator may pin scopes via the discovery handshake.
- **Keys:** edge refreshes JWKS with `kid` rotation and tolerates rotation.

This contract holds whether the AS is Keycloak, Zitadel, Hydra+Kratos, or an
external OIDC provider — the edge never depends on any vendor-specific API.

---

## 5. Selected default candidate & supported alternatives

**Default (bundled recommendation): Keycloak.** Rationale, not popularity:
- Apache-2.0 — **same license as this project**, cleanest for a self-hosted
  packaged path and for shipping a Compose profile that references it.
- Full OAuth 2.0 + OIDC surface **empirically verified this stage**, including
  the RFC 8414 path and DCR.
- Built-in login UI + local user store + consent → lowest integration burden
  (no need to assemble Kratos/Hydra/identity pieces).
- Single official container image; embedded dev DB for non-prod, Postgres/MySQL
  for prod.
- Active maintenance (2026-09-17 push), huge install base.

**Supported alternative: Zitadel** — lighter footprint, AGPL-3.0, docs even cite
MCP clients as the DCR use case. Choice for operators preferring Zitadel or
needing AGPL's model; requires Postgres and (per docs) OIDC discovery without an
RFC 8414 endpoint.

**Acceptable where already present: Ory Hydra (+ Kratos for login/user store),
Dex (only when an upstream IdP is the identity source), or an external OIDC
provider** (GitHub/Auth0/etc.) — all satisfiable via the provider-agnostic
contract, none cleanly matching the "single self-hosted component" goal,
hence not the default.

**Not bundled:** a custom/minimal in-house AS (ruled unsafe/unnecessary).

---

## 6. JWT vs introspection decision

**Primary: JWT/JWKS local validation.** Both default candidates (Keycloak,
Zitadel) issue signed JWTs and expose JWKS. Decision rationale:
- **Latency:** no per-request round-trip to the AS; local crypto verify.
- **Failure modes:** the edge keeps authorizing while the AS is briefly down
  (cached JWKS), better for a multi-origin self-hosted setup; revocation of an
  individual token is only as fast as its expiry, which we mitigate with short
  access-token TTL + refresh tokens (Claude/LibreChat already refresh).
- **Provider coupling:** minimal — only needs issuer/JWKS, not a vendor API.
- **Key rotation:** handled by `kid` + JWKS refresh (both ASes auto-rotate).
- **Implementation complexity:** low and self-contained in the edge.

**Optional fallback: RFC 7662 introspection** — enabled only when a deployment's
AS issues opaque access tokens (or immediate rejection is required). Adds a
per-request AS dependency and latency; therefore opt-in, not the default.

**Decision:** implement JWT/JWKS local validation as the core path; support
configured introspection as an alternative validator. This is the **provider-
dependent hybrid** of architecture option C (A = local JWT, B = introspect): A is
default, B is an optional fallback for opaque-token providers.

---

## 7. Static-vs-OAuth Bearer dispatch design (resolves the Stage-1 issue)

Both mechanisms arrive as `Authorization: Bearer <token>`. Dispatch must be
**deterministic** and must never let an invalid OAuth token be accepted as static
or vice-versa. No "JWT-looking means OAuth" heuristic (OAuth tokens may be
opaque).

**Design: reserved static namespace prefix as a deterministic validation domain.**

1. Static credentials are REQUIRED to use a reserved prefix (the Stage-1
   convention, `ytsk_`), configured as `EDGE_STATIC_TOKEN_PREFIX` (default
   `ytsk_`). This is an explicit, operator-reserved namespace — not a shape
   guess: the AS is instructed (and standard token formats guarantee) that issued
   OAuth tokens do not carry this prefix.
2. Dispatch rule (single deterministic order, evaluated for every request):
   - If the presented token **begins with the static prefix** → validate ONLY
     against the static store (constant-time digest compare). Succeed ⇒ static
     request; fail ⇒ `401`.
   - Else (token does not carry the static prefix) → validate as OAuth: JWT/JWKS
     local verify (iss/aud/exp/kid), or configured introspection. Succeed ⇒
     OAuth request; fail ⇒ `401`.
   - A token that matches neither ⇒ `401`. There is **no fallthrough** between
     domains.
3. Guarantees:
   - An OAuth token (random, opaque or base64 JWT) cannot accidentally carry the
     reserved static prefix → cannot be mistaken for static.
   - A static token is only ever evaluated against the static store → an invalid
     static token cannot be "rescued" by the OAuth path.
   - No token-shape sniffing beyond the reserved namespace check.
4. Configuration default: both domains off ⇒ local-only (unchanged). Static-only
   ⇒ today's behavior. OAuth+static ⇒ this dispatch. This preserves all existing
   modes and keeps DeepSeek/static clients exactly as today.

This is a refinement of Phase 1 §9's "token namespace/prefix" option, chosen
because prefix-namespacing is the only non-heuristic option that is fully
collision-free and does not require an OAuth round-trip to disambiguate.

---

## 8. Discovery / metadata design

- **Edge (RS) publishes:**
  - `GET /.well-known/oauth-protected-resource` (RFC 9728): `resource` = the MCP
    URL, `authorization_servers` = configured AS issuer(s), `scopes_supported`
    (e.g. `youtube-mcp`), bearer methods `header` (and optional `query`).
  - `401` + `WWW-Authenticate: Bearer resource_metadata="<url>"` on
    unauthenticated `/mcp` (never on `200`; Claude does not honour it on 200).
  - Serving the well-known doc at the root is preferred so Claude's fallback
    probe succeeds even without the `401` pointer.
- **AS publishes** (its own origin): OIDC Discovery + RFC 8414
  authorization-server metadata; JWKS; DCR endpoint (optional); consent.
- **Origin independence:** edge (RS) and AS may run on separate origins; the
  `authorization_servers` list must carry absolute AS issuer URLs. Claude uses
  only the **first** entry if multiple are listed, so the default deployment lists
  exactly one AS.
- Edge configuration: `EDGE_OAUTH_ISSUER`, optional
  `EDGE_OAUTH_JWKS_URL` / `EDGE_OAUTH_INTROSPECTION_URL` overrides,
  `EDGE_OAUTH_AUDIENCE`/scope. Absent when OAuth is off.

---

## 9. Deployment topology / smallest OAuth-enabled deployment

```
Claude / LibreChat
        │  Authorization Code + PKCE → AS (login, consent, issue access token)
        ▼
Public MCP Edge (OAuth Resource Server)          Authorization Server
  · /.well-known/oauth-protected-resource  ◄──── AS issuer/JWKS (+ optional introspection)
  · 401+WWW-Authenticate discovery               (Keycloak default / Zitadel / external)
  · Bearer dispatch: ytsk_* → static store       [OPTIONAL — only for OAuth clients]
  · else → JWT/JWKS (or introspection)
        │
        ▼  private HTTP
Private MCP Core  (unchanged, loopback-only)

Static clients (DeepSeek Harness, LibreChat static, custom)
        │  static Bearer ytsk_* (unchanged)
        ▼
same Public MCP Edge  (static path, no AS involvement)

OpenAI Secure MCP Tunnel ──► same Private MCP Core (unchanged, independent)
```

- **Mandatory (always):** Private MCP Core + (per path) either the tunnel or the
  public edge. Static mode = edge only, no AS.
- **Optional (OAuth only):** AS + (Postgres, for Keycloak prod). Added only when
  an OAuth-capable client (Claude/LibreChat OAuth) is deployed.
- **Smallest OAuth-enabled deployment:** Core + public edge + one AS container +
  its DB (Keycloak) → ~3-4 containers. A static-only deployment adds nothing.
- No service is required by all modes; nothing forces static users to run
  OAuth infrastructure.

---

## 10. Security considerations

- The edge remains the single public boundary; the **core is never public**.
- Edge must **strip `Authorization`** before forwarding to the core (already the
  Stage-1 behaviour) and never log the credential.
- JWT validation MUST be strict: enforce `iss == configured issuer`,
  `aud`/scope equals configured audience, `exp` freshness, `alg` allow-list
  (RS256 and keys from the AS JWKS only), reject `none`/HS256 from remote.
- JWKS: validate `kid` present; refuse algorithm confusion; refresh on rotation,
  tolerate overlap during rotation.
- Bearer dispatch must stay deterministic (§7); never fall through domains.
- TLS in front of `/mcp` (already Stage 2). AS endpoints must also be HTTPS in
  production (loopback acceptable for local dev).
- Introspection (if enabled) must authenticate to the AS and treat the AS as a
  trusted peer; also rate-limited by the existing edge limits.
- Secret/key management: AS signing keys, static tokens, client secrets live in
  secrets store/env; never in logs or generated docs.
- PKCE required (S256) for public clients (Claude hosted, Claude Code loopback,
  LibreChat): no client-secret-only reliance on public clients.
- Consent: AS shows user consent; operator can pre-grant to reduce prompts.
- Operational: short access-token TTL + refresh; monitor JWKS/issuer reachability
  from the edge.

---

## 11. Remaining empirical questions (for Stage 3, not blocking architecture)

1. **End-to-end Claude custom connector (hosted) against Keycloak** — full
   auth-code + PKCE + DCR or pre-registration + refresh over HTTPS. (Cannot be
   tested without a live claude.ai account + public endpoint; remains a real-client
   test.)
2. **Claude Code** with CIMD vs DCR vs pre-registration; confirm Keycloak
   `"none"` public-client token auth config enables CIMD.
3. **LibreChat OAuth** (non-static) against the same AS — full flow + refresh +
   OBO (if used).
4. Whether a `401` + OAuth `WWW-Authenticate` handshake disturbs DeepSeek Harness
   static clients when the handshake is *advertised* but the client attaches its
   static header on the first request (Phase 1 experiment; DeepSeek sends headers
   on every request so expected safe, to be confirmed in Stage 3).
5. AS resource footprint in a real deployment; multi-origin RS/AS discovery.
6. Whether an external OIDC provider's opaque tokens require the introspection
   fallback in practice (verify per provider).

---

## 12. Stage 3 implementation plan (do not execute here)

1. **Edge: Protected Resource Metadata + discovery handshake.** Serve RFC 9728
   `/.well-known/oauth-protected-resource`; return `401` +
   `WWW-Authenticate: Bearer resource_metadata=...` on unauthenticated `/mcp`.
2. **Edge: JWT/JWKS validator.** Fetch JWKS from configured issuer; verify
   alg/kid/iss/aud/exp; cache + refresh on rotation.
3. **Edge: optional RFC 7662 introspection validator** (opaque-token providers).
4. **Edge: deterministic Bearer dispatch (§7).** Reserve static prefix namespace;
   route `*` vs OAuth; no fallthrough.
5. **Edge: OAuth settings/env** (`EDGE_OAUTH_*`) — fail-closed, absent by default.
6. **Compose:** optional `oauth` profile / sidecar for the default AS (Keycloak)
   + its DB; edge env wired to it; core stays private; tunnel and static paths
   unchanged. Document Zitadel/external-provider alternate wiring.
7. **Tests:** discovery metadata, 401 handshake, JWT/JWKS verify (incl. wrong
   issuer/aud/expired/kid-miss), introspection fallback, deterministic dispatch
   (static prefix vs OAuth, no cross-domain acceptance), fail-closed config,
   compose topology (core private, AS optional, no port collision),
   no-credential-logging.
8. **Docs:** OAuth client setup for Claude + LibreChat; AS provisioning
   (pre-registered client as the baseline, DCR/CIMD optional); coexistence with
   static/tunnel/local paths.
9. **Real-client verification:** Keycloak + Claude custom connector and LibreChat
   OAuth against the public edge (may require a public endpoint and test
   accounts); confirm Django/DeepSeek static path unaffected; leave any
   unreachable real-client test explicitly marked pending.

Phase boundary respected: no Stage 3 work began.