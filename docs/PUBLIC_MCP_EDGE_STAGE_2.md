# Public Remote MCP edge — Stage 2 (public HTTPS static-auth deployment)

Status: Stage 2 of the multi-client MCP work. This stage turns the Stage 0/1
static-auth edge into a **supported public HTTPS deployment path** and verifies
it with real client tooling.

> **OAuth was added in a later stage.** This stage delivers static
> `Authorization: Bearer *** authentication only. OAuth Resource-Server support
> (the Claude / claude.ai Custom Connector path) landed in Stage 3 — see
> `docs/PUBLIC_MCP_EDGE_STAGE_3.md`. For a new deployment, prefer the
> capability-oriented generators and `docs/MULTI_CLIENT_DEPLOYMENT_GUIDE.md`.

## Topology

```
LibreChat / DeepSeek Harness
        │
        │ HTTPS + Authorization: Bearer <token>   (Streamable HTTP)
        ▼
Public MCP Edge (TLS termination + static auth)  ──► /mcp, /healthz
        │  private HTTP
        ▼
YouTube MCP Core  (private, loopback-only, unchanged)

OpenAI Secure MCP Tunnel ──────────► same YouTube MCP Core (unchanged, direct)
```

- The MCP core is **never directly public**. Only the edge publishes a host port.
- The tunnel and the public edge are **independent** paths into the same core and
  can be enabled simultaneously; neither reroutes through the other.
- The tunnel, core, `/mcp`, tool schemas, transcript/search logic and
  generators are **unchanged** by this stage.

## TLS / ingress model chosen

Smallest practical self-hosted HTTPS approach for this project:

**The edge can terminate TLS itself using operator-provided certificate/key
files (no ACME / certificate-issuance automation in this stage).** This keeps the
edge as the single public security boundary and works on any host without extra
software.

Two supported deployment shapes:

1. **Default packaged path — edge terminates TLS.** Set `EDGE_TLS_CERT_FILE` and
   `EDGE_TLS_KEY_FILE` to a PEM certificate (chain) and private key. The edge
   then serves HTTPS on its configured port.
2. **Existing-ingress path — external proxy terminates TLS.** If you already run
   Caddy / nginx / Traefik, leave `EDGE_TLS_*` unset and forward the ingress to
   the plain-HTTP edge (loopback). This requires **no** edge change: without TLS
   env vars the edge serves plain HTTP, intended for loopback/trusted or
   ingress-fronted operation.

Rules (fail closed):
- Setting **exactly one** of `EDGE_TLS_CERT_FILE`/`EDGE_TLS_KEY_FILE` is a
  configuration error → refused to start.
- If a configured cert/key path does not exist or is unreadable → refused to
  start.
- The private key must be readable by the edge container's runtime user
  (uid 65532, non-root). Ensure your mounted key is 0644 or owned by that uid.

## Public Compose profile

A new opt-in profile, `edge-public`, provides the public HTTPS deployment:

```bash
# Put a certificate chain and private key in ./edge-certs/, e.g.
#   ./edge-certs/tls.crt   (PEM cert chain)
#   ./edge-certs/tls.key   (PEM private key, readable by uid 65532)

# Optionally set in .env (all gitignored):
#   EDGE_STATIC_TOKENS=ytsk_your-long-secret-token   # REQUIRED
#   EDGE_PUBLIC_HTTPS_PORT=8443                      # host HTTPS port
#   EDGE_PUBLIC_PORT=8766                            # container HTTPS port
#   EDGE_TLS_DIR=./edge-certs

docker compose --profile edge-public up -d --build
```

Behavior of the public profile:
- Core stays **private**: `127.0.0.1:8765:8765` (loopback only).
- The public edge binds `0.0.0.0` inside its container (required for Docker NAT)
  and Compose publishes only **host loopback** `127.0.0.1:<PUBLIC_HTTPS_PORT>`
  by default. **To expose beyond the host** you must deliberately widen that
  publish mapping — safe-by-default.
- `EDGE_STATIC_TOKENS` defaults to empty and `EDGE_STATIC_AUTH_ENABLED` defaults
  to `true`: if an operator omits the token, the edge **refuses to start**
  (fail closed) — it can never silently fall back to no-auth.
- The edge's HTTPS healthcheck probes its own TLS listener with an unverified
  context (self-signed/operator certs are fine for liveness).

| Variable | Default | Notes |
|---|---|---|
| `EDGE_PUBLIC_HTTPS_PORT` | `8443` | Host-side published HTTPS port |
| `EDGE_PUBLIC_PORT` | `8766` | Container edge HTTPS port |
| `EDGE_TLS_DIR` | `./edge-certs` | Host dir mounted read-only at `/certs` |
| `EDGE_TLS_CERT_FILE` | `/certs/tls.crt` | PEM cert chain path (in container) |
| `EDGE_TLS_KEY_FILE` | `/certs/tls.key` | PEM key path (in container) |
| `EDGE_STATIC_AUTH_ENABLED` | `true` | Fail-closed when `true` and no tokens |
| `EDGE_STATIC_TOKENS` | *(none)* | Static Bearer credential(s). **Keep secret** |

Local loopback-only mode remains unchanged: `docker compose up -d youtube-mcp`
plus `docker compose --profile edge up -d youtube-mcp-edge` serves plain-HTTP
loopback as in Stage 0/1.

## Connecting clients

### LibreChat

`librechat.yaml` — static Bearer to `https://your-host:8443/mcp`:

```yaml
mcpServers:
  youtube-mcp:
    type: streamable-http
    url: https://your-host:8443/mcp
    headers:
      Authorization: 'Bearer ${LIBRECHAT_YOUTUBE_TOKEN}'
    requiresOAuth: false
```

- `type: streamable-http` is the documented remote-server type.
- `${LIBRECHAT_YOUTUBE_TOKEN}` is resolved from the LibreChat server environment.
- `requiresOAuth: false` skips LibreChat's OAuth auto-detection probe, so the
  static header authenticates normally instead of triggering an OAuth flow.
- Restart LibreChat after editing `librechat.yaml`.
- HTTPS is required on the endpoint if LibreChat enforces it / for public use.

### DeepSeek Harness

`cordis.yml` plugin entry (`@deepseek-ai/dsh-mcp-client`), static Bearer:

```yaml
- id: mcp-youtube
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: youtube
    transport: streamable-http
    url: https://your-host:8443/mcp
    headers:
      Authorization: !!js '`Bearer ${process.env.MCP_TOKEN}`'
```

- `transport: streamable-http` selects the Streamable HTTP transport.
- The `Authorization` header is set via the Cordis `!!js` load-time tag
  (`process.env.MCP_TOKEN`); the token is resolved once at `cordis.yml` load, so
  rotation requires a reload.
- DeepSeek Harness sends the configured headers on every request and has **no
  OAuth client** (no discovery / PKCE / refresh / 401-challenge handling), so
  static Bearer is the correct authentication mode for it.
- Tool names become namespaced as `mcp__youtube__<tool>` (serverName is
  local-only).

## Coexistence with the OpenAI Secure MCP Tunnel

- The tunnel connects **directly** to the private core on `127.0.0.1:8765`
  (unchanged). The public edge connects independently to the same core.
- Both can be enabled at once; they do not conflict and do not share auth state —
  the tunnel uses its own OpenAI transport/credentials, the edge uses its own
  static Bearer tokens.
- There is no auth leakage between the two paths, and the edge does not see or
  store the tunnel's credentials (and vice-versa).
- An edge failure does not affect tunnel-only operation, and a tunnel failure
  does not affect the edge path.
- The public edge never reroutes the tunnel; the tunnel always targets the core
  directly.

## Verification performed (2026-09-17)

Real-container E2E against the `edge-public` profile:
- Both core and public edge healthy; core published `127.0.0.1:8765` only; public
  edge published `127.0.0.1:8443` only (loopback).
- HTTPS `/healthz` → 200; missing Bearer → 401; invalid Bearer → 401.
- A real MCP client session over HTTPS (`mcp` SDK, Streamable HTTP + static
  Bearer header — the same transport+header path LibreChat and DeepSeek Harness
  use): protocol negotiated (2025-11-25), `tools/list` → 10 tools, and a live
  `get_video_transcript` call returned **a real YouTube transcript** through
  edge → core → YouTube.
- Coexistence: direct core loopback (`tools/list` → 10 tools) still worked while
  the public edge was up; no port collision; no token/`authorization` value in
  core or edge logs.

## Security notes

- Core is never public; only the edge publishes a host port (loopback-gated).
- Static Bearer validation happens at the edge before forwarding; the credential
  is never forwarded to the core and never logged.
- Request-size limit stays active (over ⇒ 413).
- A public profile without configured credentials fails closed.
- TLS: both-or-neither cert/key; missing/unreadable files refused; the edge is
  the outer boundary and does not trust client-sent `X-Forwarded-*` / `Host`.
- See `docs/MULTI_CLIENT_MCP_PHASE1_ARCHITECTURE.md` for the wider OAuth /
  capability matrix roadmap.

## Later stages (superseding the Stage-2 scope notes)

- OAuth / Authorization Server integration (Claude-standard path): implemented
  as of Stage 3 (`docs/PUBLIC_MCP_EDGE_STAGE_3.md`).
- Full Phase 1 capability matrix and generator capability branching:
  implemented as of Stage 4 (`docs/MULTI_CLIENT_DEPLOYMENT_GUIDE.md`).
- Certificate issuance / ACME automation: still out of scope (operator provides
  cert/key, or an external ingress terminates TLS).
- `X-API-Key`: still an optional, not-enabled future compatibility item.