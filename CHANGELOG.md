# Changelog

All notable changes to this project are documented here, grouped by release.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

Each release is tagged `v<major>.<minor>.<patch>` and publishes matching
container/image tags (see `README.md` "Versioned releases").

## [Unreleased]

- No changes beyond merged feature work.

## [0.2.0] - 2026-09-18

A major capability addition: the previously ChatGPT/OpenAI-tunnel-only server
can now be reached by arbitrary supported MCP clients over a public Remote MCP
endpoint with composable access methods.

### Added

- **Multi-client access methods.** Independently selectable and composable:
  local (loopback, no auth), OpenAI Secure MCP Tunnel (direct to the core),
  public static-Bearer edge, and public OAuth edge. Static and OAuth can share
  one public edge.
- **Static Bearer edge** (`youtube-mcp-edge`): public MCP edge with
  static-token authentication (`Authorization: Bearer ytsk_*`), constant-time
  token comparison, multi-token rotation, request-size limits, and fail-closed
  startup when credentials are missing.
- **Public HTTPS deployment (Stage 2):** edge can terminate TLS itself with an
  operator-provided cert/key, or sit behind an existing ingress. New
  `edge-public` Compose profile.
- **OAuth Resource Server support (Stage 3):** JWT/JWKS validation of
  access tokens against a separate Authorization Server, deterministic
  static-vs-OAuth dispatch, RFC 9728 protected-resource metadata endpoint, and
  401 `WWW-Authenticate` discovery. The resulting 401-challenge/discovery flow
  is aligned with the OAuth model that Claude / claude.ai Custom Connectors
  and OAuth-capable LibreChat expect, and their configs were validated against
  vendor documentation/source; a live hosted claude.ai connector E2E was **not**
  performed (account/public-endpoint gated).
- **Composable deployment generators (Stage 4):** a shared canonical input
  model (`generators/canonical_model.py`) drives the Bash, PowerShell, and Web
  generators; outputs are byte-for-byte equivalent across all three for the
  same choices. Non-interactive `--input` mode for automation.
- **Security hardening (Stage 5):** `cap_drop: ALL` on all Compose services,
  focused tests for interactive secret masking/confirmation and for
  Authorization-Server-outage fail-closed behavior, and documentation fixes.
- New optional configuration: `EDGE_*` (TLS, static auth, upstream) and
  `EDGE_OAUTH_*` (issuer, JWKS, audience, scopes, metadata) environment
  variables.
- New documentation: access-method matrix and working client configs for
  OpenAI, Claude, LibreChat, DeepSeek Harness, and local clients
  (`docs/MULTI_CLIENT_DEPLOYMENT_GUIDE.md`), plus stage docs 1–3 and the
  authorization-server ADR.
- Web generator UI redesigned as a capability form
  (`docs/index.html` / `docs/assets/generator.js`).

### Changed

- Both CLI generators reworked from a fixed tunnel-only flow to a
  capability-oriented flow.
- `docs/PUBLIC_MCP_EDGE_STAGE_1.md` responsibility notes updated to reflect
  that TLS (Stage 2) and OAuth (Stage 3) are now implemented.
- The `edge-public` profile's published host bind address is now explicitly
  configurable via `EDGE_PUBLIC_BIND_ADDRESS`, defaulting to loopback
  (`127.0.0.1`); a genuinely-public edge is a deliberate opt-in
  (`0.0.0.0` is never the silent default).

### Backward compatible

- The OpenAI Secure MCP Tunnel path is unchanged and remains independently
  wired directly to the core; tunnel-only deployments are unaffected.
- Local no-auth and the MCP core (`src/youtube_mcp/`) are unchanged.
- No existing tool schemas, transcript/search logic, or env vars removed.

### Security notes

- Public no-auth is invalid; a public edge requires static and/or OAuth auth.
- Never expose the core directly; only the edge is public.
- See the individual stage docs and the deployment guide for operational
  guidance (JWKS outage causes 401 until the edge refreshes — never fail-open).

### Known limitations

- Real claude.ai / live LibreChat / DeepSeek connector E2E remains
  account/instance-gated; config semantics are validated against vendor
  sources and the official MCP SDK client path.
- Certificate issuance / ACME automation is out of scope; operators provide a
  cert/key or use an external TLS-terminating ingress.
- Opaque-token introspection is not implemented (JWT/JWKS is the primary path).
- Renaming the project to reflect broader than "ChatGPT" scope is deferred to a
  separate future decision (see release prep notes).

[0.2.0]: https://github.com/Dracoform/chatgpt-youtube-mcp/releases/tag/v0.2.0