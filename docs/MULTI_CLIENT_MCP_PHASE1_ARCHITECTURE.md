# Multi-Client MCP — Phase 1 Architecture

Status: Phase 1 architecture design only (no implementation).
Branch: `feat/multi-llm-public-mcp`
Date: 2026-09-16 (UTC)
Authoritative inputs: `docs/MULTI_CLIENT_MCP_REQUIREMENTS.md` (Phase -1) and
`docs/MULTI_CLIENT_MCP_PHASE0_AUDIT.md` (Phase 0).

This document decides the target architecture at the level required for Phase 2
implementation planning. It does not implement anything. Labels used throughout:

- **[VERIFIED]** — repository fact confirmed in Phase 0 / source reading.
- **[DECISION]** — a Phase 1 architectural decision made here.
- **[ASSUMPTION]** — a stated assumption the design rests on.
- **[EXPERIMENT]** — requires a pre-implementation experiment; unresolved.
- **[EXTERNAL]** — belongs to an external product/service, not implemented in-repo.
- **[PHASE2]** — explicitly deferred to a Phase 2 decision (must not be decided here).

Authoritative external references used in this phase (checked 2026-09-16):
- Claude (Anthropic): "Authentication for connectors" —
  https://claude.com/docs/connectors/building/authentication
- Claude (Anthropic): "Authenticate to MCP servers behind a tunnel" —
  https://claude.com/docs/connectors/mcp-tunnels/oauth
- Claude (Anthropic): "Third party connectors with remote MCP" —
  https://claude.com/docs/connectors/custom/remote-mcp
- MCP authorization specification (2025-11-25) — modelcontextprotocol.io
- LibreChat MCP docs — https://www.librechat.ai/docs/features/mcp
- DeepSeek Harness `@deepseek-ai/dsh-mcp-client` README / source (Phase 0 reading)

---

## 1. Executive summary

The target is a single YouTube MCP core that can be reached through **simultaneous,
composable, non-exclusive access paths** — ChatGPT via the OpenAI Secure MCP Tunnel,
Claude/LibreChat via a public OAuth edge, LibreChat/DeepSeek via a public static-credential
edge, and trusted local no-auth — without modifying the MCP core or breaking existing
tunnel-only users.

**Recommended architecture (see §4 and ADRs):** keep the MCP core **unchanged** and add
one **public Remote MCP edge component** in front of it. The edge is a self-hostable
sidecar/gateway that acts as the OAuth **Resource Server** for `/mcp` and owns:
- the public `/mcp` protected resource,
- access-token validation,
- Protected Resource Metadata,
- the `401` + `WWW-Authenticate` discovery behavior,
- static Bearer credential validation,
- forwarding authenticated requests to the private MCP core,
- TLS-termination capability and rate/request limits.

The OAuth **Authorization Server is a logically separate, pluggable role — not something
Phase 1 mandates the project build itself.** It may be a bundled/self-hosted provider, a
separate self-hosted service, an existing OIDC/OAuth provider, or another compatible
external authorization service. The exact provider/implementation is a Phase 2 selection
([PHASE2]). The edge publishes discovery/resource metadata so the Resource Server and
Authorization Server can run on **separate origins**.

The OpenAI Secure MCP Tunnel **keeps talking directly to the MCP core** (preserving the
existing working path unchanged); it does not pass through the edge because it already has
its own control-plane access control. Trusted-local access reaches the core directly over
loopback (default) or, with an explicit warning, a trusted LAN interface.

**The MCP core stays 100% unchanged.** All security/exposure concerns move into the edge
component. The tool surface, `/mcp` Streamable HTTP transport, `/healthz`, and stateless
behavior are untouched.

**Key decisions:** security boundary = dedicated edge/sidecar (not in-process, not a
third-party managed edge by default); static auth = canonical `Authorization: Bearer
<token>` (required for static mode) with `X-API-Key` as an **optional** compatibility
alias; OAuth = the edge is the Resource Server and publishes discovery metadata, while the
Authorization Server is a separate pluggable role; both static credentials and OAuth access
tokens are **accepted mechanisms on the same protected `/mcp` resource** (with an explicit
Phase 2 dispatch strategy for the `Authorization: Bearer` ambiguity). Access capabilities
are composed as optional Compose capabilities. Legacy SSE remains **not implemented**
(Phase -1: no current requirement).

**Identity concern:** a full Authorization Server implies an identity source / account /
login model, session handling, consent/grant state, redirect validation, signing/key
management, and token lifecycle. Phase 1 does **not** assume the project should build these;
identity/account source is part of the Authorization Server choice ([PHASE2], §7).

**Architecture-blocking experiments** (do before Phase 2 implementation) are primarily:
confirm Claude static-header + advertised-OAuth coexistence, LibreChat OAuth against the
edge, DeepSeek static Bearer, static/OAuth bearer dispatch, and multiple clients against
one MCP process.

---

## 2. Established facts (from Phase -1 / Phase 0)

- **[VERIFIED]** The YouTube MCP core is generic; no ChatGPT/OpenAI coupling inside
  `src/youtube_mcp/` (Phase 0 §4).
- **[VERIFIED]** It already exposes stateless Streamable HTTP `/mcp` and `/healthz`;
  `MCP_TRANSPORT` is configurable; server binds `MCP_HOST`/`MCP_PORT` (Phase 0 §3).
- **[VERIFIED]** Tool schemas are client-agnostic and read-only (Phase 0 §12).
- **[VERIFIED]** MCP core changes are not currently required (Phase 0 §16 Q3).
- **[VERIFIED]** ChatGPT coupling lives in deployment/generator/docs layers (Phase 0 §4).
- **[VERIFIED]** Root `compose.yaml` already models a standalone (tunnel-less) local MCP.
- **[VERIFIED]** The tunnel supplies TLS/public URL/control-plane auth **[EXTERNAL]**; the
  MCP server itself has no TLS, auth, rate limiting, or origin checks (Phase 0 §5-6).
- **[VERIFIED]** Static credentials are needed for LibreChat + DeepSeek Harness; OAuth for
  Claude + LibreChat; no single auth mode covers all (Phase -1).
- **[VERIFIED]** DeepSeek Harness MCP client (current `master` source) supports only
  `stdio` + `streamable-http` with a static `headers` map; **no OAuth** (Phase 0 §5).

---

## 3. Architectural invariants

Not open to change in Phase 2 without reopening Phase 1:

1. **The MCP core is unchanged.** No edits to `src/youtube_mcp/`, `pyproject.toml` core
   deps, or tool schemas. All new capability lives in the deployment/edge layer.
2. **Streamable HTTP `/mcp` remains the single MCP endpoint** the core serves.
3. **The OpenAI Secure MCP Tunnel remains a supported deployment path**, preserved as-is
   (outbound-only sidecar → core).
4. **Access methods are composable and non-exclusive.** A single installation may serve
   tunnel + OAuth + static + local at once.
5. **Public no-auth is never a valid default.** Any public exposure requires at least a
   static-credential mode or OAuth (or an explicit opt-in that also enables a security
   layer).
6. **Tool names/schemas remain unchanged** (backward compatibility).
7. **Everything security-related is isolated in the edge** so the core stays reviewable and
   the Phase 0 "generic core" property is preserved.
8. **The Authorization Server is a pluggable role, not a mandated in-repo build.** The edge
   is the Resource Server; the Authorization Server choice is a Phase 2 decision
   ([PHASE2]) that must remain replaceable.

---

## 4. Options evaluated (security boundary)

The four candidate security boundaries, evaluated against the Phase 0 facts:

### Option A — Auth inside the Python/FastMCP process
Add middleware to the existing FastMCP/Starlette app (OAuth + static-credential validation
+ TLS config in-process).
- Pros: no new component; direct control; smallest footprint.
- Cons: **modifies the MCP core** (violates invariant 1); mixes security into the tool
  server; TLS in a Python process is more fragile than a gateway; harder to rate-limit
  independently; couples a public-face edge to the app.
- Verdict: **rejected** — it breaks the "core unchanged" invariant and is unnecessary.

### Option B — Dedicated gateway/sidecar in front of (or beside) the core
A small self-hostable edge service (or ingress) that terminates TLS, enforces auth, and
forwards to the unchanged core over the private network.
- Pros: core unchanged; security isolated; composable (can be present or absent per
  deployment); mirrors the existing tunnel-as-sidecar pattern; easy to test in isolation;
  supports static + OAuth (as Resource Server) + rate-limit + request-size in one place.
- Cons: one new component to build/maintain; self-hosted complexity; must be wired into
  Compose and generators.
- Verdict: **chosen** — best balance for self-hosting + tunability + core preservation.

### Option C — Reverse proxy + separate auth service
A reverse proxy (nginx/traefik) doing TLS + static headers, plus a separate auth service
for OAuth/token validation.
- Pros: proven pieces; strong TLS.
- Cons: two+ moving parts; wiring OAuth metadata through a generic proxy is fiddly;
  more operational complexity for a single-MCP deployment.
- Verdict: acceptable but **not chosen**; Option B is the same idea in one cohesive
  component that also knows MCP/OAuth specifics.

### Option D — External managed edge/identity service (cloud)
(Phase 0 listed as external identity/auth service.)
- Pros: near-zero self-hosted surface; managed TLS/OAuth.
- Cons: vendor lock-in; the repo is self-hosting-oriented; adds external dependency and
  per-request cost/egress; conflicts with the repo's self-hosted/tunnel posture.
- Verdict: **not the default**. **[ASSUMPTION]** may become a Phase 2 consideration for
  deployments that already standardize on a managed edge, but the self-hosted Option B is
  the baseline so the project stays provider-agnostic.

**Recommendation: Option B — a dedicated, self-hostable public Remote MCP edge
component** in front of the unchanged MCP core. The edge is the **Resource Server**; the
Authorization Server is a separate, pluggable role ([PHASE2]). (`Phase 1 decision` is
resolved to B.)

---

## 5. Recommended target architecture

```
                    ┌────────────────────────────────────────────────────┐
                    │                  PRIVATE network                    │
                    │                                                    │
  trusted loopback  │   ┌─────────────────────────────────────────────┐  │
  only (default) /  │   │   YouTube MCP core (unchanged)              │  │
  LAN (explicit,    │   │   FastMCP Streamable HTTP /mcp, /healthz    │  │
  warned) no-auth   │   │   stateless, read-only tools                │  │
  ─────────────────►│   └─────────────────────────────────────────────┘  │
                    │            ▲            ▲              ▲          │
                    │            │            │              │          │
  OpenAI Secure     │   ┌────────┴─┐   ┌──────┴──────┐   ┌───┴────────┐ │
  MCP Tunnel ──────►│   │  tunnel   │   │  public     │   │  trusted   │ │
  (outbound-only,   │   │  sidecar  │   │  edge  =    │   │  local     │ │
  control-plane     │   └──────────┘   │  OAuth       │   │  direct    │ │
  auth)             │                  │  RESOURCE    │   └────────────┘ │
                    │                  │  SERVER       │                   │
                    │                  │  (TLS + static │                   │
                    │                  │  + token       │                   │
                    │                  │  validation)   │                   │
                    │                  └──────▲───────┘                   │
                    └─────────────────────────┼───────────────────────────┘
                                              │  public HTTPS
                     public internet     ┌────┴──────────────┐
                                         │ Claude (OAuth),     │
                                         │ LibreChat (OAuth or  │
                                         │ static), DeepSeek    │
                                         │ (static)             │
                                         └──────────────────────┘

  OAuth Authorization Server (pluggable role, [PHASE2]):
   ├─ bundled/self-hosted provider
   ├─ separate self-hosted service
   ├─ existing OIDC/OAuth provider
   └─ or other compatible external authorization service
   (may be on a different origin; discovered via Protected Resource Metadata
    `authorization_servers` + RFC 8414 / OIDC discovery)
```

Flow summary:
- **Tunnel path (preserved):** ChatGPT → OpenAI control plane → tunnel sidecar → core.
  (`MCP_SERVER_URL=http://youtube-mcp:8765/mcp`)
- **Public edge path:** Claude/LibreChat/DeepSeek → public HTTPS → edge (Resource Server:
  TLS + static validation + OAuth token validation) → core (internal).
- **Authorization Server path (pluggable):** edge points clients at the Authorization
  Server via discovery metadata; the Authorization Server may be the edge's own bundled
  provider, a separate service, or external ([PHASE2]).
- **Local path:** local client → core directly (loopback only by default; explicit warned
  LAN opt-in).

---

## 6. Security boundary (decision + detail)

**[DECISION]** All public-request security is owned by the **edge** component:
- **TLS must be terminated before public traffic reaches the protected MCP resource.** The
  edge is the default terminator (public listeners HTTPS; edge→core internal plain HTTP),
  **but** deployments may instead place an existing reverse proxy / ingress / TLS
  terminator in front of the edge. Phase 1 does **not** expand into certificate-issuance /
  ACME automation design ([PHASE2] for cert provisioning).
- **Static credential validation** for `Authorization: Bearer <token>` (required for
  static mode) and optionally `X-API-Key` (see §8).
- **OAuth Resource Server surface** (the protected `/mcp`): token validation + discovery
  metadata. The Authorization Server is separate/pluggable (§7).
- **Rate limiting** per source IP and per credential/token.
- **Request-size limits** at the edge (per-request header/body cap) before any tool work.
- **Host/origin checks** on the public listener to reject wrong Host/sniffing.

Consequences:
- The core never sees an unauthenticated public request; only the edge (and the trusted
  tunnel/local paths) reaches it.
- The edge holds/inspects credentials and tokens, so secrets never need to reach the core.
- The core remains reviewable, unchanged, and reusable.

---

## 7. OAuth architecture (Resource Server / Authorization Server split)

Based on Claude's authoritative connector authentication documentation (checked
2026-09-16) and the MCP 2025-11-25 authorization spec.

### 7.1 The edge is the OAuth Resource Server

**[DECISION]** The public Remote MCP edge is the OAuth **Resource Server** for `/mcp`. It
owns:
- the public `/mcp` protected resource;
- **access-token validation** (JWT verification and/or opaque-token introspection);
- **Protected Resource Metadata** (`/.well-known/oauth-protected-resource`, RFC 9728);
- the **`401` + `WWW-Authenticate: Bearer resource_metadata=...`** discovery behavior;
- **static credential validation** (compatibility mode, §8);
- **forwarding authenticated requests** to the private MCP core;
- rate/request limits, host/origin checks, TLS-termination capability.

It does **NOT** (by Phase 1 decision) own the complete authorization-server lifecycle.

### 7.2 The Authorization Server is a separate, pluggable role

**[DECISION]** Do **not** decide in Phase 1 that the edge must implement a complete OAuth
Authorization Server. Model the Authorization Server as a **logically separate, pluggable
role** that may be:
- bundled/self-hosted with the edge,
- a separate self-hosted service,
- an existing OIDC/OAuth provider,
- another compatible external authorization service.

The exact provider/implementation is a **Phase 2 selection** ([PHASE2]).

**User identity is an explicit concern.** A full Authorization Server requires:
an identity source / account / login model, session handling, consent/grant state, redirect
validation, signing/key management, and token lifecycle. Phase 1 must **not silently assume
the project should build these itself**; the identity/account source is part of the
Authorization Server choice ([PHASE2]). This is a deliberate, explicit design decision, not
an oversight.

**Cross-origin is preserved.** The edge publishes Protected Resource Metadata whose
`authorization_servers` field can point at an Authorization Server on a **different origin**
(Claude supports cross-host authorization servers). The edge's `401` +
`WWW-Authenticate resource_metadata` handshake and the RFC 9728/8414 discovery documents
make this work whether the Authorization Server is co-located or external.

### 7.3 Protocol vs client-specific requirements (distinguish)

**Standard MCP/OAuth requirements (all OAuth-capable clients follow):**
- Authorization-server metadata at `/.well-known/oauth-authorization-server` (RFC 8414) or
  OIDC Discovery — served by the Authorization Server.
- Protected-resource metadata at `/.well-known/oauth-protected-resource` (RFC 9728) with a
  `resource` field matching the MCP URL and an `authorization_servers` list — served by the
  edge (Resource Server).
- Authorization-code flow with **PKCE `S256`**; the Authorization Server metadata
  advertises `code_challenge_methods_supported: ["S256"]`.
- **Discovery handshake:** the MCP resource must, when unauthenticated, return `401` with
  `WWW-Authenticate: Bearer resource_metadata="<protected-resource-metadata-url>"`. Claude
  does **not** honor a WWW-Authenticate on a 200. If no `resource_metadata` pointer is
  present, Claude falls back to probing `/.well-known/oauth-protected-resource/<mcp-path>`
  then `/.well-known/oauth-protected-resource` on the MCP origin — so serving the well-known
  document at the root is preferred (a 401 with the pointer avoids extra round-trips).
- The protected-resource metadata `resource` field must match the MCP URL exactly (including
  path); if `authorization_servers` lists more than one, Claude uses the first entry only.
- Scope control: Claude requests the scopes the protected-resource metadata advertises in
  `scopes_supported` (and appends `offline_access` when advertised, for refresh); an
  operator can pin scopes by including a `scope` parameter in the `WWW-Authenticate` header
  on the `401`.
- Token endpoint accepts `application/x-www-form-urlencoded`; DCR `/register` accepts
  `application/json` (RFC 7591).
- Refresh via RFC 6749 `invalid_grant` handling; refresh tokens rotated/sender-constrained
  for public clients.

**Claude-specific (documented):**
- Auth types for remote MCP: `oauth_dcr` (dynamic client registration), `oauth_cimd`
  (Client ID Metadata Document), `oauth_anthropic_creds`, `custom_connection`,
  `static_headers` (Beta), `none`.
- `oauth_dcr`/`oauth_cimd` and `static_headers` are what matter for a custom connector to
  our public edge.
- Hosted Claude surfaces redirect to `https://claude.ai/api/mcp/auth_callback`; Claude Code
  uses loopback RFC 8252 redirects (register `http://localhost/callback` +
  `http://127.0.0.1/callback` plus Claude Code's CIMD).
- CIMD requires metadata advertising `"client_id_metadata_document_supported": true` and
  `"none"` in `token_endpoint_auth_methods_supported` (public client at token endpoint).
- Claude includes `code_challenge_method=S256` on every authorization request; the
  authorization server must support S256.
- Claude refreshes **reactively on 401** plus proactively up to 5 min before expiry.
- Claude waits **up to 10s** for discovery/registration/token responses and **up to 30s**
  for refresh; endpoints must respond within those windows.
- Optionally accept `offline_access` scope to issue refresh tokens.

**LibreChat-specific (Phase -1, documented):** authorization-code + PKCE, client discovery/
DCR, refresh, token-endpoint discovery, OBO.

**DeepSeek Harness: no OAuth.** Static headers only (Phase 0 §5).

### 7.4 Registration strategies (MUST / SHOULD / MAY)

Do **not** treat DCR + CIMD as mandatory features that our edge must implement together.
Describe the supported registration strategies separately:

- **Pre-registered OAuth client credentials** — the operator (or the client, e.g. the
  organization) registers an OAuth client with the Authorization Server. **[SHOULD]** as a
  simple, stable baseline that requires no registration endpoint.
- **Client ID Metadata Documents (CIMD)** — the Authorization Server advertises
  `client_id_metadata_document_supported: true` + `"none"` in
  `token_endpoint_auth_methods_supported`; a self-hosted CIMD `client_id` avoids a
  registration call. **[MAY / required only if Claude is to use CIMD rather than DCR]**
- **Dynamic Client Registration (DCR)** — the Authorization Server exposes a
  `registration_endpoint`; clients register on first connection. **[MAY / interoperability
  option]**, especially for clients (e.g. Claude's `register automatically`) that benefit
  from it. Note Claude's own caution: on high-traffic directory-facing servers, DCR causes
  a new client registration per fresh connection, so prefer CIMD/pre-registered where
  appropriate.

**Correct Phase 1 requirement wording:** the architecture requires **at least one
registration mechanism compatible with the target client and deployment** — not "our
project must implement DCR and CIMD itself." Which mechanisms are actually enabled is a
deployment/Phase 2 decision, and DCR remains an interoperability option.

### 7.5 Edge-hosted topology (conceptual)

```
public HTTPS edge  (= Resource Server)
 ├─ /.well-known/oauth-protected-resource       (RFC 9728)   [edge]
 ├─ /.well-known/oauth-authorization-server     (RFC 8414)   [auth-server surface; see below]
 ├─ /oauth/authorize                                        [auth-server surface; see below]
 ├─ /oauth/token (form-urlencoded)                           [auth-server surface; see below]
 ├─ /oauth/register (application/json, DCR)                  [auth-server surface; see below]
 ├─ /mcp  (resource server: validates bearer/static, forwards to core)
 └─ (optional) Client ID Metadata Document (/clients/{cid}/.well-known/oauth-client-metadata)

[The authorize/token/register + authorization-server metadata MAY be:
   - served by a bundled self-hosted provider mounted behind the edge, or
   - served by a separate service / external OIDC provider on another origin.
  Phase 1 does not decide that these routes must be implemented in-repo. ([PHASE2])]
```

Responsibilities:
- **Resource server** (the `/mcp` route): on unauthenticated request return `401` +
  `WWW-Authenticate: Bearer resource_metadata=...`; validate the bearer token (JWT verify
  or opaque introspection against the configured Authorization Server); validate static
  credentials (§8); on success forward to the core.
- **Authorization server** (authorize/token/register + its metadata): issued/owned by the
  chosen Authorization Server. If the operator bundles/self-hosts it, the edge may mount it;
  otherwise it is external — the edge only references it in Protected Resource Metadata.

**[DECISION]** The edge is the **Resource Server**; it must implement access-token
validation + discovery metadata + the `401` handshake. Whether the Authorization Server is
bundled, separate, or external is **[PHASE2]**. The edge must preserve the ability for
Resource Server and Authorization Server to run on **separate origins**.

---

## 8. Static credential architecture (simplified)

Targets LibreChat + DeepSeek Harness (both send static request headers).

**[DECISION]** Smallest secure compatibility mode:
- **Canonical and required mechanism:** `Authorization: Bearer <token>`. LibreChat and
  DeepSeek Harness can both use it. **Bearer is enabled by default for static mode.**
- **`X-API-Key` is optional compatibility functionality, not a mandatory/default second
  auth API.** Accept it **only if explicitly enabled**, or later shown useful by
  interoperability testing. It maps to the same credential set.
- **Credential set:** a **small set of shared static credentials** (operator-defined; not
  one per user). Multiple allowed (e.g. 1..N) for rotation and per-integration separation,
  but **no per-user identity** in static mode.
- **Validation location:** the **edge** only (one place). Validated before forwarding.
- **Missing/invalid credential:** return `401` with `WWW-Authenticate: Bearer
  realm="youtube-mcp"` (and for OAuth-presenting clients, a `resource_metadata` pointer so
  Claude can discover OAuth — see §9 coexistence).
- **Rotation:** supported by accepting multiple static credentials and documenting that
  old+new can coexist during rotation. No automatic expiry of static tokens (operator
  rotates by removing from config) — keep it simple.
- **Logging rule:** never log the credential value; log a **masked/last-4** or a stable
  credential **id/hash** plus the outcome (auth_ok/auth_fail) and source IP. `auth_fail`
  may be rate-limited and logged for brute-force detection.

Avoid enterprise overengineering: no user directory, no per-user static tokens, no
database of tokens in this mode.

---

## 9. Auth coexistence (multiple accepted auth mechanisms)

**[DECISION]** The preferred architecture allows **static credentials and OAuth access
tokens on the same public `/mcp`** — described as **multiple accepted authentication
mechanisms for the same protected MCP resource** (not "content-negotiated auth").

- **Same `/mcp` endpoint.** A request carrying a valid static credential is treated as
  static-authenticated; a request carrying a valid OAuth access token is
  OAuth-authenticated; an unauthenticated request gets the `401` + `WWW-Authenticate`
  handshake that lets Claude/LibreChat begin OAuth. Same endpoint keeps generator/compose
  and client URL simple, and matches "one MCP URL" that Claude and LibreChat expect.
  **[EXPERIMENT]** whether a 401 handshake confuses DeepSeek/static clients depends on
  their behavior — must be tested.
- **Behavior on unauthenticated requests:** `401` + `WWW-Authenticate` (see §7). Never a
  tool error; never a silent 200.
- **The Bearer ambiguity is an explicit design issue.** Both static credentials and OAuth
  access tokens may arrive as `Authorization: Bearer <token>`. **Phase 2 must define an
  unambiguous validation/dispatch strategy.** Possible approaches (all acceptable):
  - a **distinct token namespace/prefix** for static credentials that can never collide
    with OAuth tokens;
  - a **deterministic validation order** (e.g. static store first, then OAuth
    introspection; or OAuth introspection first and static second);
  - **separate credential stores** with an explicit lookahead;
  - another collision-free mechanism.
  Do **not** choose an unsafe heuristic such as "a JWT-looking token means OAuth", because
  OAuth tokens may be **opaque**. This is marked **[PHASE2]** + a test requirement
  (§22 experiment 8).
- **Can OAuth discovery coexist with static-header clients?** Yes, when the 401 only
  *advertises* OAuth metadata. A static client that sends the correct header skips the
  401 entirely. **[EXPERIMENT]** — DeepSeek must be verified to send its header on the
  first request and not require a challenge first (Phase 0 indicated it attaches headers
  to every request, so this should work; confirm).
- **LibreChat detection note (verified):** LibreChat's own behavior reduces the risk for
  its static path — when an admin/user API key (`apiKey`) is configured, LibreChat treats
  the server as API-key authenticated and **skips OAuth auto-detection**, and explicitly
  setting `requiresOAuth: false` on a server protected by a static `Authorization` header
  prevents LibreChat from misclassifying it as OAuth-protected. So a LibreChat static
  client pointed at the public `/mcp` will not be diverted into OAuth as long as the config
  declares it (e.g. `apiKey:` or `requiresOAuth: false`).
- **Could OAuth advertisement confuse DeepSeek/static clients?** Possibly if a client
  treats a 401 + OAuth metadata as an error instead of attaching its credential. This is
  the key coexistence risk and is an **[EXPERIMENT]** (§22).
- **Should both be accepted for the same resource?** Yes — that is the composable model.
  Each request is authenticated by exactly one of: valid static credential, valid OAuth
  token, or (local/tunnel) trusted path.

If testing shows that OAuth advertisement is disruptive to static clients, fallback is
separate endpoints (`/mcp-static`, `/mcp-oauth`) — but the single-endpoint model is the
preferred design.

---

## 10. Parallel access

- **Can one stateless MCP process serve tunnel + OAuth + static + local simultaneously?
  Yes.** The core is stateless and shared; it already serves multiple clients. The edge
  and tunnel are just two internal producers; local is a third. No per-client state exists
  in the core. **[VERIFIED core stateless; parallel-path safety = ASSUMPTION until tested]**
- **Must auth identity reach the core?** **No.** The core is read-only public-YouTube data;
  no per-user logic exists or is planned. Therefore the edge validates identity and does
  NOT need to pass user identity to the core. This keeps the core unchanged and avoids
  cross-user state.
- **Separate MCP replicas needed?** **No** for correctness (stateless, read-only). Separate
  replicas would only be for isolation/scale, which is out of scope. **[ASSUMPTION]**
- **Rate limits differ by access path:** **[DECISION]** yes — the edge applies its own
  limits to public traffic; the tunnel path has its own upstream controls; local is
  trusted and unthrottled. The edge rate-limits static-credential and OAuth-token uses
  independently.
- **Session/state collisions?** None expected: stateless core; opaque pagination tokens are
  stateless content (not sessions). Multiple clients paging different transcripts do not
  collide. **[VERIFIED: continuity tokens are self-contained; ASSUMPTION for concurrency
  stress until tested]**

---

## 11. Network topology

**[DECISION]**
- **MCP core remains private.** It binds on the internal/docker network (or loopback for
  local-only). Never exposed to the public internet directly (host/origin+auth at edge).
- **TLS must be terminated before public traffic reaches the protected MCP resource.** The
  **packaged/default topology terminates TLS at the edge**; deployments **may** instead use
  an existing reverse proxy / ingress / TLS terminator in front of the edge. Phase 1 does
  not design certificate issuance/ACME ([PHASE2]).
- **Public ports: only the edge publishes a host port** (HTTPS), and only in
  public-capability profiles.
- **OpenAI tunnel continues to talk directly to the MCP core** (`MCP_SERVER_URL=.../mcp`),
  bypassing the edge. Rationale: preserving the existing working tunnel path, and the
  tunnel already has its own control-plane auth. It does not need the edge.
- **Trusted local access — two distinct categories:**
  - **Default trusted-local no-auth = loopback only** (`127.0.0.1` / equivalent). This is
    the only unauthenticated exposure with no warning.
  - **Trusted LAN no-auth = explicit advanced opt-in with a warning.** Binding
    unauthenticated MCP to a LAN interface is **not equivalent** to loopback-only access;
    anyone on that LAN can call it. This must be flagged in topology, deployment profiles,
    generator semantics, and the threat model.
- **Preventing accidental public no-auth exposure:** the core's own listener stays
  bound to loopback by default; a trusted-LAN profile is an explicit opt-in with a warning;
  only the edge opens a public socket, and public profiles always include at least one
  auth capability. Generators must not emit a public core publish without an auth
  capability (§13, §15).

---

## 12. OpenAI tunnel integration

**[DECISION]** The tunnel sidecar is **unchanged and independent** of the edge. It keeps:
`CONTROL_PLANE_TUNNEL_ID`, `CONTROL_PLANE_API_KEY`, `MCP_SERVER_URL=http://youtube-mcp:
8765/mcp`, depends_on `service_healthy`, no published host ports, outbound-only.

Relationship to the edge:
- The tunnel and the edge are **two independent producers** reaching the same private core.
- They are **independent optional capabilities**: a deployment may enable tunnel-only,
  edge-only, both, or neither (local-only).
- No coupling between them; removing one leaves the other functional.

---

## 13. Composable deployment capabilities

**[DECISION]** Deployment is expressed as **optional capabilities** (compose features /
generator selections), never "choose one frontend":

| Capability | Effect |
|---|---|
| **Core MCP service** | always present |
| **OpenAI Secure MCP Tunnel** | optional — adds tunnel sidecar, keeps existing behavior |
| **Trusted local exposure** | optional — loopback by default; **trusted LAN is an explicit opt-in with a warning** |
| **Public Remote MCP edge** | optional — the Resource Server (TLS + static + OAuth token validation) in front of the core |
| **Static credential auth** | optional public capability (within the edge); Bearer canonical, X-API-Key opt-in |
| **OAuth** | optional public capability (edge = Resource Server; Authorization Server pluggable [PHASE2]) |

**Valid combinations (all considered valid):**
- tunnel only (today's ChatGPT deployment)
- local only (today's root compose; loopback default)
- tunnel + OAuth
- tunnel + static auth
- tunnel + OAuth + static auth
- OAuth + static (no tunnel)
- local + tunnel
- local + public access (edge with auth)

**Invalid / disallowed defaults:** public exposure with **no** auth capability. A local
profile may be no-auth (loopback default; LAN opt-in only). A public profile must enable
static and/or OAuth. The generator must validate that public capability implies an auth
capability.

---

## 14. Compose architecture (conceptual)

**[DECISION]** future service roles:
- `youtube-mcp` — core (always). Networks: internal.
- `openai-tunnel` — optional. Networks: internal (outbound only).
- `youtube-mcp-edge` — optional public edge = Resource Server (TLS + static + OAuth token
  validation). Networks: **public** (host HTTPS publish) + **internal** (to core).
- (Authorization Server) — optional/pluggable **[PHASE2]**: may be a bundled service in the
  edge profile, a separate compose service, or an external URL (no compose entry).

Compose design choices:
- **Networks:** an internal bridge for core+tunnel+edge-to-core; the edge additionally has
  a public/host-bound network for its HTTPS listener.
- **Health dependencies:** edge `depends_on: youtube-mcp {condition: service_healthy}`;
  tunnel `depends_on: youtube-mcp {condition: service_healthy}` (unchanged). Edge exposes
  its own `/healthz`.
- **Secrets/env flow:** YouTube API key + MCP config → core (as today). Public credentials
  (static tokens, OAuth client/issuer/keys, TLS keys) → **edge** (new), via env or secret
  files; never in logs. If a bundled Authorization Server is used [PHASE2], its secrets live
  with it.
- **Public ports:** only the edge publishes an HTTPS host port (or the ingress/TLS
  terminator in front of it); core/tunnel publish none (core may expose internal only).
- **Compose profiles vs generated service inclusion:** **[DECISION]** prefer **generated
  service inclusion** (the existing generator philosophy) over Compose `profiles`, because
  the project already has three generators producing concrete YAML and tests lock
  equivalence. Compose `profiles` may also be offered as a convenience, but the generated
  per-capability YAML is the primary mechanism. This is a Phase 2 detail; not editing
  Compose now.

---

## 15. Generator semantics (design only)

**[DECISION]** Future generator decision flow (per the task's 5-step model):

1. **Common YouTube/MCP settings** (unchanged and shared): image tags, YouTube API key,
   yt-dlp enable/consent, languages, max chars, proxy.
2. **Select enabled access capabilities** (multi-select, composable — NOT "Connect to
   ChatGPT? yes/no"):
   - [ ] OpenAI Secure MCP Tunnel
   - [ ] Trusted local exposure (loopback default; **LAN requires explicit opt-in + warning**)
   - [ ] Public edge with static credentials
   - [ ] Public edge with OAuth
3. **Ask only questions required by the selected capabilities:**
   - tunnel ⇒ ask Tunnel ID + Runtime API key (existing, now conditional on tunnel [✓]).
   - local ⇒ ask loopback vs LAN binding; **if LAN, show a warning** that unauthenticated
     MCP will be reachable from that LAN.
   - static ⇒ ask one or more static credentials (masked); **Bearer is the canonical
     default**; ask whether to also accept `X-API-Key` (off by default).
   - OAuth ⇒ ask for the Authorization Server: bundled/self-hosted, separate service, or
     external issuer URL; issuer/domain, scopes; registration strategy (pre-registered /
     CIMD / DCR). **[PHASE2]**-level prompts; the generator emits the selected provider
     wiring.
4. **Validate dangerous combinations:** public capability without any auth ⇒ error;
   conflicting ports/hosts; LAN no-auth without warning; etc.
5. **Emit equivalent Compose from Bash, PowerShell, Web** (existing three-way equivalence
   test extended to per-capability outputs).

Existing tunnel-specific questions (Tunnel ID, Runtime key, tunnel image tag) become
**conditional** on the tunnel capability. New public-security inputs: static credential(s),
Authorization Server selection + issuer/scope/registration settings, TLS/cert handling,
rate-limit tuning (optional). No generator code written in this phase.

---

## 16. Configuration model

Semantics required (no final env names decided — §24):
- **Core (unchanged):** `YOUTUBE_*`, `YTDLP_*`, `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`.
- **Tunnel (unchanged):** `CONTROL_PLANE_TUNNEL_ID`, `CONTROL_PLANE_API_KEY`,
  `MCP_SERVER_URL`, proxy vars.
- **Edge — static auth:** a flag to enable static auth; a **list of static credentials**
  (masked, from env/secret, Bearer canonical); an optional flag to enable `X-API-Key`; a
  stable credential id/hash for logging. Rotation = multiple entries.
- **Edge — OAuth (Resource Server role):** a flag to enable OAuth; the **Authorization
  Server issuer / discovery URL** (RFC 8414 or OIDC); a token-validation mode (JWT verify
  using jwks vs opaque introspection endpoint); scopes; protected-resource metadata
  resource URL; whether to accept `offline_access` (refresh); **registration strategy**
  (pre-registered / CIMD / DCR). The edge does not itself hold authorization-server signing
  keys **unless** a bundled provider is selected ([PHASE2]).
- **Authorization Server (pluggable role, [PHASE2]):** issuer/base URL, identity/account
  source, authorization-endpoint/token-endpoint/registration-endpoint, signing/key
  management, token lifecycle, consent/grant store — owned by the chosen provider, not
  prescribed here.
- **Edge — TLS/network:** TLS may be terminated at the edge **or** by an existing
  ingress/reverse-proxy in front of it; a standard cert/key contract (or a
  reverse-proxy-TLS mode); public bind address/port; rate-limit defaults; request-size cap.
  Certificate issuance/ACME is **[PHASE2]**.

**[DECISION]** Prefer environment- and secret-file-based configuration (matching the
existing env-driven core), with secrets supplied via Docker secrets/mounts or env, never in
container argv or logs. Final variable names are a Phase 2 decision.

---

## 17. Threat model

| Threat | Responsible layer / mitigation |
|---|---|
| Public no-auth exposure | Generators reject public-without-auth; edge only opens public socket(s); core stays private; host/origin checks on edge public listener. Loopback is the default local exposure; LAN no-auth is an explicit warned opt-in, never silently enabled. |
| Leaked static credential | Edge validates; credential never logged; operator rotates (multi-credential set); rate-limit auth failures; optional per-credential id in logs only. |
| Brute force on static/static OAuth | Edge rate limiting (per IP and per credential); 401 + backoff. |
| Scraping / resource abuse | Edge rate limiting; request-size limits; transcript already bounded/paginated (core caps); per-token caps. |
| Oversized requests | Edge request-size cap before forwarding; core transcript hard-cap is second line. |
| DoS | Edge rate limiting; TLS at edge (or existing ingress); core only reachable from private network + edge/tunnel/local. |
| OAuth token misuse | Bearer validation at edge (JWT verify or opaque introspection against the Authorization Server); short-lived access tokens; refresh rotated/sender-constrained for public clients (Claude/DCR/CIMD); token never logged. |
| DCR abuse | DCR is an optional interoperability strategy, not mandatory; if enabled, may be limited/rate-limited or replaced by pre-registered/CIMD; document DCR client proliferation warning (Claude docs). |
| Secrets in logs | Edge logs never include credential/token values (masked only); entrypoint/wrapper patterns reused. |
| Bypassing the edge by reaching core directly | Core private + not publicly exposed; only edge/tunnel/local producers reach it; generator validation prevents a public core publish. |
| Authorization Server compromise / key leakage | Owned by the chosen Authorization Server ([PHASE2]); signing keys/token lifecycle are the provider's responsibility, not the edge's. |
| Identity/account breach | Owned by the chosen Authorization Server's identity source ([PHASE2]); the edge only validates tokens it is told to trust. |

---

## 18. Resource controls

- **Rate limiting:** edge per-IP and per-credential/token; tunable; different limit classes
  for static vs OAuth.
- **Request-size limits:** edge per-request header/body cap.
- **Transcript/payload bounds:** already bounded in the core (60k/120k char caps +
  pagination) — retained as the second line, not relied on as the only control.
- **Concurrency:** stateless core scales naturally; no per-auth-path pinning needed.
- **Observability/health:** edge exposes `/healthz` (for compose `service_healthy`),
  logs auth outcomes (masked), and can emit metrics (rate-limit hits, auth failures,
  latency) without secrets. Core `/healthz` unchanged. OAuth/token introspection failures
  are observable.

---

## 19. Backward compatibility

- **Tunnel-only users:** fully supported with zero new requirements — the tunnel path is
  untouched; they never need OAuth or a public edge.
- **Tool names/schemas:** unchanged.
- **Root local compose:** still works standalone (loopback default).
- **Existing generator output** for tunnel-only remains valid (capability model simply adds
  options; the tunnel-only selection reproduces the current output exactly).
- **[EXPERIMENT]** — verify a current tunnel-only installation is unaffected when an edge
  is later added to the same compose (no seed-order dependency between the two internal
  producers).

---

## 20. Complexity assessment

| Item | Complexity |
|---|---|
| Static auth (Bearer validation; X-API-Key optional) | **small** |
| TLS / public HTTPS edge (termination at edge or existing ingress) | **medium** |
| OAuth — **Resource Server only** (token validation + discovery metadata + 401 handshake) | **medium** |
| OAuth — Authorization Server provider selection/integration (if bundled: full provider = **large**; if external: integration = **medium**) | **medium-large, [PHASE2]-dependent** |
| Compose changes (capabilities/service roles) | **small** |
| Generator branching (composable capabilities) | **medium** |
| Tests (edge unit/integration, Bearer-dispatch, generator equivalence, coexistence) | **medium** |
| Docs | **small** |

Overall: the core stays trivial to review; the Resource Server work is **medium**; the
largest item — a **complete Authorization Server** — is **not mandated** by Phase 1. If the
deployment reuses an existing OIDC/OAuth provider, OAuth work shrinks to the Resource
Server + discovery wiring. Phase 2 should land static + TLS first (small/medium), then the
OAuth **Resource Server**, then the Authorization Server integration decision.

---

## 21. ADRs

### ADR 1 — MCP core unchanged vs changed
- **Decision:** keep the MCP core **unchanged**; all security/exposure in the edge.
- **Rationale:** core is already generic/stateless/Streamable HTTP; changing it risks the
  working ChatGPT/tunnel path and the read-only tool surface; Phase 0 shows no core gap.
- **Alternatives:** in-process auth (rejected — mixes security into core, breaks invariant)
- **Consequences:** a new edge component must be built; core tests remain valid.
- **Uncertainty:** none material.

### ADR 2 — Security boundary location
- **Decision:** dedicated self-hostable **edge/sidecar** (Option B); not in-process, not a
  managed third-party edge by default.
- **Rationale:** isolates security, preserves core, mirrors tunnel-sidecar pattern, is
  compose/generator-friendly; hosts the Resource Server.
- **Alternatives:** reverse proxy + auth service (Option C, acceptable), external managed
  edge (Option D, not default).
- **Consequences:** one new component; must be wired into compose+generators; TLS may be
  terminated at the edge or by an existing ingress/reverse proxy in front of it.
- **Uncertainty:** exact edge implementation (language/framework) is Phase 2.

### ADR 3 — Static auth strategy
- **Decision:** canonical **`Authorization: Bearer <token>`** (required for static mode),
  with `X-API-Key` as an **optional** alias (off by default / only if interop tests show it
  useful); a small shared credential set; validation at the edge; 401 on failure; mask in
  logs.
- **Rationale:** Bearer covers LibreChat + DeepSeek with one mechanism; simple; no per-user
  DB; keeps the surface minimal (X-API-Key not a default/second API).
- **Alternatives:** per-user static tokens (overengineering), single shared token only
  (fragile rotation), X-API-Key required (rejected — unnecessary second auth API).
- **Consequences:** static mode has no per-user identity; fine for read-only shared use.
- **Uncertainty:** whether X-API-Key is ever needed — resolve in Phase 2 interop test.

### ADR 4 — OAuth architecture (Resource Server vs Authorization Server)
- **Decision:** the public edge acts as the OAuth **Resource Server** and publishes the
  required discovery/resource metadata, the `401` + `WWW-Authenticate` handshake, and
  access-token validation (JWT verify / opaque introspection). The **Authorization Server
  is a separate, pluggable role selected in Phase 2** — it may be bundled/self-hosted, a
  separate self-hosted service, an existing OIDC/OAuth provider, or another compatible
  external authorization service. **Phase 1 does not mandate implementing a complete
  Authorization Server from scratch**, and does not assume the project builds the
  identity/account/login/session/consent/signing/token-lifecycle machinery itself.
- **Rationale:** Claude/LibreChat require spec-compliant discovery and a working issuer;
  keeping the Authorization Server pluggable preserves provider-agnosticism and avoids a
  large unmandated build. Resource Server + Authorization Server can run on separate
  origins via discovery metadata (Claude supports cross-host authorization servers).
- **Alternatives:** (a) build a full in-edge Authorization Server now (rejected — Phase 1
  over-commitment; large, unmandated); (b) no OAuth (rejected — excludes Claude/LibreChat);
  (c) external-managed identity only (not the default, but a valid Phase 2 Authorization
  Server choice).
- **Consequences:** the required build is the Resource Server (medium) + Authorization
  Server **selection/integration** ([PHASE2]); if an existing OIDC provider is chosen, no
  full in-repo auth server is needed. DCR is an optional interoperability strategy, not a
  project mandate.
- **Uncertainty:** real Claude/LibreChat interactions must be tested (ADR experiments);
  whether a bundled provider is ever needed depends on the Phase 2 Authorization Server
  selection.

### ADR 5 — OpenAI tunnel relationship to new edge
- **Decision:** tunnel talks **directly to the core**, independent of the edge.
- **Rationale:** preserves working path; tunnel already authenticates via control plane;
  no reason to route through the edge.
- **Alternatives:** tunnel → edge → core (no benefit; adds a hop and dependency).
- **Consequences:** edge and tunnel are independent optional capabilities; two internal
  producers.
- **Uncertainty:** minimal.

### ADR 6 — Composable deployment model
- **Decision:** optional capabilities (core always; tunnel/loopback-local/edge/static/OAuth
  optional), public-without-auth invalid; trusted-LAN no-auth is an explicit warned opt-in.
- **Rationale:** Phase -1 says access methods composable and non-exclusive.
- **Alternatives:** "choose one frontend" (rejected).
- **Consequences:** generators become multi-select; more combinations to test.
- **Uncertainty:** none.

### ADR 7 — Legacy SSE
- **Decision:** **not implemented** in the public edge; not required by modern target
  client paths (Phase -1).
- **Rationale:** no current requirement; DeepSeek has no SSE; Claude/LibreChat modern paths
  use Streamable HTTP.
- **Alternatives:** add SSE passthrough (rejected — no requirement).
- **Consequences:** none.
- **Uncertainty:** if a legacy client path surfaces, revisit.

---

## 22. Pre-implementation experiments

Experiments that could block the Phase 2 architecture (do NOT implement now):

1. **Claude OAuth discovery against the edge** — verify Claude finds protected-resource
   metadata (`401` + `WWW-Authenticate resource_metadata`), authorization-server metadata
   (RFC 8414 / OIDC), PKCE S256, callback `https://claude.ai/api/mcp/auth_callback`, and
   refresh-on-401. Use a real or throwaway Authorization Server (the point is to validate
   the **edge's Resource Server handshake**, not to build one).
2. **Claude static-header + advertised-OAuth coexistence** — add a connector with a
   `static_headers` credential against an edge that also advertises OAuth (ADR 2/9). Confirm
   Claude sends the header without being diverted into OAuth.
3. **LibreChat OAuth against the edge** — verify PKCE, client discovery/DCR, refresh,
   configured `oauth:` block, callback path.
4. **LibreChat static Bearer against the edge** — verify `headers`/`apiKey` (bearer; and
   optionally custom `X-API-Key` if enabled) against the edge.
5. **DeepSeek static Bearer against the edge** — verify DeepSeek Harness
   `transport: streamable-http` + `headers: Authorization: Bearer <token>` first-request
   success; confirm an unauthenticated 401 + OAuth advertisement does not confuse it.
6. **Tunnel + public edge simultaneously** — confirm both internal producers reach the
   same core without conflict.
7. **Multiple clients hitting the same MCP process** — stress the stateless core with the
   tunnel, an OAuth client, a static client, and a local client concurrently (paginated
   transcripts) and confirm no collisions/state leakage.
8. **Static vs OAuth Bearer dispatch** — since both may arrive as `Authorization: Bearer`,
   test a candidate dispatch strategy (e.g. distinct static-token namespace; deterministic
   validation order; separate stores) with real clients to confirm no ambiguity and no
   unsafe token guessing. **[PHASE2] design decision + test requirement.**

---

## 23. Phase 2 implementation plan (staged, not executed)

### Stage 0 — Edge scaffold (depends on experiments)
- **Scope:** create an isolated edge service project/dir (scaffold), health endpoint, TLS
  termination capability (edge-terminated or behind existing ingress), request-size limit,
  private-network default.
- **Files/components:** new `edge/` (or `src/` subpackage), compose service role, tests.
- **Tests:** edge unit tests (health, size limits), integration (edge→core forward).
- **Done when:** edge forwards an authenticated request to the unchanged core and returns
  the result; Docker health green.

### Stage 1 — Static credential auth
- **Scope:** `Authorization: Bearer <token>` (canonical, required for static mode) +
  optional `X-API-Key` (off by default), multi-credential set, rate limiting on failures,
  masked logging, 401 on failure.
- **Files/components:** edge auth module, static-creds config, tests.
- **Tests:** valid/invalid/missing credential, X-API-Key opt-in behavior, rotation (two
  creds), rate-limit behavior, no-secret-in-logs.
- **Done when:** LibreChat + DeepSeek (experiments 4/5) connect successfully.

### Stage 2 — Public HTTPS edge + compose/generator local+
- **Scope:** public HTTPS (at edge or existing ingress), public publish, generator/compose
  to emit "core + edge static" capability; prune invalid public-without-auth; loopback-vs
  warned-LAN local semantics.
- **Files/components:** compose, three generators, generator equivalence tests.
- **Tests:** generator three-way equivalence for static-public profile; compose validation;
  LAN-warning gating.
- **Done when:** a public static-credential profile is generated and works.

### Stage 2.5 — Authorization Server selection/evaluation step
- **Scope:** a **Phase 2 decision/evaluation gate** (not assumed to be "build a complete
  OAuth provider"): evaluate bundled/self-hosted provider vs separate service vs existing
  OIDC/OAuth provider vs external identity service; pick the Authorization Server; decide
  registration strategy (pre-registered / CIMD / DCR) and identity source.
- **Files/components:** decision doc (ADR), edge config for the chosen provider.
- **Tests:** provider discovery/issuer sanity; registration strategy behavior.
- **Done when:** an Authorization Server choice is made and its endpoint/issuer contract is
  captured. **This replaces the earlier assumption that Stage 3 = "build a complete OAuth
  provider."**

### Stage 3 — OAuth (Resource Server + Authorization Server integration)
- **Scope (Resource Server):** protected-resource metadata, access-token validation (JWT
  verify or opaque introspection against the chosen Authorization Server), `401` +
  `WWW-Authenticate` handshake, scope negotiation. **Authorization Server:** integrate the
  chosen provider (bundled mount, separate service, or external URL) — not a from-scratch
  build unless Stage 2.5 chose a bundled provider.
- **Files/components:** edge resource-server `/mcp` integration, provider wiring, tests.
- **Tests:** metadata discovery, token validation, 401 handshake, provider registration
  strategy (pre-registered/CIMD/DCR as selected), refresh, Claude-specific
  callback/loopback, LibreChat OAuth.
- **Done when:** Claude + LibreChat OAuth (experiments 1/3) connect.

### Stage 4 — Composable capability rollout
- **Scope:** full capability matrix (tunnel-only, local-only, tunnel+static, tunnel+OAuth,
  tunnel+both, OAuth+static, local+tunnel, local+public); generator validation of
  dangerous combos; static-vs-OAuth Bearer dispatch strategy finalized (experiment 8).
- **Files/components:** generators, compose, generator equivalence tests, edge dispatch
  logic.
- **Tests:** all valid combination outputs; invalid combos rejected; Bearer-dispatch tests.
- **Done when:** every valid combination from §13 generates and (where public) runs with
  auth; Bearer dispatch is unambiguous.

### Stage 5 — Hardening, docs, ADRs finalization
- **Scope:** threat-model mitigations (rate limits, DoS controls), observability/metrics,
  docs (architecture, setup per capability), final config names.
- **Files/components:** edge hardening, docs, tests.
- **Done when:** all §17 threats mapped to a tested control; docs updated; Phase 1 doc ADRs
  still accurate.

---

## 24. Remaining unknowns / conclusion

**Remaining unknowns:**
- Real client behavior for coexistence (esp. DeepSeek static vs OAuth advertisement; Claude
  static-header vs OAuth; static-vs-OAuth Bearer dispatch) — **[EXPERIMENT]** (ADR 4
  experiments 2/5/8).
- The chosen Authorization Server and identity source — **[PHASE2]** (Stage 2.5).
- Whether a bundled Authorization Server provider is ever required, or an existing
  OIDC/OAuth provider suffices — **[PHASE2]**.
- Exact edge implementation technology (language/framework) — Phase 2.
- TLS/cert provisioning approach (edge-terminated vs existing ingress; ACME) — **[PHASE2]**.
- Final config/env names, exact secret-storage, exact compose service names, deployment
  provider — Phase 2.
- Stress/concurrency limits of a single shared stateless core under mixed clients.

**Conclusion:** The Phase 1 architecture is a **preserve-the-core + isolated public edge**
design. It requires building **one new component** — a self-hostable public Remote MCP
edge acting as the OAuth **Resource Server** (TLS + static-credential auth + OAuth
access-token validation + discovery metadata) — while leaving the existing YouTube MCP
core, its Streamable HTTP `/mcp`, `/healthz`, read-only tools, and the OpenAI Secure MCP
Tunnel path **completely unchanged**. The **Authorization Server is a separate, pluggable
role** selected in Phase 2; Phase 1 does **not** mandate building a complete Authorization
Server or its identity/session/consent/signing/token-lifecycle machinery. Static auth is a
small compatibility layer (canonical Bearer; X-API-Key optional) for LibreChat + DeepSeek.
OAuth (Resource Server work) is medium and shrinks further if an existing OIDC/OAuth
provider is reused. Access modes are composable and can run simultaneously against one
stateless core. Loopback is the default local no-auth; trusted LAN is an explicit warned
opt-in. Public-without-auth is invalid by construction; TLS is terminated at the edge or by
an existing ingress. The architecture-blocking experiments (Claude and LibreChat
OAuth/static against the edge, DeepSeek static + OAuth coexistence, static-vs-OAuth Bearer
dispatch, and mixed-client concurrency) must run before Phase 2 Stages 2-3, with
static/TLS (Stages 1-2) being the low-risk first increments and the Authorization Server
selection (Stage 2.5) preceding OAuth implementation.

---

## Sources
- Claude: Authentication for connectors — https://claude.com/docs/connectors/building/authentication (2026-09-16)
- Claude: Authenticate to MCP servers behind a tunnel — https://claude.com/docs/connectors/mcp-tunnels/oauth (2026-09-16)
- Claude: Third party connectors with remote MCP — https://claude.com/docs/connectors/custom/remote-mcp (2026-09-16)
- MCP authorization specification 2025-11-25 — https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- LibreChat MCP docs — https://www.librechat.ai/docs/features/mcp
- DeepSeek Harness mcp-client (README/source, Phase 0 reading)
- Repo Phase 0 audit: docs/MULTI_CLIENT_MCP_PHASE0_AUDIT.md
- Repo Phase -1 requirements: docs/MULTI_CLIENT_MCP_REQUIREMENTS.md