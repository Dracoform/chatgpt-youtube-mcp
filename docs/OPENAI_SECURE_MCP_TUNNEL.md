# OpenAI Secure MCP Tunnel: wrapper image and deployment

Status: the wrapper implementation is prepared and tested without production
credentials. The public image and complete ChatGPT-to-YouTube path are not
yet claimed as validated.

## The three layers, clearly separated

| Layer | What it is | Status |
| ----- | ---------- | ------ |
| Official OpenAI image | `ghcr.io/openai/tunnel-client` — OpenAI's supported multi-arch image for their `tunnel-client`, Apache-2.0. Pinned by digest `sha256:41d7c85d…` (release `v0.0.14`). | Published by OpenAI; verified in the GHCR registry 2026-09-05. |
| Our thin wrapper | `docker/openai-mcp-tunnel/` in this repository: adds a non-root user (10001), an entrypoint that validates the environment contract, a loopback healthcheck, and OCI labels. ~2 files on top of the official image. | Implemented and unit-tested; build verified in CI. |
| Public image address | `ghcr.io/dracoform/openai-mcp-tunnel:0.1.0` | **Planned, not published.** Do not reference it as existing until an anonymous `docker pull` succeeds. |

## Environment-variable contract

| Variable | Required | Meaning |
| -------- | -------- | ------- |
| `CONTROL_PLANE_TUNNEL_ID` | yes | Official OpenAI variable for the tunnel ID. Format: `tunnel_` + 32 lowercase hex characters. |
| `OPENAI_TUNNEL_ID` | no | **Deprecated compatibility alias** accepted by our entrypoint; mapped to `CONTROL_PLANE_TUNNEL_ID`. Setting both with different values is a fatal configuration error (exit 78). New deployments should use the official name only. |
| `CONTROL_PLANE_API_KEY` | yes | OpenAI Runtime API key (Runtime API keys page). Passed by environment variable only; never placed in command-line arguments; never printed. |
| `MCP_SERVER_URL` | yes | Internal Streamable HTTP MCP target, e.g. `http://youtube-mcp:8765/mcp`. |
| `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | optional | Standard outbound-proxy semantics; the tunnel client applies them when no explicit proxy flag is set. The generators set `NO_PROXY=youtube-mcp,localhost,127.0.0.1` when a proxy is configured. |
| `LOG_LEVEL` / `LOG_FORMAT` | optional | `debug|info|warn` and `struct-text|json`; defaults `info`/`json`. |

The generated Portainer stack uses only official variable names.

## Validation rules (container entrypoint)

Configuration errors exit with code 78 (EX_CONFIG) and a single stderr line;
values are never echoed:

- tunnel ID missing, or both names set with different values;
- tunnel ID does not match `^tunnel_[0-9a-f]{32}$`;
- `CONTROL_PLANE_API_KEY` unset, empty, whitespace-only, or containing CR/LF;
- `MCP_SERVER_URL` missing or not an absolute `http(s)` URL;
- `LOG_LEVEL`/`LOG_FORMAT` outside their enumerations.

## Container lifecycle and security

- Runs as UID/GID 10001 (non-root), no shell package installs at runtime.
- `read_only: true` root filesystem; only `/tmp` is writable (tmpfs, 16 MB).
- `cap_drop: [ALL]`, `no-new-privileges:true`.
- No published host ports: the tunnel is outbound-only; the health listener
  stays on loopback inside the container.
- `stop_grace_period: 30s` for clean SIGTERM shutdown; the entrypoint `exec`s
  the client so it is PID 1 and receives signals directly.
- No config volume: the env-only client keeps no state across restarts; the
  `tunnel-config:/config` volume of earlier drafts was removed deliberately.

## Container health — what it proves and what it does not

The image healthcheck polls `http://127.0.0.1:8080/healthz` inside the
container. That verifies **process health** only (the client's local admin
server is up).

It does **not** verify:

- control-plane authentication or the tunnel connection (`/readyz` is the
  client's readiness surface; its exact semantics are captured during the
  first live test — tracked as unresolved);
- reachability of the MCP target;
- MCP initialization or tool discovery.

A green Docker health status therefore must not be read as end-to-end
readiness. End-to-end validation requires the credentialed test described in
`docs/OPENAI_MCP_TUNNEL_IMAGE_RESEARCH.md` §11.3 (dedicated test tunnel and
runtime key, not production credentials).

## Build verification

CI (`.github/workflows/publish-openai-mcp-tunnel.yml`) verifies, on every
push/PR touching the wrapper:

1. shell syntax and the no-credential BATS suite;
2. completeness and format of every pin in `versions.env`;
3. that the pinned digest is still the registry digest of the pinned tag
   (via the `Docker-Content-Digest` response header — fail closed on
   mismatch or missing header);
4. that the wrapper builds for `linux/amd64` and `linux/arm64` — without
   publishing anything.

Publication happens **only** via manual `workflow_dispatch` with a valid
semantic version, producing `0.1.0`-style, `0.1`-style, and `latest` tags
plus provenance and SBOM attestations.

## What is not yet validated

- The public image does not exist yet (`ghcr.io/dracoform/openai-mcp-tunnel:0.1.0`
  cannot be pulled until publication is approved and executed).
- No container has run against the real OpenAI control plane: behavior under
  non-root with `cap_drop: ALL`, `/healthz` vs `/readyz` semantics, and the
  full ChatGPT-to-YouTube path remain to be verified in the credentialed
  integration test.
