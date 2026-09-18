# Multi-Client MCP — Phase 0 Repository & Architecture Audit

Status: Phase 0 (repository and architecture audit only).
Branch audited: `feat/multi-llm-public-mcp`
Date: 2026-09-16 (UTC)
Authoritative requirements input: `docs/MULTI_CLIENT_MCP_REQUIREMENTS.md` (Phase -1).

Scope: Determine what already exists, what is ChatGPT-specific, what is generic MCP,
what security/exposure is currently supplied by the OpenAI Secure MCP Tunnel, and what
concrete gaps remain for the Phase -1 deployment profiles (local/trusted no-auth, public
static-credential, public OAuth). This document establishes facts for Phase 1
architecture decisions. It does not implement anything.

Legend used throughout:
- **[VERIFIED]** — fact confirmed by reading repository source on this branch.
- **[INFER]** — reasonable inference from verified code/configuration.
- **[EXTERNAL]** — belongs to the external OpenAI Secure MCP Tunnel product / control
  plane; cannot be proven from this repository.
- **[RESOLVED-LATER]** — deliberately left for Phase 1; not decided here.

---

## 1. Executive summary

The most important finding of this audit is **structural and favorable**: the MCP server
is already a **generic, stateless, read-only Streamable HTTP server** with no ChatGPT
coupling inside the application code, and it already implements the Phase -1 transport
requirement (`stateless_http=True`, `/mcp` Streamable HTTP, configurable `MCP_TRANSPORT`,
a `/healthz` endpoint, `json_response`). The ChatGPT/OpenAI coupling lives **entirely
outside the MCP application** — in the Secure MCP Tunnel deployment, the generators that
emit a tunnel-enabled Compose stack, and documentation/branding (repo/docs names).

Because the server binds to `MCP_HOST` and is driven entirely by environment variables, it
**can run standalone today** with `MCP_TRANSPORT=streamable-http` and no tunnel. In the
generated ChatGPT stack the tunnel is a required, outbound-only, non-port-exposing
sidecar; removing it leaves a functional local MCP server (nothing in `server.py` depends
on the tunnel).

The gaps are therefore all in **security/exposure**, not in the MCP core:

- The server has **no authentication, no TLS in-process, no rate limiting, no origin/host
  checks, no request-size limits at the auth boundary**. None of this is missing "by
  accident" — the current public path is exclusively through the Secure MCP Tunnel, which
  (per docs, **[EXTERNAL]**) supplies the TLS/public endpoint and upstream control-plane
  access control.
- For **public static-credential** deployment, the missing pieces are: HTTPS (before the
  server, since it serves plain HTTP), and a static Bearer/API-key validation boundary
  (in-process middleware, gateway, or ingress — **undecided**), plus rate limiting.
- For **public OAuth** deployment, additionally missing: OAuth authorization-server
  metadata/discovery, authorization code flow, token validation, and refresh — a
  genuinely larger layer, again with undecided placement.

**Is any MCP-core rewrite required? No.** The tool surface is complete and read-only, the
transport is already Streamable HTTP, the server is stateless and multi-client-compatible.
Multi-client support requires **exposure and security layers**, not core changes.

Bottom line for Phase 1: preserve the generic MCP core and the tunnel path; add a
separate, injectable security/exposure layer (TLS + credential validation, and
separately MCP OAuth) without touching the YouTube/MCP tool code.

---

## 2. Repository map

Concise structural map of components relevant to Remote MCP deployment and the
multi-client goal (unrelated files omitted).

| Path | Role | Relevance |
|---|---|---|
| `src/youtube_mcp/server.py` | MCP server (FastMCP): tool registration, `/healthz`, `main()` transport dispatch | **MCP core / entrypoint** |
| `src/youtube_mcp/core.py` | `YouTubeService`, `Settings`, parsers, error envelope, pagination/search | **MCP core (YouTube tool logic)** |
| `src/youtube_mcp/updater.py` | Optional staged yt-dlp self-updater (background thread) | Tool behavior (ops); not deployment |
| `src/youtube_mcp/__init__.py` | package version `0.1.0` | metadata |
| `pyproject.toml` | deps: `mcp>=1.12,<2`, `yt-dlp`; console script `youtube-current-data-mcp` | dependencies / entrypoint |
| `Dockerfile` | Builds MCP image; ENV defaults (`MCP_TRANSPORT=streamable-http`, `MCP_HOST=0.0.0.0`, `MCP_PORT=8765`), HEALTHCHECK, `USER 65532`, read-only | **MCP container / entrypoint** |
| `compose.yaml` | Root compose: single `youtube-mcp` service, loopback-published port `127.0.0.1:8765:8765`, read-only, tmpfs | **Manual/local compose (no tunnel)** |
| `.env.example` | Documents all env vars (MCP + YouTube + yt-dlp updater) | config reference |
| `generators/generate_docker-compose_for_ChatGPT_MCP.sh` | Bash interactive generator → two-service (mcp + tunnel) Portainer stack | **Deployment generator (ChatGPT/tunnel)** |
| `generators/generate_docker-compose_for_ChatGPT_MCP.ps1` | PowerShell equivalent generator | **Deployment generator (ChatGPT/tunnel)** |
| `docs/assets/generator.js` | Client-side web generator (same two-service stack), CSP/no-telemetry | **Web generator (ChatGPT/tunnel)** |
| `docs/index.html` | Web generator page (CSP, forms) | Web generator UI |
| `docker/openai-mcp-tunnel/Dockerfile` | Thin wrapper over `ghcr.io/openai/tunnel-client` (pinned digest), non-root, healthcheck | **Secure MCP Tunnel integration** |
| `docker/openai-mcp-tunnel/entrypoint.sh` | Env validation + `exec tunnel-client run` | **Secure MCP Tunnel integration** |
| `docker/openai-mcp-tunnel/versions.env` | Pinned upstream tunnel-client pins | **Secure MCP Tunnel integration** |
| `docker/openai-mcp-tunnel/tests/test_entrypoint.bats` | Entrypoint BATS tests | **Tunnel tests** |
| `.github/workflows/publish-container.yml` | Test + publish MCP image (main/tags) | CI |
| `.github/workflows/publish-openai-mcp-tunnel.yml` | Test + publish tunnel wrapper (manual `workflow_dispatch`) | CI |
| `scripts/live_smoke.py` | Live YouTube smoke script (not MCP transport) | ops helper |
| `docs/ARCHITECTURE.md`, `docs/INTERFACE.md`, `docs/OPENAI_SECURE_MCP_TUNNEL.md`, `docs/PORTAINER_STACK_GENERATORS.md`, `docs/END_TO_END_VALIDATION.md`, `docs/CHATGPT_SETUP.md` | Architecture/interface/deployment validation docs | Documentation (deployment-relevant) |
| `tests/` | Core/failure/playlist/transcript/updater/generator/tunnel tests | **Test coverage** |

Key structural facts:
- The **application layer is fully decoupled** from the tunnel and from any specific
  client. It is named `youtube-current-data-mcp` / `YouTube Current Data (read-only)`
  (server.py:12-13, pyproject.toml:2), i.e. generic, not "chatgpt-youtube-mcp".
- The **only** ChatGPT-tagged artifact is the repo/package branding and the
  tunnel-aware generators/docs. There is **no `openai`/`chatgpt`/`tunnel` import or code
  path inside `src/youtube_mcp/`**.

---

## 3. Existing MCP core capability

Verified from `src/youtube_mcp/server.py`:

- **Framework/library:** `mcp.server.fastmcp.FastMCP` (from the official `mcp` Python SDK),
  import at server.py:6.
- **Version:** dependency `mcp>=1.12,<2` (pyproject.toml:9). Exact installed minor not
  pinned; `>=1.12` modern SDK. **[VERIFIED]**
- **Instantiation (server.py:12-18):**
  ```python
  mcp = FastMCP("YouTube Current Data (read-only)",
      host=os.getenv("MCP_HOST", "127.0.0.1"),
      port=int(os.getenv("MCP_PORT", "8765")),
      stateless_http=True,
      json_response=True)
  ```
- **Transport:** `main()` reads `MCP_TRANSPORT` (default `streamable-http`), allows
  `{streamable-http, stdio, sse}`, then `mcp.run(transport=transport)` (server.py:221-229).
  **[VERIFIED]** Streamable HTTP is the default and is the Phase -1 requirement.
- **Streamable HTTP endpoint path:** the FastMCP mount serves the MCP endpoint at `/mcp`
  (confirmed by tunnel `MCP_SERVER_URL: http://youtube-mcp:8765/mcp` in all three
  generators, and Docker/compose). The exact path is provided by FastMCP's Streamable HTTP
  transport; the container/compose depend on it at `/mcp`. **[VERIFIED for usage;
  path resolution is the SDK's]**.
- **Health endpoint:** `@mcp.custom_route("/healthz", methods=["GET"])` (server.py:21-32),
  served on the same listener, returns `{"status":"ok","service":"youtube-current-data-mcp"}`.
  Docker HEALTHCHECK uses it (Dockerfile:26-27).
- **Stateful or stateless:** `stateless_http=True` (server.py:16). **[VERIFIED]** The server
  does not maintain per-user sessions; transcripts are stateless per request (pagination is
  via opaque continuation tokens in response content, not server-side sessions).
- **Bind address:** `MCP_HOST`, default `127.0.0.1` in code; the **Docker image overrides
  to `0.0.0.0`** (Dockerfile:19) and generators also set `MCP_HOST: '0.0.0.0'`. Root
  `compose.yaml` publishes `127.0.0.1:8765:8765` (loopback on host). **[VERIFIED]**
- **SSE:** not currently exposed as a custom route; `MCP_TRANSPORT=sse` is selectable at
  runtime (server.py:223) but the default is Streamable HTTP and no SSE is deployed.
  **[VERIFIED: sse is a configurable option, not the deployed mode]**
- **HTTP sessions:** `stateless_http=True` means no reliance on long-lived HTTP sessions.
- **Multi-client independence:** stateless, single shared `YouTubeService` instance, no
  per-user state → one server can serve multiple independent clients on Streamable HTTP.
  The only shared mutable state is an in-memory 5-minute `_video_info_cache`
  (core.py:347,420) which is cross-client by design (cache, not auth). **[VERIFIED/
  INFER]**
- **Does it already satisfy the Phase -1 Streamable HTTP requirement without modification?
  Yes.** The server is already a Streamable HTTP `/mcp` server with `/healthz`, stateless,
  json responses. **[VERIFIED]**

**Existing vs inferred vs missing:**
- Existing: Streamable HTTP `/mcp`, `/healthz`, configurable transport, stateless/json,
  env-driven bind, read-only tools, bounded/paginated transcripts.
- Inferred: multi-client independence follows from statelessness; no per-client isolation
  but none required for read-only public YouTube data with no user accounts.
- Missing (for Phase -1 profiles, not core): authentication, TLS, rate limiting, origin
  checks, request-size limits — all at the exposure/security boundary (see §6, §7).

---

## 4. ChatGPT/OpenAI coupling audit

Identified every ChatGPT/OpenAI-coupled element and classified it:

| Item | Location | Classification |
|---|---|---|
| README title "YouTube MCP for ChatGPT" | `README.md:1` | naming/branding only |
| README path diagram `ChatGPT -> MCP -> YouTube` | `README.md:15` | naming/branding only |
| Repo/package name `chatgpt-youtube-mcp`, GHCR `dracoform/chatgpt-youtube-mcp` | pyproject.toml:21-23, generators, workflows | naming/branding only |
| pyproject description "...bridge for ChatGPT and other MCP clients" | pyproject.toml:4 | documentation/naming |
| `docs/CHATGPT_SETUP.md`, `docs/END_TO_END_VALIDATION.md` (ChatGPT path) | docs | documentation-only |
| Generators named `..._for_ChatGPT_MCP.{sh,ps1}` and web generator; emit tunnel service | `generators/*`, `docs/assets/generator.js` | **configuration/generator coupling** |
| `docker/openai-mcp-tunnel/*` (tunnel wrapper + entrypoint + pins) | `docker/openai-mcp-tunnel/` | **deployment-only coupling** (tunnel sidecar) |
| `.env.example` "MCP_TRANSPORT: streamable-http for ChatGPT" | `.env.example:4-5` | documentation-only |
| MCP server app code (`src/youtube_mcp/*`) | — | **MCP core; NO ChatGPT coupling found** |

**Critical Phase 0 question answered:** Is ChatGPT-specific behavior inside the MCP
server itself, or outside it? **Outside.** The MCP application (`src/youtube_mcp/`) has
zero references to OpenAI/ChatGPT/tunnel. ChatGPT coupling is confined to:
1. the **Secure MCP Tunnel deployment** (sidecar + generated Compose adding
   `openai-tunnel` and env contract),
2. the **generators** (they emit a tunnel-enabled stack),
3. **documentation and naming/branding**.

This means the server core is reusable for LibreChat/DeepSeek/local clients **as-is**;
only exposure/generator/documentation work is needed.

---

## 5. Secure MCP Tunnel responsibility analysis

What the tunnel currently provides around the MCP server, from repository evidence, and
what is **[EXTERNAL]**:

| Responsibility | Evidence | Classification |
|---|---|---|
| Outbound-only connection from the server host to OpenAI control plane | entrypoint execs `tunnel-client run`; `docker/openai-mcp-tunnel/Dockerfile` (no published ports; HEALTHCHECK loopback `127.0.0.1:8080/healthz`); generators set no `ports:` on tunnel | **[VERIFIED]** (no host port published; outbound) |
| Tunnel config validation (id format, API key present, MCP_SERVER_URL abs http(s), log level) | `docker/openai-mcp-tunnel/entrypoint.sh` (exit 78) | **[VERIFIED]** |
| Secret handling (Control-plane API key via env only, never printed/argv) | entrypoint.sh:51-60,84-85; test_entrypoint.bats; OPENAI_SECURE_MCP_TUNNEL.md | **[VERIFIED]** |
| Public HTTPS endpoint + TLS termination | Docs reference "https://..." and the tunnel's control-plane-issued URL; not in this repo | **[EXTERNAL]** (OpenAI Secure MCP Tunnel product/control plane) |
| Access control / authentication at the public endpoint | Control-plane auth (`CONTROL_PLANE_API_KEY`) is for the tunnel↔OpenAI connection, not the MCP URL's downstream users; upstream behavior | **[EXTERNAL]** |
| Public endpoint creation | `CONTROL_PLANE_TUNNEL_ID` identifies a tunnel pre-created in ChatGPT | **[EXTERNAL]** (created in ChatGPT UI / control plane) |
| Request forwarding to the MCP server | `MCP_SERVER_URL: http://youtube-mcp:8765/mcp` in generators; tunnel `run` | **[EXTERNAL]** (performed by OpenAI client) + **[VERIFIED]** (target URL config) |
| Health/lifecycle integration | `depends_on: youtube-mcp: {condition: service_healthy}` (generators); tunnel healthcheck on its own /healthz | **[VERIFIED]** |
| Protection against direct unauthenticated Internet access | The generated stack publishes **no** host port for the MCP service (`expose`-only, internal network); the only in-bound path is the tunnel. Root `compose.yaml` publishes `127.0.0.1:8765:8765` (loopback, not Internet) | **[VERIFIED]** (current path is tunnel-only / loopback) |

**Which protections disappear when the tunnel is removed:** TLS termination, the
public HTTPS URL, and control-plane access control all disappear because they are
**[EXTERNAL]** to the tunnel product — they are not implemented in this repo and not in
the MCP server. The server itself provides **none** of those protections in-process
(no TLS, no auth). This directly motivates the Phase -1 "public static-credential /
public OAuth" gaps (§6, §7).

Note: the wrapper image is **planned, not published** — `docs/OPENAI_SECURE_MCP_TUNNEL.md`
states the public wrapper image `ghcr.io/dracoform/openai-mcp-tunnel:0.1.0` does not yet
exist, and `END_TO_END_VALIDATION.md` records a validated live ChatGPT path. **[VERIFIED:
repo docs; actual ChatGPT-path validation is recorded but the end-to-end claim is from
repo docs, not re-run here]**.

---

## 6. Direct / public exposure audit

What would happen if the MCP server container were exposed directly (e.g. publishing its
port publicly) today:

| Question | Finding |
|---|---|
| Reachable without authentication? | **Yes.** No auth anywhere in `server.py`/`core.py`; `error_result` only wraps tool errors (no 401/403). **[VERIFIED]** |
| Which interface binds? | `MCP_HOST` (code default `127.0.0.1`; image/generators set `0.0.0.0`). **[VERIFIED]** |
| Does Compose publish its port directly? | Root `compose.yaml:6-7` publishes `127.0.0.1:8765:8765` (loopback host port). Generated ChatGPT stacks use `expose`-only (internal network), no host port. **[VERIFIED]** |
| Environment flags governing exposure? | `MCP_HOST`/`MCP_PORT` control bind; `MCP_TRANSPORT` controls transport. No exposure/security flag exists. **[VERIFIED]** |
| TLS in-process? | **None.** Server runs plain HTTP (FastMCP/uvicorn); no cert config. **[VERIFIED]** |
| Rate limiting? | **None** in app. (Potential: none found.) **[VERIFIED]** |
| Request authentication? | **None.** **[VERIFIED]** |
| Origin/host checks? | **None.** **[VERIFIED]** |
| Reverse-proxy assumptions? | No reverse-proxy-specific code; stateless HTTP works behind one, but nothing enforces one. **[VERIFIED/INFER]** |
| Request-size/time limits? | Transcript output is bounded (`YOUTUBE_TRANSCRIPT_MAX_CHARS`/`HARD_MAX_CHARS`, defaults 60k/120k, core.py:651). yt-dlp subprocess has a 90s timeout (core.py:387). But there is **no HTTP-level request-body limit or per-client quota** at the exposure boundary. **[VERIFIED]** |

**Factual gap list for direct public exposure (all currently missing):**
1. TLS termination (HTTPS).
2. Request authentication (static credential or OAuth).
3. Rate limiting / per-IP or per-token quota.
4. Origin/host allow-listing (CSRF-ish / host header checks) if applicable at a gateway.
5. HTTP request-body size limits at the edge.
6. Audit/observability of who called which tool (currently none at the server; only
   process-level logging implied).

None of these are implemented and none block the current tunnel path; they are the
public-deployment gap.

---

## 7. Authentication / security boundary audit (per Phase -1 profile)

### A. Local / trusted (no auth)
- **What exists:** a fully functional local server (`MCP_HOST=127.0.0.1`, Streamable HTTP
  `/mcp`, `/healthz`). Root `compose.yaml` is already a single-service local compose at
  `127.0.0.1:8765`. Local LLMs (LM Studio/Qwen) or local LibreChat/DeepSeek can connect to
  a Streamable HTTP endpoint with no auth. **[VERIFIED]**
- **What's missing:** nothing functional. The only note is that `MCP_HOST` must stay
  loopback/LAN and never be the public default.
- **Owner of responsibility:** the MCP app itself is the boundary; no security layer
  needed. **[INFER]**
- **Insertion point:** none required.

### B. Public static-credential compatibility (HTTPS + static Bearer/API-key)
- **What exists:** the MCP core; no credential validation, no TLS.
- **What's missing:** TLS termination (HTTPS) and static Bearer/API-key validation; rate
  limiting.
- **Owner:** currently nothing. Phase -1 says this must serve LibreChat (stable
  `headers`/`apiKey`) and DeepSeek Harness (stable `headers`).
- **Possible insertion boundaries (Phase 1 candidate, not chosen):**
  - inside MCP application (middleware verifying `Authorization: Bearer`/`X-API-Key`),
  - middleware around the MCP app (same process, e.g. a Starlette middleware),
  - sidecar/gateway in front of the MCP container,
  - reverse proxy/ingress (nginx/traefik) doing header auth + TLS.
  Constraint from current architecture: the app is a plain FastMCP/uvicorn HTTP server, so
  both "inside app" (process middleware) and "gateway/ingress" are viable without core
  changes; the tunnel currently plays the "gateway" role.

### C. Public OAuth standard (HTTPS + MCP OAuth)
- **What exists:** nothing OAuth. `END_TO_END_VALIDATION.md` notes "this MCP intentionally
  exposes no app-level OAuth" — consistent with the tunnel path.
- **What's missing:** OAuth authorization-server metadata/discovery
  (`/.well-known/oauth-authorization-server`), authorization code flow, token validation,
  token refresh, per-user identity, and (optionally) Dynamic Client Registration.
- **Owner:** nothing in-repo; **[EXTERNAL]** for the tunnel path.
- **Possible insertion boundaries (Phase 1 candidate):** inside the MCP app (add OAuth
  provider), a gateway/sidecar acting as OAuth resource server, or an external
  identity/auth service. Claude + LibreChat support OAuth; DeepSeek Harness does not
  (so this must coexist with profile B).

### Architecture note
The current repository offers **no barrier between "tool-enabled HTTP server" and "public
service"** — the tunnel is that barrier today. For Phase 1, the cleanest framing is an
injectable exposure/security layer in front of (or wrapped around) the unchanged MCP app.
Where exactly (app middleware vs gateway/ingress vs sidecar) is explicitly **undecided**
in Phase 0.

---

## 8. Deployment topology audit

Current ChatGPT flow (from generated Compose + tunnel wrapper + docs):

```
ChatGPT / ChatGPT API
   |  (OpenAI control plane; TLS/public URL — EXTERNAL)
   v
OpenAI Secure MCP Tunnel (control plane + tunnel-client)
   |  (outbound-only on server host; no published host port)
   v
  MCP server container (FastMCP Streamable HTTP)
   |  env MCP_SERVER_URL=http://youtube-mcp:8765/mcp
   v
YouTubeService -> YouTube Data API / Atom feed / yt-dlp -> YouTube
```

More precisely from the generated stack (`generators/*`, `docs/assets/generator.js`):
`openai-tunnel` container (env: `CONTROL_PLANE_TUNNEL_ID`,
`CONTROL_PLANE_API_KEY`, `MCP_SERVER_URL=http://youtube-mcp:8765/mcp`) is on the internal
`youtube-mcp-internal` bridge network with `youtube-mcp` (which exposes `8765` internally
only). `depends_on: youtube-mcp: {condition: service_healthy}` ensures the MCP is healthy
before the tunnel starts. Neither container publishes a host port in the generated stack.

### Conceptual target topologies (responsibility boundaries only, not implementation)

**A. Existing ChatGPT mode (preserve):** exactly the above. Tunnel sidecar provides TLS +
public URL + control-plane access; MCP stays unauthenticated and internal.

**B. Trusted local mode:**
```
Local MCP client (LM Studio/Qwen, local LibreChat, local DeepSeek) 
   |  localhost/LAN Streamable HTTP, no auth
   v
YouTube MCP (unchanged core; MCP_HOST=127.0.0.1 or LAN)
   -> YouTube
```
Boundary: the machine/network trust boundary; no auth layer needed.

**C. Public static-credential mode:**
```
Remote MCP client (LibreChat/DeepSeek headers)
   |  HTTPS
   v
[HTTPS + static Bearer/API-key validation]  <-- NEW boundary (in-app middleware OR gateway/ingress)
   |  valid token forwarded on internal HTTP
   v
YouTube MCP (unchanged core)
   -> YouTube
```
The **new boundary** must provide: HTTPS, credential validation, and forwarding to MCP.
Placement undecided (Phase 1).

**D. Public OAuth mode:**
```
Remote MCP client (Claude/LibreChat OAuth)
   |  HTTPS
   v
[OAuth provider: metadata/discovery, authorization, token validation,
 refresh, per-user identity]  <-- NEW boundary (in-app OR gateway OR external identity svc)
   |  validated, possibly user-bound
   v
YouTube MCP (unchanged core)
   -> YouTube
```
The **new boundary** must provide: OAuth metadata/discovery, authorization, token
validation, forwarding/integration with MCP. Placement undecided (Phase 1); must coexist
with profile C for DeepSeek.

---

## 9. Generator audit

Three generators, all emitting an identical **two-service ChatGPT/tunnel** Compose stack
(equivalence enforced by tests):

- **Bash:** `generators/generate_docker-compose_for_ChatGPT_MCP.sh`
- **PowerShell:** `generators/generate_docker-compose_for_ChatGPT_MCP.ps1`
- **Web:** `docs/assets/generator.js` (+ `docs/index.html`), pure client-side, CSP `none`
  telemetry.

**Current decision flow & semantic inputs (shared shape across all three):**
1. MCP image tag
2. Tunnel image tag
3. YouTube Data API key (secret, masked+confirm)
4. yt-dlp enable (and consent `JA` when enabled)
5. Transcript languages
6. Transcript max chars (1,000–500,000)
7. **OpenAI Tunnel ID (mandatory)**
8. **OpenAI Runtime API key (mandatory, secret)**
9. Outbound proxy (optional)
10. Overwrite-confirmation

**Inputs shared by all deployment modes:** MCP image tag, YouTube API key, yt-dlp, languages,
max chars, proxy. These are pure YouTube/MCP config.

**Inputs specific to OpenAI Secure MCP Tunnel:** Tunnel image tag, Tunnel ID, Runtime API key
(the three that add `openai-tunnel` to the stack).

**Assumptions implying ChatGPT-only deployment:**
- The generators always include an `openai-tunnel` service and always prompt for tunnel
  credentials. There is currently **no path to emit a tunnel-less public/local stack**.
- Service/file/image naming embeds "ChatGPT".

**Where deployment-mode branching would logically occur:** after the shared YouTube/MCP
questions, before the tunnel-specific questions. A "deployment mode" selection (ChatGPT
tunnel / trusted local / public static-credential / public OAuth) would gate the
tunnel-specific prompts and the emitted services/security block.

**Does generator-equivalence testing give a safe foundation for future branching? Yes.**
`tests/test_generator_equivalence.py` runs both Bash and PowerShell with identical answers
and compares structurally; `tests/test_web_generator.cjs` runs a three-way
Bash/PowerShell/web structural equivalence, including security settings, networks,
proxy, and secrets. The three generators are linted to ASCII, secret-safe, and
equivalence-locked — a strong, safe base for refactoring output via a mode branch (the
tests would need extending, not replacing).

**Conceptual future structure (Phase 1, not implemented):**
1. common YouTube/MCP configuration (shared across modes);
2. deployment/exposure mode selection (tunnel vs local vs public-static vs OAuth);
3. mode-specific questions (e.g. tunnel ID/key only for tunnel mode; token for static
   mode; OAuth nothing-secret to ask beyond provider settings).

---

## 10. Docker / Compose audit

- **Number of services:**
  - Root `compose.yaml`: **1** (`youtube-mcp`) — loopback host port `127.0.0.1:8765:8765`,
    read_only, tmpfs `/tmp`, named volume `ytdlp-state`.
  - Generated stacks (Bash/PS/Web): **2** (`youtube-mcp` + `openai-tunnel`) on an internal
    `youtube-mcp-internal` bridge network; MCP uses `expose` (no host port), tunnel
    depends on `service_healthy`.
- **Network relationships:** generated stack — both containers on one internal bridge;
  tunnel reaches MCP only via `MCP_SERVER_URL=http://youtube-mcp:8765/mcp`. No published
  host ports in generated stacks.
- **Exposed/published ports:** root compose publishes `127.0.0.1:8765` (loopback-only).
  Generated stack: none published to host.
- **Tunnel dependency:** generated stack adds `openai-tunnel` that requires
  `CONTROL_PLANE_TUNNEL_ID`, `CONTROL_PLANE_API_KEY`, `MCP_SERVER_URL`. The MCP service
  does **not** depend on the tunnel; the tunnel depends on MCP being healthy.
- **Secrets into containers:** via `environment:` (generated stack writes the runtime API
  key and YouTube key in plaintext env in the YAML; docs warn the file is a secret). Tunnel
  wrapper reads them from env, never prints.
- **Health checks:** MCP Docker HEALTHCHECK hits `127.0.0.1:8765/healthz` (stdlib);
  tunnel HEALTHCHECK hits its loopback `/healthz`. Generated `depends_on` uses
  `service_healthy` for the MCP→tunnel ordering.
- **Restart policies:** `restart: unless-stopped` both services (generated); root compose
  same for MCP.
- **Can the MCP service run independently of the tunnel? Yes.** `compose.yaml` (root) is a
  standalone MCP service with no tunnel; Dockerfile ENV defaults include Streamable HTTP;
  `server.py` has no tunnel code. **[VERIFIED]**
- **Does tunnel removal produce a functional local MCP server?** Yes — the root compose is
  literally that (single `youtube-mcp` service). The generated stack's `youtube-mcp` block
  is also self-contained (removing the `openai-tunnel` block yields a working MCP). The
  only ChatGPT-specific bits in the generated MCP block are documentation/naming; its env
  (`MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, `YOUTUBE_*`) are all generic.
- **Hard-coded ChatGPT assumptions preventing standalone operation:** none functional.
  Only processor defaults and the missing security layer. Root compose proves standalone
  operation.

---

## 11. Configuration model audit

All configuration is **environment-based** (parsed in `Settings.from_env()` /
`UpdaterSettings.from_env()`, read at import/startup), plus **generator-produced**
environment values in the Compose YAML. No CLI config beyond the generators' `--output`
flag; nothing hard-coded beyond defaults.

| Setting (env) | Category |
|---|---|
| `YOUTUBE_API_KEY` | YouTube/tool behavior (official API path) |
| `YOUTUBE_ENABLE_YTDLP` | YouTube/tool behavior (fallback on/off) |
| `YOUTUBE_TRANSCRIPT_MAX_CHARS`, `YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS`, `YOUTUBE_DEFAULT_LANGUAGES` | YouTube/tool behavior (bounds, languages) |
| `YTDLP_*` (`AUTO_UPDATE`, `CHANNEL`, `INTERVAL`, `STATE_DIR`, `SMOKE`, `SMOKE_URL`) | YouTube/tool behavior (yt-dlp updater) |
| `MCP_TRANSPORT` | MCP server behavior (streamable-http/stdio/sse) |
| `MCP_HOST`, `MCP_PORT` | network/exposure (bind) |
| `CONTROL_PLANE_TUNNEL_ID`, `CONTROL_PLANE_API_KEY`, `MCP_SERVER_URL`, `HTTPS_PROXY`/`NO_PROXY` | Secure MCP Tunnel behavior (+ proxy) |
| (none) | authentication/security — **no settings exist** |
| (future) static token / OAuth provider settings | **future candidate only** (not implemented; semantics needed: a way to set a shared credential and enable OAuth discovery, but no names decided here) |

Current state: environment-based + generator-produced, with no security-related
configuration yet. Phase 1 will need to add a place (env or other) to express
"deployment/exposure mode" and credential/OAuth configuration, but this is deliberately
not designed in Phase 0.

---

## 12. Tool/API surface audit

Verified from `server.py` + `core.py` + `docs/INTERFACE.md`. All ten tools are marked
`READ_ONLY` annotations (server.py:35):

| Tool | Bounded behavior |
|---|---|
| `get_video` | metadata; provenance; no-key fallback |
| `list_caption_tracks` | caption discovery; bounded translation languages (50) |
| `get_video_transcript` | bounded transcripts; range `[start,end)`; **pagination** via opaque continuation tokens; hard cap `YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS` (default 120k), page 60k |
| `search_video_transcript` | deterministic textual substring search; `limit` 1..50; returns locator regions, not authoritative passages |
| `get_channel` | channel metadata |
| `get_recent_uploads` | channel feed; limit 1..50 |
| `search_videos` | video search; limit 1..25 |
| `get_playlist` | playlist + ordered items; bound at 500 items |
| `find_playlist_position` | playlist position by enumeration |
| `get_ytdlp_updater_status` | read-only updater diagnostics |

- **Bounded transcript:** yes — pagination + hard char cap (defensive against
  payload-abusive large results). `over` oversized single segment hard-truncated.
- **Pagination:** opaque `tr1.` tokens (core.py:176-222), no server-side session state →
  stateless pagination, ideal for multiple public clients.
- **Read-only guarantees:** all tools read-only (`readOnlyHint=True`,
  `destructiveHint=False`, idempotent). `core.py` only fetches YouTube; accepts only
  YouTube video/channel/playlist references (host allow-list `ALLOWED_YOUTUBE_HOSTS`),
  never arbitrary upstream URLs from tool input. **[VERIFIED + ARCHITECTURE.md]**
- **Resource-abuse characteristics relevant to public exposure:** each tool triggers a
  real YouTube/yt-dlp request; `yt-dlp` subprocess has a 90s timeout; transcript/search
  bounded by caps. There is **no per-client rate limit** and **no authentication** — a
  public endpoint without the security layer (§7) would be open to quota abuse (this is
  exactly why public profiles require auth + rate limiting).
- **Does multi-client deployment require any tool/schema change? No.** The tool surface and
  schemas are client-agnostic (the same MCP tool JSON schema works for Claude, LibreChat,
  DeepSeek, and local clients). Explicitly confirmed: no tool/API change is required for
  multi-client support.

---

## 13. Test coverage audit

| What it verifies | Tests | Notes |
|---|---|---|
| YouTube tool logic (parsing, callbacks, failure classification, provenance) | `tests/test_core.py` (471 L), `tests/test_failure_semantics.py` (474 L) | Core logic; no HTTP transport |
| Playlist tools + **FastMCP tool schema** (`_tool_manager`) | `tests/test_playlist.py:413-428` | Schema/annotations, not transport |
| Transcript search logic + schema | `tests/test_transcript_search.py` (266 L), schema test at :248 | Logic + schema |
| yt-dlp updater | `tests/test_updater.py` (555 L) | Updater, not server |
| Generator equivalence Bash↔PS | `tests/test_generator_equivalence.py` (207 L); explicit CI run | Structural YAML equivalence |
| Generator behavior (secret masking, proxy, ports, security opts, ASCII, permissions) | `tests/test_generators.py` (524 L) | Static + executable (bash) |
| Web generator (masking, validation, YAML, **three-way Bash/PS/Web equivalence**, CSP, no-telemetry) | `tests/test_web_generator.cjs` (395 L) | Runs with plain node |
| Docker HEALTHCHECK correctness (stdlib, /healthz, delayed-listener behavior) | `tests/test_generators.py:444-512` | Deterministic, no Docker |
| Tunnel wrapper entrypoint env validation, non-root, pins | `docker/openai-mcp-tunnel/tests/test_entrypoint.bats` (186 L) + `tests/test_generators.py:420+` | BATS + static |
| CI publish/verify for MCP + tunnel wrapper | `.github/workflows/publish-*` | Build/publish multi-arch |

**Gaps that Phase 1/2 would eventually need tests for** (no tests exist for these today):
- **MCP HTTP transport behavior** (actual Streamable HTTP `/mcp` request/response, protocol
  handshake, multiple simultaneous clients) — current tests only check the FastMCP
  `_tool_manager` schema, not a live `/mcp` exchange.
- **Server health endpoint over a real listener** — the Dockerfile healthcheck command is
  tested deterministically, but not the actual `/healthz` route end-to-end against the app.
- **Standalone (tunnel-less) server integration** — no test runs `mcp.run(transport=...)`
  standalone and issues tool calls.
- **Generator mode-branching** — once deployment-mode branching exists, equivalence tests
  must extend per-mode; today only the single tunnel+local output shape is covered.
- **Security/auth behavior** — no tests exist (nothing implemented): static credential
  validation, OAuth flow, rate limiting, origin checks. These are Phase 1 additions.

---

## 14. Architecture diagrams

### 14.1 Existing system (ChatGPT)

```
[ChatGPT / ChatGPT API]
        |
        |  public HTTPS URL + control-plane (EXTERNAL: OpenAI Secure MCP Tunnel product)
        v
[OpenAI Secure MCP Tunnel  (control plane + tunnel-client sidecar)]
        |  outbound-only from host; NO published host port; config:
        |    CONTROL_PLANE_TUNNEL_ID, CONTROL_PLANE_API_KEY,
        |    MCP_SERVER_URL=http://youtube-mcp:8765/mcp
        |  depends_on youtube-mcp: condition service_healthy
        v
[YouTube MCP container  (FastMCP Streamable HTTP /mcp, stateless, /healthz, read-only)]
        |
        |  MCP tools: get_video, list_caption_tracks, get_video_transcript,
        |             search_video_transcript, get_channel, get_recent_uploads,
        |             search_videos, get_playlist, find_playlist_position, updater_status
        v
[YouTubeService] --(source ladder)--> [YouTube Data API v3] / [Atom feed] / [yt-dlp]
                                     =============== YouTube ===============
```

Network boundary rendered in the generated stack: everything is on the internal
`youtube-mcp-internal` bridge; nothing is exposed to the host in the tunnel path.

### 14.2 Trusted local mode (Phase -1 profile A)

```
[Local MCP client (LM Studio/Qwen, local LibreChat, local DeepSeek)]
        |  Streamable HTTP, NO auth, localhost/LAN (MCP_HOST=127.0.0.1)
        v
[YouTube MCP (unchanged core, /mcp)] -> YouTube
```
Responsibility boundary: the trusted network/host. No security layer.

### 14.3 Public static-credential mode (Phase -1 profile B)

```
[Remote MCP client (LibreChat headers, DeepSeek headers)]
        |  HTTPS
        v
[NEW boundary: HTTPS + static Bearer/API-key validation + rate limit]
        |  (in-app middleware  OR  gateway/ingress/sidecar)   <-- Phase 1 decision
        |  forwards only after credential validation
        v
[YouTube MCP (unchanged core)] -> YouTube
```

### 14.4 Public OAuth mode (Phase -1 profile C)

```
[Remote MCP client (Claude OAuth, LibreChat OAuth)]
        |  HTTPS
        v
[NEW boundary: OAuth server (metadata/discovery, authorize, token validate,
 refresh, per-user identity)]   (in-app  OR  gateway  OR  external identity svc)
        |  <-- Phase 1 decision
        |  validated token -> forwarding
        v
[YouTube MCP (unchanged core)] -> YouTube
```

Notes: these are responsibility-boundary diagrams, not implementation choices. Profiles B
and C may share the HTTPS/TLS boundary and may share the MCP-core-facing forwarding path.

---

## 15. Gap matrix

Rows = capability; columns = Phase -1 profiles. Classification: Already exists /
Partially exists / Missing / External responsibility / Not required.

| Capability | Existing ChatGPT/Tunnel | Local/trusted | Public static-credential | Public OAuth |
|---|---|---|---|---|
| **Streamable HTTP `/mcp`** | Already exists (server.py:16, 221-229) | Already exists | Already exists | Already exists |
| **`/healthz`** | Already exists (server.py:21-32; Dockerfile:26) | Already exists | Already exists | Already exists |
| **Tool surface (read-only)** | Already exists (server.py:50-218) | Already exists | Already exists | Already exists |
| **Stateless / multi-client** | Already exists (stateless_http=True) | Already exists | Already exists | Already exists |
| **Standalone operation** | Partially (root compose standalone; generated stack tunnel-coupled) | Already exists (root compose) | Already exists (core) | Already exists (core) |
| **Public HTTPS / TLS** | External responsibility (tunnel) | Not required (local) | **Missing** (new boundary) | **Missing** (new boundary) |
| **Auth: no-auth** | Already exists (server unauthenticated) | Already exists (intended) | Not the mode | Not the mode |
| **Auth: static Bearer/API-key** | External (tunnel control-plane, not downstream) | Not required | **Missing** (new boundary) | Not required |
| **Auth: OAuth (discovery/authorize/validate/refresh)** | External (tunnel/control-plane; no in-app OAuth) | Not required | Not required | **Missing** (new boundary) |
| **Per-user identity** | Not present (share YouTube key; no user accounts) | Not required | Not required (shared credential) | **Missing** (per-user) |
| **Rate limiting** | External (assumed upstream) | Not required (trusted local) | **Missing** | **Missing** |
| **Request-size limits** | Partially exists (transcript caps core.py:651; no HTTP-level body limit) | Partially | **Missing** (edge) | **Missing** (edge) |
| **Origin/host checks** | External (tunnel) | Not required | **Missing** | **Missing** |
| **Generator mode selection** | Partially (single tunnel output only) | Missing (no local mode generator) | Missing | Missing |
| **Compose deployment profiles** | Partially (generated tunnel stack + root local compose) | Partially (root compose) | Missing | Missing |
| **Legacy SSE** | Not required (Phase -1: optional only; not implemented) | Not required | Not required | Not required |
| **Tunnel independence** | External (tunnel) | Already exists | Already exists functionally | Already exists functionally |
| **In-process secrets/creds** | External/Env (control API key via env, tunnel) | Not required | Missing (token source) | Missing (OAuth keys/refresh) |
| **Documentation (multi-client)** | Partially (ChatGPT docs exist; multi-client req doc exists) | Partially | Missing | Missing |
| **Tests: transport/standalone/auth** | Missing | Missing | Missing | Missing |

---

## 16. Explicit answers to the 13 critical questions

1. **Is the existing MCP server already generic enough for non-ChatGPT clients?**
   **Yes.** `src/youtube_mcp/` has no ChatGPT/OpenAI/tunnel coupling; it is a generic
   read-only Streamable HTTP YouTube MCP server. **[VERIFIED]**

2. **Does the existing server already provide the required Streamable HTTP `/mcp`
   endpoint?** **Yes.** `stateless_http=True`, `MCP_TRANSPORT=streamable-http` default,
   endpoint used as `.../mcp` throughout. **[VERIFIED]**

3. **Is any MCP-core rewrite required?** **No.** Transport, health, statelessness,
   read-only tools, pagination, and multi-client operation already meet the requirement.
   Gaps are exposure/security layers, not core.

4. **Where exactly is the current ChatGPT/OpenAI coupling?** Entirely outside the MCP app:
   (a) the Secure MCP Tunnel deployment (`docker/openai-mcp-tunnel/` + generated tunnel
   service), (b) the three generators (they emit a tunnel-enabled stack and prompt for
   tunnel credentials), and (c) documentation/naming/branding (README, repo/image names,
   docs). **[VERIFIED]**

5. **Can the MCP container run independently of the Secure MCP Tunnel today?** **Yes.**
   Root `compose.yaml` is a standalone MCP service; Dockerfile ENV defaults are
   self-sufficient; no tunnel code in `src/youtube_mcp/`. The generated stack's
   `youtube-mcp` block also stands alone if the tunnel block is removed. **[VERIFIED]**

6. **What security functionality disappears when the tunnel is removed?** TLS/public
   HTTPS, the public reachable URL, and control-plane access control — all **[EXTERNAL]**
   to the OpenAI Secure MCP Tunnel product, none implemented in-repo or in the MCP server.

7. **What is the smallest missing layer for public static-token deployment?** A single new
   **HTTPS edge + static Bearer/API-key validation boundary** (with rate limiting), placed
   as middleware in the MCP app or in a gateway/ingress/sidecar. Because the MCP core is
   already Streamable HTTP and bindable, this is the minimal addition. Placement undecided.

8. **What additional capability is required for OAuth deployment?** An **OAuth
   authorization-server layer**: `/.well-known/oauth-authorization-server` metadata/
   discovery, authorization-code flow, token validation, refresh, and per-user identity
   (optionally Dynamic Client Registration). This is a larger layer than static-credential
   auth and must coexist with profile B (DeepSeek has no OAuth).

9. **Do static auth and OAuth necessarily belong inside the MCP application?** **No.**
   Nothing in the current architecture forces them in-process. Both could be a gateway /
   sidecar / ingress / external identity service in front of the unchanged MCP. **[INFER]**
   Phase 1 must decide; Phase 0 sees no architectural constraint ruling out either.

10. **Does local no-auth require any code change?** **No.** The server runs unauthenticated
    on `MCP_HOST` today; trusted local mode works with zero code change (just keep
    bind/port/local-only and don't expose publicly).

11. **Do the existing YouTube tools or schemas require changes for multi-client support?**
    **No.** Tool names, JSON schemas, read-only annotations, bounded transcripts, and
    pagination are all client-agnostic and work for any MCP client.

12. **Which current generator assumptions must eventually become deployment-mode
    branches?** The tunnel-specific assumptions: (a) always emitting an `openai-tunnel`
    service, (b) always prompting for Tunnel ID + Runtime API key, and (c) ChatGPT-only
    naming/output. These become a "deployment mode" branch after the shared YouTube/MCP
    questions.

13. **Which implementation decisions must deliberately remain unresolved until Phase 1?**
    Exact OAuth implementation/library/provider; exact reverse proxy; whether auth lives
    in-process vs gateway; secret-storage strategy; final env var names; final Compose
    service names; final project/product name; final generator UI wording; whether legacy
    SSE is actually implemented; deployment provider/cloud architecture. (Explicitly
    listed in the task; none decided here.)

---

## 17. Phase 1 decision inputs

Established facts Phase 1 should build on (not decisions):

- **Preserve the MCP core unchanged** — it already meets the transport, tool, stateless,
  read-only requirements.
- **Generic vs tunnel-specific is an exposure concern, solved by an injection/seam**: the
  natural refactor is a security/exposure boundary in front of (or wrapped around) the
  unchanged FastMCP app.
- **Two complementary public modes must coexist** (Phase -1 conclusion): static-credential
  mode (LibreChat + DeepSeek; Claude conditional/Beta) and OAuth mode (Claude + LibreChat;
  not DeepSeek). There is **no single reliable auth mode** covering all three.
- **No-auth = local/trusted only.** `compose.yaml` already models the trusted-local
  deployment.
- **Tunnel path must be preserved** as a supported deployment (Phase -1 carried-forward
  requirement) — the tunnel is currently an outbound-only sidecar that supplies TLS/public
  URL/control-plane auth.
- **Generator refactor is low-risk** because three-way equivalence is already test-locked;
  the seamless seam is between shared-config and mode-specific blocks.
- **CI is a strong base**: publish workflows for MCP and tunnel wrapper, with hard pins and
  fail-closed checks.
- **Placement candidates for the auth layer (already present as options, not selected):**
  in-app FastMCP/Starlette middleware; a gateway/sidecar in front of the MCP container; an
  ingress/reverse proxy; an external identity service. The tunnel currently plays the
  "gateway" role, so a similar sidecar/gateway shape is architecturally familiar.

---

## 18. Unknowns requiring verification

- Whether the OpenAI **wrapper tunnel image and full ChatGPT path** are currently published
  and live: repo docs say the wrapper `0.1.0` is "planned, not published" and the E2E chart
  records a validated live ChatGPT path — this is repo-documentation-derived, **not
  re-verified** in this audit. **[EXTERNAL / verify before Phase 1 assumes it]**
- Exact FastMCP SDK version resolved at runtime (`mcp>=1.12,<2`, exact minor unpinned) —
  verify the `/mcp` path and `stateless_http`/`json_response` semantics against the
  resolved version during Phase 1.
- Whether any request currently reaches the MCP server unauthenticated from the Internet in
  real deployments (depends on how operators deploy the root `compose.yaml`, whose
  `127.0.0.1:8765` publish is loopback but could be changed).
- Whether the host has a reverse proxy / ingress in real deployments (nothing in-repo
  assumes one).
- Real payload/clients limits when multiple clients page long transcripts concurrently
  (Phase -1 unknowns 6-7): no stress test exists.

---

## 19. Conclusion

The existing repository is already in an excellent position for the multi-client goal.
**The critical architecture finding: the MCP server core is generic, stateless, read-only,
Streamable HTTP `/mcp` with a `/healthz` endpoint, and satisfies the Phase -1 transport
requirement as-is — it contains no ChatGPT/OpenAI coupling.** All ChatGPT coupling lives
in the Secure MCP Tunnel deployment, the generators, and documentation/naming.

**The MCP core does NOT require modification.** No tool/schema change is needed for
multi-client use; the tool surface is client-agnostic.

**Minimum missing capabilities for public multi-client deployment** are a security and
exposure layer, not core work: (1) HTTPS/TLS at the edge; (2) a static Bearer/API-key
validation boundary for LibreChat/DeepSeek; (3) an MCP-compatible OAuth layer for
Claude/LibreChat; (4) rate limiting and request-size limits for abuse protection; all
serving the unchanged MCP core, with a local no-auth profile requiring no code change.

**Unresolved Phase 1 decisions** concern *where* those layers live (in-process middleware
vs gateway/sidecar vs ingress vs external identity service), exact OAuth/TLS/library/
secret/Compose/config choices, and generator mode-branching UX — none decided here.

**Git status:** only `docs/MULTI_CLIENT_MCP_PHASE0_AUDIT.md` is new; the Phase -1
requirements document is untouched; no application code, Docker/Compose, generators, or
tests were modified; nothing committed or pushed; Phase 1 not started.
