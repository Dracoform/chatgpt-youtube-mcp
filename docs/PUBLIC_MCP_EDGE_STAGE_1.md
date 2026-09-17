# Public Remote MCP edge — Stage 0/1 (static Bearer auth)

Status: Stage 0 (edge scaffold) + Stage 1 (static Bearer authentication) of the
multi-client MCP work. OAuth, TLS-termination implementation, and full public
deployment UX are deliberately out of scope here (see
`docs/MULTI_CLIENT_MCP_PHASE1_ARCHITECTURE.md`).

## What this is

A **separate public edge component** that sits in front of the private MCP core.
After authenticating a static Bearer credential, it transparently proxies
Streamable HTTP MCP traffic to the unchanged core.

```
Public/static client ─► MCP edge ─► MCP core (private)
                                    ▲
OpenAI tunnel ─────────► ──────────┘  (unchanged, independent path)
```

- The edge does **not** contain MCP tools or business logic; it only forwards.
- The MCP core, its `/mcp` endpoint, `/healthz`, tool schemas, transcript/search
  behavior, and the OpenAI Secure MCP Tunnel path are **unchanged** by this stage.
- The edge runs as a separate service (`youtube-mcp-edge`), built from the same
  image (which ships the `youtube-mcp-edge` console script).

## Responsibilities currently implemented

| Responsibility | Status |
|---|---|
| `GET /healthz` | Edge liveness (bounded, no secrets) |
| `/mcp` Streamable HTTP proxy to the core | Implemented (transparent forward, streams JSON / `text/event-stream`) |
| Static Bearer auth (`Authorization: Bearer <token>`) | Implemented (Stage 1) |
| Credential never logged / never forwarded upstream | Implemented |
| Request-size protection | Implemented (413 before any tool work) |
| Multiple tokens / rotation | Implemented (configure several; remove old after migration) |
| Constant-time token comparison | Implemented (SHA-256 digests + `hmac.compare_digest`) |
| Upstream failure treatment | Returns 502 |
| TLS termination | **Not** implemented in the edge (Phase 1: TLS MUST terminate before public traffic; pair with a reverse proxy/ingress or add later) |
| OAuth | **Not** implemented (Phase 2) |
| `X-API-Key` | **Not** enabled (optional future compatibility) |

## Configuration

All environment-based, minimal. There is an important distinction between the
edge process **bind address inside its container** and the **host-side address** that
Compose publishes externally:

- **Code default** (`EDGE_HOST` unset, e.g. running the edge directly for local
  dev): the edge binds **`127.0.0.1:8766`** — loopback only.
- **In Docker** (the `compose.yaml` `edge` profile): the container sets
  `EDGE_HOST: 0.0.0.0`, so the edge listens on **`0.0.0.0:8766` inside the
  container** — this is required for Docker NAT to reach it via the container's
  network interface. Compose then publishes the host-side address as
  **`127.0.0.1:8766:8766`**, i.e. reachable only from the host loopback, not the
  public interface. So in Compose the service is reachable through Docker while
  still never exposed publicly by default.

| Variable | Default | Notes |
|---|---|---|
| `EDGE_MCP_UPSTREAM_URL` | `http://127.0.0.1:8765/mcp` | Private core target. In Compose: `http://youtube-mcp:8765/mcp` |
| `EDGE_HOST` | `127.0.0.1` | Bind address (loopback default) |
| `EDGE_PORT` | `8766` | Bind port |
| `EDGE_STATIC_AUTH_ENABLED` | `true` | Fail-closed when `true` and no tokens are set |
| `EDGE_STATIC_TOKENS` | *(none)* | Newline- and/or comma-separated static Bearer credentials. **Keep secret.** Multiple tokens → rotation |
| `EDGE_MAX_REQUEST_BYTES` | `2097152` (2 MiB) | Request-body cap; over ⇒ `413` |
| `EDGE_UPSTREAM_TIMEOUT_SECONDS` | `120` | Upstream request timeout |

## Static Bearer validation

- Only the canonical `Authorization: Bearer <token>` form is accepted.
- The presented token is SHA-256-hashed and compared constant-time against the
  precomputed digest of every configured token (`hmac.compare_digest`).
- **Multiple tokens** are accepted, which is how rotation works: add the new
  token, wait for callers to migrate, remove the old token.
- Missing / invalid / malformed credential ⇒ `401` with
  `WWW-Authenticate: Bearer realm="youtube-mcp"`. The response does not reveal
  whether the header or the token was wrong.
- The raw token is **never logged and never forwarded** to the private core.
- No database and no per-user identity are involved.

## Running locally

```bash
# 1. Start the private core (unchanged behavior):
docker compose up -d youtube-mcp

# 2. Set a static token in .env (gitignored), e.g.
#    EDGE_STATIC_TOKENS=ytsk_your-long-secret-token

# 3. Run the edge via the Compose profile. The container binds 0.0.0.0:8766
#    (reachable through Docker NAT); Compose publishes 127.0.0.1:8766 on the
#    host, so it stays loopback-only externally:
docker compose --profile edge up -d youtube-mcp-edge

# 4. Call the core THROUGH the edge with the token:
curl -i http://127.0.0.1:8766/mcp \
  -H 'Authorization: Bearer ytsk_your-long-secret-token' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

To run unit tests:

```bash
uv run python -m unittest tests.test_static_auth tests.test_edge -v
uv run python -m unittest discover -s tests -v   # full suite
```

## Security notes

- The edge is the intended **future public exposure point**, but Stage 0/1 does
  not implement TLS. Phase 1 requires TLS to be terminated before public traffic
  reaches the protected MCP resource — either at the edge (later stage) or by an
  existing reverse proxy/ingress in front of it.
- The edge never emits the credential; logs record method, path, status, outcome,
  client, and elapsed time only.
- Typical token format: use a long, high-entropy random value, optionally with a
  `ytsk_` prefix to leave an unambiguous namespace for future OAuth access-token
  coexistence (see Phase 1 §9).

## Out of scope (later phases)

- OAuth / Authorization Server (Phase 2).
- TLS termination / certificate automation (Phase 2).
- `X-API-Key` (optional future compatibility).
- Full Compose/generator capability branching (Phase 2).