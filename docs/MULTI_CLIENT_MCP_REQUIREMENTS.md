# Multi-Client MCP Requirements

Status: Research / requirements derivation (Phase -1 of the multi-LLM public MCP work).
Date researched: 2026-09-16 (UTC)
Scope: Determine what a Remote MCP endpoint must expose so that Claude, LibreChat and
DeepSeek Harness can consume one YouTube MCP service reliably and securely.

This document is evidence-based. Every claim about client capability is labelled with
its confidence class:

- **[std]** — standardized MCP / OAuth protocol capability (from the MCP and OAuth2
  specifications). A protocol capability is NOT treated as a client capability unless a
  client source confirms it.
- **[doc]** — documented client functionality (official vendor docs).
- **[code]** — confirmed in official client source code (official repo, current default
  branch).
- **[beta]** — beta / gradual rollout; not universally available.
- **[unknown]** — not verifiable from primary sources; needs real-world testing.

Source numbering in brackets refers to the Sources section at the end. All primary
sources were checked on 2026-09-16.

---

## 1. Executive summary

A single YouTube MCP implementation that must serve Claude, LibreChat and DeepSeek
Harness has one clear, non-negotiable common denominator and one sharp incompatibility.

- **Transport common denominator: Streamable HTTP.** All three clients in scope support
  it. Claude treats a plain `/mcp` URL as Streamable HTTP (SSE is a legacy `/sse`
  fallback). LibreChat supports Streamable HTTP (production-recommended) and legacy SSE
  (discouraged). DeepSeek Harness supports **only** `stdio` and `streamable-http` — it
  has no SSE client transport in its current official `mcp-client` source.
- **Deployment reachability is separate from the transport.** Public HTTPS is required
  when **Claude / claude.ai Custom Connectors** are included in the deployment target.
  LibreChat and DeepSeek Harness can also consume local/private HTTP endpoints. Public
  HTTPS is **not** an MCP protocol requirement and is **not** a hard client requirement
  of LibreChat or DeepSeek Harness.

- **Authentication:** this is where the three clients diverge sharply, and there is
  **no single reliable authentication mode shared by all three clients today**.
  - **DeepSeek Harness cannot do OAuth at all** in its current official MCP client. It
    only attaches a static `headers` map (e.g. `Authorization: Bearer <token>`) and has
    no OAuth discovery, no PKCE, no Dynamic Client Registration, no refresh, and no 401
    re-authorization logic in the package.
  - **Claude supports OAuth natively** (authorization-code + client discovery + Dynamic
    Client Registration; PKCE/refresh are the standard MCP/OAuth flow and are protocol
    expectations, not separately documented on the connector page), and supports static
    request headers (API key / Bearer) only as a **beta, gradual rollout** that is not
    available to every organization.
  - **LibreChat** is the richest: it supports no-auth, static headers (incl. arbitrary
    custom headers), structured API keys (admin/user; Bearer/Basic/custom header),
    full OAuth 2.0 authorization-code + PKCE with client discovery, DCR, refresh tokens,
    token-endpoint discovery, on-behalf-of (OBO) token exchange, and per-user credentials.

- **Two complementary public authentication profiles are therefore required** (section 11),
  not one universal mode:
  - **Static credential compatibility mode** — static `Authorization: Bearer <token>` /
    API-key header. Stable for LibreChat and DeepSeek Harness; **conditional/Beta for
    Claude** (request-header feature is beta/gradual-rollout). Static headers are the
    only *mechanism* exposed by all three in some configuration, but they are **not** a
    dependable universal deployment mode because of the Claude Beta caveat.
  - **OAuth public standard mode** — the normal/dependable path for Claude and LibreChat,
    but **unsupported by the current DeepSeek Harness MCP client**.

- **MCP OAuth (per-user) should not be the only mode while DeepSeek Harness is in scope**;
  a static-header mode must remain available so DeepSeek is not locked out.

- **Bottom line:** the server must expose a **Streamable HTTP** `/mcp` endpoint — made
  publicly reachable over **HTTPS when Claude Custom Connectors are included** — and
  must support both a **static-header / API-key mode** (for LibreChat + DeepSeek) and
  **MCP OAuth** (for Claude + LibreChat). Unauthenticated public exposure must never be
  the production default.

The detailed evidence for every statement is in sections 3–8; a structured matrix is in
section 9.

---

## 2. Research scope and date

- Scope: officially documented and/or source-confirmed Remote MCP *client* capabilities
  of (1) Claude / claude.ai Custom Connectors, (2) LibreChat, (3) official
  deepseek-ai/deepseek-harness MCP client. Plus a brief interoperability check of three
  public YouTube transcript MCP endpoints and a compatibility matrix.
- Method: primary sources first (official docs + official repos + official issue/PR
  trackers), MCP/OAuth specs only where needed, high-quality third-party sources only
  where noted. Client capabilities were not inferred from the protocol spec.
- Date all sources checked: 2026-09-16 (UTC). The DeepSeek Harness repo was active the
  day before (pushed_at 2026-09-15), so the source reading reflects the current default
  branch (`master`) as of research time. **Being found on `master` is source-forward
  evidence and is not asserted to equal a published/packaged release** (see section 5.3).
- Limitation: none of the three clients was exercised against a live endpoint for this
  report; conclusions about actual in-the-field behavior are therefore labelled and
  listed as unknowns requiring testing (sections 12–13).

---

## 3. Claude findings

Primary source: claude.com Custom Connectors "remote MCP" page [1], checked 2026-09-16.

### 3.1 Transport

- Custom connectors connect to a remote MCP server by its URL; example given is
  `https://mcp.example.com/mcp` [1]. **[doc]**
- Public HTTPS is required — the docs describe an "HTTPS address where the server
  accepts MCP requests" [1]. **[doc]**
- Transport is selected automatically from the URL: "a URL ending in /sse selects the
  older SSE transport. Change it only if the server's documentation says to" [1].
  Implication: a plain `/mcp` endpoint is treated as Streamable HTTP; appending `/sse`
  opts into legacy SSE. **[doc]**
- Redirects are not documented on this page; behavior on redirects **[unknown]**.
- Session/stateless expectations: not spelled out on this page, but Streamable HTTP is
  the supported modern transport (see MCP spec [std]).

### 3.2 Authentication

Claude's custom-connector dialog offers these authentication paths [1]. Note: the page
confirms the OAuth options below, but it does **not** document every OAuth lifecycle
detail (automatic token refresh, exact OAuth discovery mechanics, reconnect/re-
authorization after a 401). Where behavior rests on the MCP/OAuth standard rather than
the connector page, it is labelled `[std]` / `Expected [std]` or `[unknown]` — per the
document's core rule that a protocol capability is not a client capability without a
client source.

- **No authentication** — "No sign-in: anyone with access to the server URL can use the
  connector." If the server uses an API key, the key is added under Request headers.
  **[doc]**
- **OAuth** — "Sign in now" (each user signs in through the server's OAuth flow before
  using it) or "Sign in when needed" (connects without credentials and prompts to sign
  in when the server asks). **[doc]**
- **OAuth client identity** options [1]:
  1. "Use Claude's published identity (recommended)" — the server reads Claude's client
     details from a URL Anthropic hosts (Client ID Metadata Document); the server must
     support it.
  2. "Register automatically" — Claude registers OAuth clients with the server as users
     connect (**Dynamic Client Registration**); "works with most servers, but adds
     client registrations over time" [1]. **[doc]**
  3. "Use your own OAuth client" — a client ID the operator registered with the server
     (client secret optional). **[doc]**
- **Static request headers (API key / Bearer / Basic)** [1]:
  - **Beta / gradual rollout.** "Request header authentication is in beta and available
    to a limited set of organizations. If you don't see the Request headers section in
    the Add custom connector dialog, your organization doesn't have access yet." **[beta]
    [doc]**
  - The available header-name list offers standard auth/routing names such as
    `authorization`, `x-api-key`, `x-auth-token`. Custom header names are reviewed and
    approved by Anthropic before Claude will send them; unapproved names are rejected at
    save (up to four headers). **[doc][beta]**
  - Values are sent exactly as entered; Claude does not add a scheme/prefix. For
    `Authorization` the operator must include the scheme, e.g. `Bearer your-token`
    (or `Basic <base64>`). **[doc]**
  - Request headers can be used in addition to OAuth; the one exception is that
    `Authorization` cannot be set as a request header on an OAuth connection (OAuth owns
    it). **[doc]**
- **PKCE**: not stated on the connector page. PKCE is part of the standard MCP/OAuth2
  authorization-code flow, so treat it as a **protocol expectation (`Expected [std]`)**,
  **not** as independently verified Claude client behavior (`[doc]`). The exact Claude
  PKCE handling is **[unknown]** from this source.
- **Automatic token refresh / re-authorization after 401**: not documented on the
  connector page. Under the standard an OAuth client may refresh tokens and re-
  authorize, but whether Claude does so automatically is **[unknown] / `Expected [std]`**,
  not verified Claude behavior from this source.

### 3.3 Request-header beta: subscription / org gating

- The docs state only "a limited set of organizations" and "your organization doesn't
  have access yet" [1]. It is described as an **org-level capability**, not a per-plan
  option, and availability is explicitly not universal. Whether a specific
  subscription plan is tied to the rollout is **not stated on the primary page**
  **[unknown]**.

### 3.4 Claude Cowork

- The custom-connector page does not discuss Cowork. Cowork behavior with request
  headers / custom connectors is **not covered by this source** **[unknown]**.

### 3.5 Known cases where Claude attempts OAuth despite configured headers

- The docs note `Authorization` is owned by OAuth on an OAuth connection [1], implying
  an OAuth-configured connector will send OAuth bearer tokens. Whether Claude ever
  attempts OAuth when a connector was configured purely with a non-Authorization
  request header is **not documented on this page** **[unknown]**; it would need
  real-world verification (see section 12).

### 3.6 Practical configuration for `https://example.org/mcp`

1. **OAuth:** org owner: Organization settings → Connectors → Add → Custom (type Web) →
   enter the MCP server URL → optionally set OAuth Client ID/Secret in Advanced settings →
   Add. Members then go to Customize → Connectors → Connect to authenticate. (Free/Pro/Max:
   Customize → Connectors → Add custom connector → URL → OAuth credentials → Add.) [1]
2. **Static header (where the beta is available):** choose "No sign-in", then add the
   header under Request headers (e.g. `authorization: Bearer <token>` or `x-api-key:
   <key>`). Claude stores the value as the connector's credential [1]. **[beta]**
3. **No auth:** choose "No sign-in" with no request headers [1].

---

## 4. LibreChat findings

Primary sources: official LibreChat MCP docs [2] and the `mcpServers` YAML schema
reference [3], checked 2026-09-16.

### 4.1 Transport

- Configurable types: `stdio`, `websocket`, `streamable-http`, or `sse` (default derived
  from url/command form) [3]. **[code/doc]**
- **Streamable HTTP**: recommended for production. "For production environments, only
  MCP servers with 'Streamable HTTP' transports are recommended… Unlike SSE which
  maintains long-running connections, Streamable HTTP offers stateless options that are
  better suited for scalable, multi-user deployments." [2] **[doc]**
- **SSE**: "Remote transport mechanism but not recommended for production environment" [2].
  **[doc]**
- Endpoint configured by `type:` + `url:` in `librechat.yaml` `mcpServers` (or in the MCP
  Settings UI) [2][3]. **[code/doc]**
- Streamable HTTP reconnect nuance: a reconnect can briefly return HTTP 409 when the
  server holds a stale standalone SSE stream for the old session; LibreChat treats this
  as transient and rebuilds [2]. **[code/doc]**

### 4.2 Authentication

Verified mechanisms and their stability:

- **No auth** — supported: simply omit headers/apiKey/oauth. **[doc]**
- **Static Bearer header / custom headers** — supported via `headers` (arbitrary header
  names and values; supports `${ENV_VAR}` and per-user `{{LIBRECHAT_USER_*}}`
  placeholders) [2][3]. Considered a normal, stable mechanism. **[code/doc]**
- **Structured API key** — `apiKey:` object with `source: admin|user` and
  `authorization_type: bearer|basic|custom` (`custom_header` when custom) [3]. Stable.
  **[code/doc]**
- **OAuth 2.0 authorization-code + PKCE** — documented: "LibreChat MCP servers support
  OAuth 2.0 with: Authorization Code Flow with PKCE (recommended)…" [2]. Stable/documented.
  **[doc]**
- **Client discovery / Dynamic Client Registration** — "Client Discovery: Automatic
  client registration when supported by the OAuth provider"; if no client id/secret is
  provided, DCR is used [2][3]. **[doc]**
- **Refresh tokens** — "Refresh Tokens: Automatic token renewal when available";
  automatic refresh is documented [2]. **[doc]**
- **Token endpoint discovery** — token-endpoint auth method (form POST vs HTTP Basic) is
  discovered from provider metadata, with a manual override (`token_exchange_method`)
  [2][3]. **[doc]**
- **Per-user credentials** — `customUserVars` (user-provided keys referenced in headers),
  each user's own API key and own OAuth session; multi-user isolation is a headline
  feature [2][3]. Stable. **[code/doc]**
- **On-behalf-of (OBO) token exchange** — `obo:` config exchanges the user's OpenID token
  for a delegated downstream token, forwarded as Bearer [2][3]. Documented.
  **[doc]**
- **401 recovery** — "Silent 401 Recovery: If an OAuth MCP connection receives a
  mid-session authentication rejection, LibreChat coordinates one bounded refresh and
  reconnect attempt per user and server" [2]. **[code/doc]**
- `requiresOAuth` flag: auto-detected at startup by default; can be set explicitly;
  `requiresOAuth: false` is recommended for servers protected by a static Authorization
  header to avoid misclassification [3]. **[code/doc]**
- OAuth timing env vars: `MCP_OAUTH_HANDLING_TIMEOUT` (default 600000 ms),
  `MCP_OAUTH_FLOW_TTL` (default 900000 ms) [2]. **[code/doc]**

Stability verdict: no-auth, static headers, API keys, and per-user custom credentials are
all stable, long-standing mechanisms. OAuth with PKCE, client discovery/DCR, refresh and
OBO are documented and feature-complete in the current docs (v0.8.x); treat them as
normal/stable, not experimental [2][3].

### 4.3 Minimal working configuration for `https://example.org/mcp`

```yaml
# 1) No auth
mcpServers:
  youtube:
    type: streamable-http
    url: https://example.org/mcp

# 2) Static Bearer header (server-side env var)
mcpServers:
  youtube:
    type: streamable-http
    url: https://example.org/mcp
    headers:
      Authorization: 'Bearer ${YOUTUBE_MCP_TOKEN}'

# 3) Arbitrary custom header (env var)
mcpServers:
  youtube:
    type: streamable-http
    url: https://example.org/mcp
    headers:
      X-API-Key: '${YOUTUBE_MCP_KEY}'

# 4) Structured API key, admin-provided, Bearer
mcpServers:
  youtube:
    type: streamable-http
    url: https://example.org/mcp
    apiKey:
      source: 'admin'
      authorization_type: 'bearer'

# 5) OAuth (authorization-code + PKCE), explicit endpoints
mcpServers:
  youtube:
    type: streamable-http
    url: https://example.org/mcp
    oauth:
      authorization_url: https://example.org/oauth/authorize
      token_url: https://example.org/oauth/token
      redirect_uri: http://localhost:3080/api/mcp/youtube/oauth/callback
      scope: 'read execute'
```

Source: schema examples in [3]; OAuth callback path (`${baseUrl}/api/mcp/<server>/oauth/callback`) in [2].

---

## 5. DeepSeek Harness findings

Primary sources: official `deepseek-ai/deepseek-harness` repository, default branch
`master` (active 2026-09-15), specifically the `@deepseek-ai/dsh-mcp-client` package
(README [5], `src/transport.ts` [6], `src/index.ts` [7], `src/connection.ts` [8]); repo
overview [4]. Checked 2026-09-16.

> Important: this section distinguishes merged official source from proposals/issues/
> forks, and does NOT assert release status. All statements below reflect code
> **confirmed on the current official `master` branch** as of research time — i.e.
> merged official source. Being on `master` is **not** asserted to equal a published/
> packaged release, because no package/version was independently matched to these
> commits in this pass. No relevant PRs/issues/forks were treated as released support.

### 5.1 Transport

- The MCP client supports exactly **two** transports: `stdio` and `streamable-http`.
  `createTransport()` switches on `config.transport` and instantiates either
  `StdioClientTransport` or `StreamableHTTPClientTransport` [6]. **[code]**
- **No SSE** and **no websocket** client transport in this package. README states
  "Choose stdio for a local program and Streamable HTTP for a service" [5]. **[code]**
- Endpoint configured via `url` (streamable-http) — e.g. `url: http://localhost:3000/mcp`
  [5][7]. **[code]**
- The pinned MCP SDK selects the 2026-07-28 protocol revision when available and falls
  back to supported legacy revisions [5]. **[code]**

### 5.2 Authentication

- **Static headers** — the only authentication mechanism. `headers: Record<string,
  string>` is attached verbatim to MCP requests via
  `new URL(config.url)`, `{ requestInit: { headers: config.headers } }` [6][7]. This
  supports `Authorization: Bearer <token>` and arbitrary custom header names (e.g.
  `X-API-Key`). **[code]**
- **API keys / Bearer** — supported, as headers. Example given in README:
  `headers: { Authorization: 'Bearer ${process.env.MCP_TOKEN}' }` [5]. **[code]**
- **OAuth discovery** — **No.** There is no OAuth code, no authorization-server metadata
  handling, and no `oauth` field in the config schema [7]. **[code]**
- **Authorization-code flow / PKCE** — **No** OAuth/implicit authorization-handling in
  this package [7][8]. **[code]**
- **Refresh-token handling** — **No.** The only lifecycle logic is a transport-level
  reconnect policy (connection-loss backoff), unrelated to token refresh [8]. **[code]**
- **Reauthorization after 401** — **No.** `401` appears nowhere in the client README
  [5]. Reconnect auto-reconnects on *lost connections* (transport close / failed
  negotiation), not on HTTP 401 auth challenges [8].
- Config schema field inventory for `streamable-http`: `transport`, `serverName`, `url`,
  `headers`, `toolCallTimeoutMs`, `failOnStartupError`, `maxInstructionBytes`,
  `reconnect` [7]. No auth model beyond `headers`. **[code]**

### 5.3 Merged official source vs. released package vs. proposals

- All of the above is **confirmed in the current official `master` branch** (merged
  official source) at research time. This means the current default-branch source
  supports Streamable HTTP + static headers and contains **no OAuth client lifecycle**.
- **This does not by itself prove a released/published package version.** No release or
  package version was independently matched to the examined commits in this pass;
  therefore "current published package" is **not** asserted here. Re-check the released
  package if release-version confidence is required.
- No MCP OAuth implementation was found **merged** in the official repo's MCP client. A
  GitHub issue search for MCP OAuth in the repo returned no results at research time
  (2026-09-16). Treat any claim of DeepSeek Harness MCP OAuth **release/merge** support
  as not-found at research time **[unknown / not found]**.
- No OAuth mention in the README at all ("OAuth": 0 occurrences across the mcp-client
  README) [5]. **[code]**
- Distinction maintained: **(a)** merged official source [code] — what this report
  established; **(b)** released/published package — not independently verified here;
  **(c)** proposals/issues/forks — none found relevant to MCP OAuth in the mcp-client
  at research time.

### 5.4 Smallest real configuration to connect DeepSeek Harness to `https://example.org/mcp` today

```yaml
- id: mcp-youtube
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: youtube
    transport: streamable-http
    url: https://example.org/mcp
    headers:
      Authorization: 'Bearer ${MCP_TOKEN}'
```

That is the smallest real configuration: `transport: streamable-http`, `url`, and a
static `Authorization` header [5][6][7]. OAuth is not an option in this client.

---

## 6. Hosted-reference findings

Three public YouTube transcript MCP endpoints were checked as interoperability
references on 2026-09-16. Purpose: learn from their transport/auth choices and their
client compatibility approach — not to treat proprietary infrastructure as a blueprint
and not to treat hosted references as architecture authorities.

### 6.1 ergut / youtube-transcript-mcp (`https://youtube-transcript-mcp.ergut.workers.dev`)

- **Open source, self-hostable:** `github.com/ergut/youtube-transcript-mcp`, deployable to
  Cloudflare Workers (one-click deploy; manual clone + `npm run deploy`) [9]. **[code]**
- **Transport:** exposes **both** `/sse` and `/mcp` endpoints; root JSON reports
  `{"endpoints":{"sse":"/sse","mcp":"/mcp"}, ... "status":"ready"}` [10]. README documents
  "Transport: Server-Sent Events (SSE) or HTTP" [9]. **[code/doc]**
- **Authentication:** **none** — "Authentication: None required (public server)" [9].
  **[doc]**
- **Documented clients:** Claude Desktop (via the `mcp-remote` bridge on the `/sse` URL)
  and "any compatible client"; explicitly touts zero-setup access incl. mobile Claude
  [9]. **[doc]**
- **What it demonstrates:** a single public no-auth endpoint with an SSE and a
  streamable-http URL can interoperate across Claude-ecosystem clients. It relies on
  `mcp-remote` (a client-side bridge + OAuth helper) for Claude Desktop rather than
  implementing auth server-side. Self-hostable, so a viable open-source interoperability
  reference.

### 6.2 youtube-transcript.ai (`https://youtube-transcript.ai/mcp`)

- **Commercial hosted service** (drives the youtube-transcript.ai product); **not**
  documented as self-hostable [11]. **[doc]**
- **Transport:** Streamable HTTP (explicitly: "the server URL to
  https://youtube-transcript.ai/mcp (transport: Streamable HTTP)") [11]. **[doc]**
- **Authentication:** **none / keyless** — "Free · no API key · no signup · works in any
  MCP-compatible client… No account, no token, no quota wall." [11]. **[doc]**
- **Documented clients:** Claude (Add custom connector, URL only) and ChatGPT (Developer/
  Custom connector → Add MCP server, Streamable HTTP); also claims "any MCP-compatible
  client" [11]. **[doc]**
- **What it demonstrates:** a **keyless public Streamable HTTP** endpoint is a proven
  deployment shape for YouTube-transcript tooling across Claude and ChatGPT today,
  avoiding OAuth entirely. It also shows that unauthenticated public exposure is
  commercially acceptable for a transcript proxy — but this is exactly the model the
  task instructs us NOT to make a production default for our own server.

### 6.3 BulkTranscripts (`https://bulktranscripts.co/mcp`)

- **Commercial hosted service** (freemium: 30 free transcripts, API keys `bt_ak_`), not
  self-hostable [12]. **[doc]**
- **Transport:** the `/mcp` path answered HTTP 405 to a GET at research time (method-not-
  allowed) — consistent with an MCP Streamable HTTP endpoint that only accepts POST
  (observed 2026-09-16) **[observed / unknown]**. The site offers a REST API plus an MCP
  server and documents clients: Claude, ChatGPT, Cursor, Claude Code, Codex, VS Code,
  Grok, OpenClaw, n8n, Make [12]. **[doc]**
- **Authentication (REST API):** confirmed — the REST API uses `Authorization: Bearer
  <API key>` [12]. **[doc]**
- **Authentication (MCP `/mcp` endpoint): Not independently confirmed.** The cited page
  documents the REST API key flow; it does **not** explicitly state the MCP endpoint's
  authentication model. Therefore the MCP endpoint's auth is **[unknown / not
  independently verified]** here. Do not use BulkTranscripts as evidence for MCP
  Bearer/API-key authentication.
- **What it demonstrates:** a commercial YouTube-transcript platform that offers a
  key/Bearer-authenticated REST API plus an MCP server, documented for a broad set of LLM
  frontends. It shows key/Bearer for the REST API only; the MCP endpoint's own auth model
  is not confirmed from this source. Treat it as an interoperability example, not an
  architecture authority for MCP Bearer auth.

---

## 7. Compatibility matrix

Rows = capability, columns = target client. Confidence class in parentheses (see legend
in the intro and the "notes" column for Partial/Beta/Unknown).

| Capability | Claude | LibreChat | DeepSeek Harness | Notes for Partial/Beta/Unknown |
|---|---|---|---|---|
| Streamable HTTP | Yes [doc] | Yes [doc/code] | Yes [code] | Claude: default non-`/sse` URL. LibreChat: recommended production. DSH: only HTTP/stdio. |
| Legacy SSE | Yes [doc] | Yes [doc/code] | No [code] | Claude: URL ending in `/sse`. LibreChat: supported, "not recommended for production". DSH: no SSE transport. |
| Public HTTPS | Yes [doc] (required for Claude) | Yes [doc/code] | Yes [code] | HTTP(s) URL everywhere. Public HTTPS is a deployment requirement for Claude Custom Connectors; for LibreChat/DSH it is a deployment/access choice, not an MCP protocol or hard client requirement (both also take local/private http URLs) [2][5]. |
| Local/private endpoint | Partial [unknown] | Yes [doc] | Yes [code] | Claude custom connectors expect an HTTPS (public) URL; local/private reachability undocumented. LibreChat/DSH take arbitrary http(s) URLs (e.g. localhost examples) [2][5]. |
| Unauthenticated MCP | Yes [doc] | Yes [doc] | Yes [code] | All can connect with no auth; not recommended as a production default. |
| Static Bearer token | Beta [doc] | Yes [doc/code] | Yes [code] | Claude: only via beta Request headers. LibreChat: headers / apiKey bearer. DSH: `headers` map. |
| Arbitrary request headers | Beta [doc] | Yes [doc/code] | Yes [code] | Claude: beta, name list + Anthropic approval of custom names. LibreChat: arbitrary `headers`. DSH: arbitrary `headers`. |
| API-key header | Beta [doc] | Yes [doc/code] | Yes [code] | Claude: `x-api-key` etc. via beta Request headers. LibreChat: structured `apiKey` (admin/user). DSH: any header. |
| OAuth | Yes [doc] | Yes [doc] | No [code] | Claude: authorization-code + client discovery + DCR + own client. LibreChat: PKCE + discovery + DCR + refresh. DSH: none. |
| OAuth discovery | Yes [doc] | Yes [doc] | No [code] | Claude: Client ID Metadata Document / register automatically. LibreChat: client discovery / DCR by default. DSH: none. Exact discovery mechanics for Claude beyond these options are [std]/[unknown]. |
| Dynamic Client Registration | Yes [doc] | Yes [doc] | No [code] | Claude: "Register automatically". LibreChat: DCR when no client id/secret. DSH: none. |
| PKCE | Expected [std] | Yes [doc] | No [code] | Claude: standard MCP/OAuth flow, not explicitly stated on the connector page → protocol expectation, not verified [doc]. LibreChat explicitly documents PKCE. DSH: no OAuth at all. |
| Automatic token refresh | Unknown / Expected [std] | Yes [doc] | No [code] | Claude: not documented on connector page; standard OAuth → protocol expectation, not verified client behavior [unknown]. LibreChat: documented refresh + silent 401 recovery. DSH: none. |
| Per-user authentication | Yes [doc] | Yes [doc] | Partial [code] | Claude: OAuth per user. LibreChat: per-user OAuth sessions + customUserVars. DSH: headers are config-wide (not per-user); per-user would require separate server entries (Partial). |
| Stateless MCP compatibility | Yes [doc] | Yes [doc] | Yes [code] | Streamable HTTP is stateless-friendly; LibreChat explicitly favors stateless for multi-user. |
| Configurable timeout | Unknown | Yes [code/doc] | Yes [code] | Claude: not documented on connector page [unknown]. LibreChat: `timeout`/`initTimeout`. DSH: `toolCallTimeoutMs`. |
| known payload/timeout constraints | Unknown | Partial [code/doc] | Yes [code] | LibreChat: stdio 10 MB message cap; OAuth timing env vars. DSH: `maxInstructionBytes` (default 32768), `toolCallTimeoutMs` (default 60000). Claude: not documented. |
| Reconnect / re-auth behavior | Unknown / Expected [std] | Yes [doc/code] | Yes [code] | Claude: not documented on connector page [unknown]. LibreChat: silent 401 re-auth + reconnect. DSH: transport reconnect w/ backoff (no 401 re-auth). |

---

## 8. Common transport denominator

**Transport common denominator: Streamable HTTP.** All three clients support Streamable
HTTP as the modern in-scope transport:

- Claude: a plain `/mcp` URL = Streamable HTTP; SSE is a legacy `/sse` opt-in [1].
- LibreChat: Streamable HTTP is the production-recommended transport; SSE is discouraged
  [2].
- DeepSeek Harness: `mcp-client` implements exactly `stdio` and `streamable-http`; there
  is **no SSE** transport [6][7].

**Deployment reachability is a separate question from the protocol transport.**

- **Public HTTPS is required when Claude / claude.ai Custom Connectors are part of the
  deployment target** — Claude connects to an HTTPS address [1].
- **LibreChat and DeepSeek Harness also accept local/private HTTP endpoints** (e.g.
  localhost/LAN [2][3][5]); for them public HTTPS is a deployment/access decision, not an
  MCP protocol requirement and not a hard client requirement.

Conclusion: the endpoint must be **Streamable HTTP**, and it must be **publicly
reachable over HTTPS whenever Claude Custom Connectors are included**. Do not treat
public HTTPS as an MCP protocol requirement or as a hard requirement of
LibreChat/DeepSeek Harness.

**Legacy SSE is an optional compatibility feature only.** Claude and LibreChat can
support legacy SSE, DeepSeek Harness cannot, and modern in-scope client configurations do
not require it. ergut happens to expose `/sse` plus `/mcp` [9][10], but that does not make
SSE necessary. There is **no current requirement for the target modern client paths**;
legacy SSE should not be recommended for implementation unless Phase 0 finds a concrete
reason.

## 9. Common authentication denominator

**There is currently no single reliable authentication mode shared by all three target
clients.**

- **Static credentials (Bearer / API-key header)** are the only *mechanism* exposed by
  all three in some configuration: LibreChat (stable `headers`/`apiKey`), DeepSeek
  Harness (stable `headers`), and Claude (only where the **Beta** Request-headers feature
  is available). Because Claude's support is Beta / gradual rollout and org-gated, this
  is **not** a dependable universal deployment mode.
- **OAuth** covers Claude and LibreChat reliably (authorization-code + PKCE, discovery,
  DCR, refresh), but the current DeepSeek Harness MCP client has **no OAuth** and cannot
  do discovery/PKCE/refresh/401-reauth. OAuth is therefore **not** a universal mode
  either while DeepSeek Harness is in scope.
- **No-auth** is supported by all three but is explicitly out of scope as a production
  default for public exposure per the task.

Conclusion: two complementary public authentication profiles are required — a **static
credential compatibility mode** (stable for LibreChat + DeepSeek; conditional/Beta for
Claude) and an **OAuth public standard mode** (stable for Claude + LibreChat; unsupported
by the current DeepSeek Harness MCP client). See section 11.

## 10. Important incompatibilities

1. **OAuth vs DeepSeek Harness.** Claude and LibreChat support MCP OAuth (discovery,
   DCR; PKCE/refresh are documented for LibreChat and are protocol expectations for
   Claude). DeepSeek Harness cannot authenticate via OAuth at all today; it only sends
   static headers. An OAuth-only endpoint is unusable from DeepSeek Harness. **[code]**
2. **Claude request-header beta gating.** Claude's static-header/API-key support is beta
   + limited organizations and cannot be treated as universally available; a
   header-only design still requires Claude users to obtain org access. **[beta]**
3. **SSE is not a universal transport.** DeepSeek Harness has no SSE client transport.
   SSE-only endpoints would exclude it. Claude still uses SSE (legacy) and LibreChat
   supports it, but neither requires it. **[code]**
4. **Per-user vs shared credentials.** Claude and LibreChat have per-user identity models
   (OAuth per user; LibreChat per-user sessions/customUserVars). DeepSeek Harness headers
   are configuration-global — there is no built-in per-user credential story without
   launching one plugin instance per credential set (Partial). **[code]**
5. **401 / re-auth semantics.** LibreChat implements silent 401 recovery (one bounded
   refresh+reconnect); DeepSeek Harness does not (reconnect is transport-level only), and
   Claude's behavior is not documented on the connector page. A server relying on
   401-challenge-driven OAuth would work for LibreChat and, per the standard, for Claude,
   but not for DeepSeek. **[code]/[unknown]**

## 11. Recommended client-facing deployment profiles (evidence-based)

Three profiles, chosen to match client reality, not the ideal spec.

### A. Local / trusted (Streamable HTTP, no auth, localhost/LAN)

- Shape: Streamable HTTP on `http://localhost:PORT/mcp` (or LAN), no authentication.
- Suitable for: **local models such as LM Studio / Qwen** and single-operator local use
  (LibreChat and DeepSeek Harness both accept localhost URLs [3][5]).
- **Risk:** if bound to anything reachable outside the host, an unauthenticated MCP
  endpoint lets anyone invoke transcript extraction (a read-only tool here, but still
  unauthenticated access + potential abuse of server resources). Must stay loopback/LAN
  and must never be the default for a public host. This profile is explicitly NOT a
  production default — only a trusted-local convenience.

### B. Static credential compatibility mode (public HTTPS + static Bearer/API-key header)

- Shape: public HTTPS Streamable HTTP `/mcp`; authentication via a static
  `Authorization: Bearer <token>` (or `X-API-Key`) request header, operator-managed
  shared credential. This is a **compatibility** mode, not a dependable universal mode.
- **Stable for:** **LibreChat** (stable headers/apiKey) and **DeepSeek Harness** (stable
  headers).
- **Conditional/Beta for Claude:** works only where the beta Request-headers feature is
  enabled; NOT universal. Until that beta is generally available, this mode cannot be
  advertised as dependable "works in Claude" for everyone.
- Recommendation: use this as the compatibility mode for LibreChat + DeepSeek; pair it
  with OAuth (profile C) to cover Claude.

### C. OAuth public standard mode (public HTTPS + MCP OAuth)

- Shape: public HTTPS Streamable HTTP `/mcp` + MCP OAuth (authorization-server metadata
  discovery, authorization-code + PKCE, optional DCR), per-user sign-in.
- **Stable/normal for:** **Claude** (native, incl. Claude's published client identity
  and "register automatically" [1]) and **LibreChat** (explicit broad OAuth support with
  discovery+PKCE+refresh [2][3]).
- **Unsupported by the current DeepSeek Harness MCP client** — its source has no OAuth.
  Until the harness client adds OAuth, DeepSeek must use profile B.
- Recommendation: use OAuth as the per-user mode for Claude/LibreChat, but keep the
  static-header mode (B) live so DeepSeek is not locked out.

### Hypotheses confirmed/rejected

- "Static credential compatibility (HTTPS + static Bearer) is a shared baseline for
  LibreChat + DeepSeek" — **confirmed**; **rejected as a dependable universal mode for
  Claude** due to the Beta gate (it is conditional/Beta for Claude).
- "OAuth public standard (HTTPS + MCP OAuth)" — **confirmed** for Claude + LibreChat;
  **rejected as a universal mode** because DeepSeek Harness lacks OAuth.
- "Local/trusted no-auth is fine" — **confirmed** as a local-only convenience, with an
  explicit warning against public exposure.

---

## 12. Unknowns requiring real-world testing

1. Whether Claude's Request-headers (static header) feature is enabled for a given org /
   account, and whether it depends on subscription plan vs account/org feature flag [1]
   **[unknown]**.
2. Claude's behavior when a connector is configured with request headers and the server
   additionally advertises OAuth discovery — does Claude attempt OAuth, send the header,
   or both? (Only the `Authorization`-on-OAuth exclusion is documented [1].)
3. Claude connector redirect handling and stateless/session expectations across proxy /
   CDN layers.
4. DeepSeek Harness behaviour on an HTTP 401 from a Streamable HTTP server — whether the
   SDK attempts any auth challenge, and what the tool call surfaces (current code does
   only transport-level reconnect [8]).
5. LibreChat OAuth against a Streamable HTTP endpoint served behind a reverse proxy /
   tunnel — callback URL reachability and the 409-stale-SSE recovery path in practice
   [2].
6. Actual payload/timeout ceiling that each client imposes on large transcript tool
   results (Claude undocumented; LibreChat stdio 10 MB for stdio only; DeepSeek
   `maxInstructionBytes` 32768 is about instructions, not tool results).
7. Whether the three hosted references' claimed "any MCP client" compatibility holds for
   DeepSeek Harness specifically (they document Claude/ChatGPT/others, not DeepSeek).
8. Claude Cowork's MCP behavior (supports factories? request headers?) — not covered by
   the primary connector page.
9. Whether the DeepSeek Harness MCP OAuth support is (or becomes) present in a
   **published/released** package version, independent of the `master` source examined
   [5].
10. BulkTranscripts' actual MCP `/mcp` endpoint authentication model, which is not stated
    by the cited source [12].

## 13. Exact experiments to resolve those unknowns

1. **Claude org gate probe:** in a Claude Team/Enterprise account, open Add custom
   connector and check for the Request headers section; record plan type + result. Repeat
   across a Free/Pro account to detect plan vs org dependency. (Answers unknown 1.)
2. **Claude header-vs-OAuth precedence:** configure a connector with a non-Authorization
   request header against a test server that (a) advertises OAuth discovery and (b) does
   not; observe which auth challenge the client follows. (Unknown 2.)
3. **Claude redirect test:** host the server behind a CDN that 301/302-redirects
   `/mcp`; confirm Claude follows and document any failure. (Unknown 3.)
4. **DSH 401 probe:** point DeepSeek Harness `mcp-client` at a Streamable HTTP server
   that returns 401 for `tools/list`/`tools/call`; confirm whether any retry/challenge
   occurs and log the tool-call result and reconnect counter. (Unknown 4.)
5. **LibreChat proxy/OAuth end-to-end:** run LibreChat against the server behind a tunnel
   (like the OpenAI tunnel pattern), complete client-discovery OAuth, and exercise the
   409-stale-SSE reconnect path by dropping the connection mid-session. (Unknown 5.)
6. **Payload ceiling sweep:** return transcripts of increasing size (e.g. 60k, 120k,
   250k chars) from a test server and record per-client truncation/error. (Unknown 6.)
7. **DSH against hosted refs:** configure DeepSeek Harness to the ergut `/mcp` (or a
   self-hosted ergut) Streamable HTTP endpoint and to youtube-transcript.ai/mcp; verify
   tool discovery + a real transcript call. (Unknown 7.)
8. **Cowork check:** connect the same server to Claude Cowork (where available) and
   verify header vs OAuth behavior. (Unknown 8.)
9. **DSH released-package check:** pin/install the published `@deepseek-ai/dsh-mcp-client`
   package and confirm whether OAuth and static-header behaviour match the `master` source
   examined. (Unknown 9.)
10. **BulkTranscripts MCP auth probe:** connect an MCP client to `bulktranscripts.co/mcp`
    and observe whether it requests a header/key, OAuth, or none — separate from the
    REST API key. (Unknown 10.)

## 14. Phase 0 audit questions (not implementation orders)

Phase 0 (a repository audit, not started here) should audit the existing repository
against these client-derived requirements. This section deliberately raises **questions**
for that audit; it does **not** decide where or how mechanisms are implemented.

### Transport audit

- Does the existing MCP server already satisfy a Streamable HTTP `/mcp` transport
  requirement without modification? (The existing project may already expose Streamable
  HTTP `/mcp`.)
- Which current components are generic MCP core versus ChatGPT/OpenAI-tunnel-specific
  deployment?
- Is any protocol/server change actually needed, or only a new exposure/security layer?

### Security / auth audit

- Where should static-credential validation live?
- Where should MCP OAuth live?
- Should either mechanism be implemented inside the YouTube MCP process, or in a
  gateway/sidecar/ingress layer?
- Which protections currently come from the OpenAI Secure MCP Tunnel, and therefore
  disappear in a public (non-tunnel) deployment?

### Deployment audit

- What changes are required to expose the existing server safely through HTTPS?
- Which existing Compose/generator blocks can remain unchanged?
- Which blocks need conditional deployment modes (tunnel vs public, local vs remote)?

### Carried-forward requirements (from Phase -1)

These stay as Phase -1 requirements, but their implementation location is NOT decided
here:

- Streamable HTTP must be supported.
- Static-credential mode is needed for DeepSeek/LibreChat compatibility.
- OAuth is needed for reliable Claude/LibreChat public authentication.
- No-auth remains local/trusted only.
- The OpenAI Secure MCP Tunnel remains a supported deployment path.

---

## Conclusion: what our server must support, and what it must not assume

**To serve Claude, LibreChat and DeepSeek with ONE YouTube MCP implementation today:**

### It must support
- A **Streamable HTTP** endpoint at `/mcp`, made **publicly reachable over HTTPS when
  Claude Custom Connectors are included** (transport is common to all three; public HTTPS
  is the deployment requirement for Claude, and an access/deployment choice for
  LibreChat/DeepSeek).
- **Static-header / API-key authentication** — `Authorization: Bearer <token>` and/or a
  custom API-key header — so both LibreChat (stable headers/apiKey) and DeepSeek Harness
  (stable headers) can connect today.
- **MCP OAuth** (authorization-server discovery + authorization-code + PKCE + refresh,
  optional Dynamic Client Registration) alongside it, so Claude and LibreChat get a
  dependable per-user public authentication path.
- Strictly-controlled no-auth mode for trusted/local use only.
- **Legacy SSE only as an optional compatibility feature with no current requirement for
  the target modern client paths** — not recommended unless Phase 0 finds a concrete
  reason.

### It must NOT assume
- **That OAuth alone is enough** — it is not; the current DeepSeek Harness MCP client has
  no OAuth, so an OAuth-only endpoint excludes it.
- **That static headers are a dependable universal mode** — they are only the *mechanism*
  exposed by all three; Claude's request-header support is Beta / gradual rollout, so no
  single auth mode is both universal and dependable today.
- **That every Claude org can use static request headers** — that is Beta / gradual
  rollout; header-only auth must be paired with OAuth for Claude.
- **That SSE is a universal transport** — DeepSeek Harness cannot consume SSE.
- **That per-user identity is available on every client** — DeepSeek headers are
  config-global; per-user auth there needs separate server entries.
- **That a client will re-authorize after a 401** — only LibreChat has documented silent
  401 recovery; DeepSeek does not, and Claude is not documented on the source page.
- **That unauthenticated public exposure is acceptable** — it is not the production
  default; it is a trusted-local option only.

**In short:** the single non-negotiable transport requirement is **Streamable HTTP** at
`/mcp`, made publicly reachable over **HTTPS when Claude Custom Connectors are included**.
There is **no single reliable authentication mode shared by all three clients today**, so
the server must offer two complementary public modes — a **static-credential compatibility
mode** (LibreChat + DeepSeek; Claude conditional/Beta) and an **OAuth public standard
mode** (Claude + LibreChat; unsupported by current DeepSeek Harness) — never a single
exclusive mode.

---

## Sources

Checked 2026-09-16 (UTC) unless noted.

1. Claude (Anthropic): "Third party connectors with remote MCP" —
   https://claude.com/docs/connectors/custom/remote-mcp
2. LibreChat: "MCP" (features; transports, OAuth, headers, per-user credentials) —
   https://www.librechat.ai/docs/features/mcp
3. LibreChat: "MCP Servers Object Structure" (mcpServers YAML schema) —
   https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers
4. deepseek-ai/deepseek-harness (official repository, default branch `master`,
   pushed 2026-09-15) — https://github.com/deepseek-ai/deepseek-harness
5. DeepSeek Harness `@deepseek-ai/dsh-mcp-client` README —
   https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md
6. DeepSeek Harness `mcp-client/src/transport.ts` —
   https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/src/transport.ts
7. DeepSeek Harness `mcp-client/src/index.ts` (config schema) —
   https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/src/index.ts
8. DeepSeek Harness `mcp-client/src/connection.ts` (reconnect policy) —
   https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/src/connection.ts
9. ergut/youtube-transcript-mcp (README) —
   https://github.com/ergut/youtube-transcript-mcp
10. ergut hosted YouTube transcript MCP root (endpoints `/sse`,`/mcp`) —
    https://youtube-transcript-mcp.ergut.workers.dev/
11. youtube-transcript.ai MCP server page —
    https://youtube-transcript.ai/mcp
12. BulkTranscripts developer/MCP docs —
    https://bulktranscripts.co/docs
