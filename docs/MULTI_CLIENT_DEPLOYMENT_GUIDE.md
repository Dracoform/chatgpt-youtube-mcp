# Multi-client YouTube MCP deployment guide (Stage 4)

Branch: `feat/multi-llm-public-mcp`
Status: Stage 4 — capability-oriented generator and deployable configuration.

This guide describes the **composable access-method model** behind the
generators, the resulting deployment topologies, and working client
configurations for every supported frontend. The same capability model powers
the Bash, PowerShell, and Web generators; all three produce **identical** Compose
YAML for the same choices (verified by
`tests/test_generator_equivalence.py` against `generators/canonical_model.py`,
the single source of truth).

## Access methods (independently selectable, composable)

| Access method | What it adds to the stack | Intended clients |
|---|---|---|
| `local` | Core reachable on host **loopback** `127.0.0.1:8765` (loopback-safe, not LAN/public) | Generic local MCP clients, local LLM tools |
| `tunnel` | OpenAI Secure MCP Tunnel container, wired **directly to the core** (`MCP_SERVER_URL=http://youtube-mcp:8765/mcp`) | ChatGPT / OpenAI |
| `static` | Public edge with static Bearer auth (`Authorization: Bearer ytsk_...`) | DeepSeek Harness, LibreChat (static), custom |
| `oauth` | Public edge with OAuth access-token validation (JWT/JWKS) | Claude / claude.ai, LibreChat (OAuth) |
| `static` + `oauth` | **ONE** edge handling both (one public `/mcp` endpoint) | Claude + static clients together |

Any subset may be enabled. **Tunnel-only stays short and backward-friendly**
(it does not ask about the edge or OAuth). The MCP core is **always present** and
is **never published on a public interface** — only the edge (when selected)
publishes a host port, loopback-gated by default.

## Canonical input model

The generators share one canonical semantic input (JSON):

```json
{
  "mcp":    {"tag":"latest", "youtube_api_key":"", "enable_ytdlp":true,
             "languages":"de,en", "max_chars":"60000"},
  "access": ["tunnel","static","oauth"],
  "tunnel": {"tag":"0.1.0", "tunnel_id":"tunnel_...", "runtime_api_key":"sk-...",
             "http_proxy":""},
  "edge":   {"enabled":true, "auth_modes":["static","oauth"], "tls":"edge" | "ingress",
             "public_port":"8443", "cert_file":"", "key_file":"",
             "static_token_prefix":"ytsk_", "static_tokens":["ytsk_..."],
             "oauth":{"issuer":"...", "resource":"...", "audience":"",
                      "required_scope":"", "jwks_url":"",
                      "authorization_servers":"", "scopes_supported":""}}
}
```

Non-interactive rendering (used by tests, CI, and automation):

```bash
python -m generators.canonical_model <in.json> [--out stack.yml]

# or through the generators' --input mode:
./generators/generate_docker-compose_for_ChatGPT_MCP.sh --input in.json --output stack.yml
./generators/generate_docker-compose_for_ChatGPT_MCP.ps1 -InputPath in.json -OutputPath stack.yml
```

## Capability validation (rejected combinations)

The model **fails closed** and the generators refuse to emit an invalid or
insecure stack:

| Combination | Result |
|---|---|
| public edge with neither static nor OAuth | **rejected** (never a public no-auth edge) |
| OAuth without issuer + resource | **rejected** |
| TLS `edge` with only cert or only key | **rejected** (half-configured TLS) |
| static + OAuth on one edge, static token lacking the `ytsk_` namespace prefix | **rejected** (namespace ambiguity) |
| static access with no static token | **rejected** |
| TLS `ingress` but cert/key provided | **rejected** |
| core published to a public interface | never generated (invariant) |

## TLS / ingress

- `tls: edge` — the edge terminates TLS itself. The generated stack mounts
  `./edge-certs:/certs:ro` (read-only); place `tls.crt` (PEM chain) and
  `tls.key` (PEM key, readable by the container uid 65532) in `./edge-certs`.
  The public HTTPS port is published **loopback-gated** by default
  (`127.0.0.1:<port>:8766`); widen the port mapping only to expose publicly.
- `tls: ingress` — an external Caddy/nginx/Traefik terminates TLS and forwards
  to the plain-HTTP edge. No cert/key in the stack. (OAuth/Claude clients
  require HTTPS in front of the resource, so an ingress or edge TLS is
  mandatory for OAuth access.)

## Client configurations

### OpenAI tunnel (ChatGPT / OpenAI)

No client config needed beyond the tunnel itself. Enable `tunnel` in the
generator; the tunnel container connects **directly** to the core. In
ChatGPT, use the tunnel's assigned Secure MCP endpoint URL.

### Claude / claude.ai — OAuth

Enable `oauth` (edge TLS required). Claude's custom Remote MCP connector
discovers the OAuth flow from the edge (`/.well-known/oauth-protected-resource`
+ the `401` / `WWW-Authenticate` handshake). Point the connector at the public
`https://<host>:<port>/mcp`.

- The edge advertises `authorization_servers` (the configured OAuth issuer) and
  `scopes_supported`; configure the Authorization Server (e.g. Keycloak) as the
  issuer.
- `required_scope`/`audience` are mapped to the edge config; a client must
  present a valid JWT from the configured issuer.
- **Live-tested:** edge OAuth provisioning + JWT accept/reject were verified in
  Stage 3 against a real Keycloak instance. A full hosted `claude.ai` connector
  session still depends on a live account/public endpoint (marked pending).

### LibreChat — static or OAuth

**Static** (recommended, simplest):

```yaml
# librechat.yaml
mcpServers:
  youtube-mcp:
    type: streamable-http
    url: https://<host>:<port>/mcp
    headers:
      Authorization: 'Bearer ${LIBRECHAT_YOUTUBE_TOKEN}'
    requiresOAuth: false
```

**OAuth** (when `oauth` is enabled): LibreChat's OAuth flow (authorization-code
+ PKCE) discovers the edge metadata and the Authorization Server. Its static
`apiKey`/`headers` path skips OAuth auto-detection, so static and OAuth clients
can share the same endpoint.

- **Live-tested:** LibreChat's static config semantics were verified in Stage 2.
  A live LibreChat instance (static or OAuth) against the public edge is marked
  pending on this host.

### DeepSeek Harness — static

DeepSeek Harness has **no OAuth client** and sends configured headers on every
request, so static Bearer is the correct mode:

```yaml
# cordis.yml
- id: mcp-youtube
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: youtube
    transport: streamable-http
    url: https://<host>:<port>/mcp
    headers:
      Authorization: !!js '`Bearer ${process.env.MCP_TOKEN}`'
```

(Requires `static` access; the token must start with the reserved `ytsk_`
prefix if OAuth is also enabled on the same edge.)

- **Live-tested:** the static Bearer path through the public edge was verified
  end-to-end in Stage 2; the config above was validated against the official
  `deepseek-ai/deepseek-harness` source in Stage 2.5. A live Harness session is
  marked pending.

### Local generic MCP client

Enable `local`. The core is reachable on the host loopback:

```
http://127.0.0.1:8765/mcp
```

Point any Streamable-HTTP MCP client at that URL (no auth). This is loopback
only by default — do not expose it to the LAN/public (that is what the public
static/OAuth edge is for).

## Coexistence and the OpenAI tunnel

The tunnel always talks directly to the core (never through the edge), so it
works alongside static/OAuth local access. The core stays private; there are no
port collisions (core `expose` only, or loopback 8765 for `local`; the edge uses
its own public port). Tunnel failure does not affect the edge path and vice
versa.

## How the generators choose questions

The interactive flow is:
1. common MCP / YouTube settings (image tag, languages, max chars, optional API
   key, yt-dlp consent);
2. **select access methods**;
3. ask **only** the questions those methods require (tunnel questions only if
   tunnel selected; edge/TLS/static/OAuth questions only if a public edge is
   selected — tunnel-only users are never asked about OAuth);
4. validate + render (`--input <json>` skips the prompts entirely and delegates
   to `canonical_model.py`).

## Verification status

- Cross-generator equivalence (Bash, PowerShell, Web vs the canonical model) for
  `tunnel-only`, `static-public`, `oauth-public`, `static-oauth`,
  `tunnel-static-oauth`, `local`: **byte-for-byte PASS** (real execution).
- Real generated-stack Docker E2E (tunnel+static+oauth, edge TLS): core healthy,
  edge healthy and serving HTTPS, **static `ytsk_` token and a real Keycloak
  OAuth token both reached the same core (`tools/list`, 10 tools)**; invalid
  tokens/missing header rejected; core not published publicly; no port
  collisions.
- Claude/LibreChat/DeepSeek live-client connectors remain account/instance-gated
  (details above); config semantics validated against vendor sources.