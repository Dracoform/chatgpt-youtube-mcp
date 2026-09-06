# Research: OpenAI Secure MCP Tunnel Container (`openai-mcp-tunnel` 0.1.0)

Status: research + implementation prepared. The wrapper image
(`docker/openai-mcp-tunnel/`) is implemented and tested **without production
credentials**. The public image `ghcr.io/dracoform/openai-mcp-tunnel:0.1.0`
is **planned, not published** — no anonymous pull succeeds yet, and the
complete ChatGPT-to-YouTube path is not yet claimed as validated. See
`docs/OPENAI_SECURE_MCP_TUNNEL.md` for the implementation status.

Research date: 2026-09-05. All primary facts below were verified on that date
against live sources: the official repository docs (`main` branch, which
carries the v0.0.14 release documentation), the v0.0.14 GitHub release asset
list, `SHA256SUMS.txt`, and the GHCR registry manifest/config API.

## 1. Executive summary

**The tunnel image should be a thin wrapper FROM the official OpenAI image,
pinned by immutable digest.** The headline research outcome changes the
original plan: OpenAI publishes a supported multi-arch container image at
`ghcr.io/openai/tunnel-client` (linux/amd64 + linux/arm64, verified in the
registry for v0.0.14), Apache-2.0 licensed, with OCI labels, SBOM attestation,
and signed provenance. There is no need to maintain our own binary-copying
image, and no need to decide redistribution questions ourselves beyond what
`FROM <Apache-2.0 image>` already requires (keep the upstream LICENSE and
notice files).

Verified key facts that reshape the design:

1. The official env-var name for the tunnel ID is
   **`CONTROL_PLANE_TUNNEL_ID`**, not `OPENAI_TUNNEL_ID`. The generators'
   contract `OPENAI_TUNNEL_ID` must be adapted in a thin entrypoint (rename
   into `CONTROL_PLANE_TUNNEL_ID`) or the generators must be migrated to the
   official name. `CONTROL_PLANE_API_KEY` and `MCP_SERVER_URL` are correct as
   the official client spells them (this was lucky, not designed).
2. The Runtime API key is supplied by **environment variable** (preferred:
   `CONTROL_PLANE_API_KEY`, with `OPENAI_API_KEY` as a fallback that we do
   not use), a `file:/run/secrets/...` reference, or
   `--control-plane.api-key=env:VAR/file:...`. It never has to appear in argv,
   and the client's own support-bundle export redacts secrets. No profile
   file, stdin hand-off, or credential store is required.
3. The config surface for our use case is exactly three env vars plus
   optional logging/proxy/health settings. A config file is *not* required:
   the official Docker example runs with env vars alone. Therefore the
   proposed named volume `tunnel-config:/config` is **not needed** and should
   be dropped: it would preserve no state the client requires, adds a
   writable surface, and would persist nothing but what an entrypoint could
   regenerate deterministically each start.
4. The health surface is real and documented: `/healthz`, `/readyz`,
   `/metrics`, `/ui` on `HEALTH_LISTEN_ADDR` (default `127.0.0.1:8080`). A
   container healthcheck should bind the listener to loopback inside the
   container and probe `/healthz` (liveness) and optionally `/readyz`
   (readiness). This is non-invasive: it triggers no control-plane traffic.
5. Signal handling is the client's own concern (it is documented to propagate
   exits and remove PID files on shutdown; upstream ships a runtime
   container-compatibility smoke that exercises SIGTERM). Our wrapper must
   use `exec` so the client is PID 1 and receives SIGTERM directly.

The official image runs as root by default (no `USER` in its config). Because
we want non-root, read-only rootfs, `no-new-privileges`, and a validated env
contract with clear error messages, the wrapper image adds: a `tunnel`
user, an entrypoint that validates/forwards the environment, an OCI-label
set pointing at this repository, and a healthcheck. The wrapper's Dockerfile
is ~20 lines; its entrypoint ~90 lines of POSIX sh.

**Go/no-go: GO**, on the wrap-official-image architecture. The only remaining
pre-implementation blocker is a live credentialed smoke test (Section 11.3);
everything else is resolved by verified facts.

## 2. Verified facts (with sources)

Each fact below was checked against a primary source on 2026-09-05.

| # | Fact | Source |
| - | ---- | ------ |
| F1 | Official client is `tunnel-client`, customer-run agent for Secure MCP Tunnel; connects private MCP servers to ChatGPT/Codex/Responses API/AgentKit; long-polls `api.openai.com` over outbound HTTPS (`/v1/tunnels/*`); no inbound ports. | https://github.com/openai/tunnel-client (README, repo description); https://raw.githubusercontent.com/openai/tunnel-client/main/docs/deployment/overview.md |
| F2 | Official container images are published at `ghcr.io/openai/tunnel-client` for linux/amd64 and linux/arm64. Stable releases publish `<vX.Y.Z>`, `<X.Y.Z>`, `<X.Y>`, `latest`, and `sha-<commit>` tags; prereleases never move `X.Y`/`latest`. Pin exact version or digest. | https://raw.githubusercontent.com/openai/tunnel-client/main/docs/deployment/docker.md |
| F3 | `v0.0.14` (published 2026-09-01, stable, not a prerelease) is the current release and is explicitly named the supported release for the multi-replica OAuth/Harpoon rollout. | https://api.github.com/repos/openai/tunnel-client/releases/latest (fetched 2026-09-05) |
| F4 | The GHCR index for tag `v0.0.14` contains platform children for linux/amd64 (`sha256:77a76fb94e201ee503cb0af2933eec730a1a25ae3ce8ee36b6b7e8aff778089c`) and linux/arm64 (`sha256:9fa9acabb76e8f4728b38c3507b6eec24b9f47e26ad92e76780ea727c315663b`), plus two `attestation-manifest` entries (SBOM/provenance attestations). | Read-only GHCR registry API query, 2026-09-05 (command preserved in this document's appendix) |
| F5 | The amd64 image config (config blob `sha256:70d4299256ac9d34a7f1bbe186c3f966d6b05ef08cb96056b483ad4dba5564bb`) declares: `Entrypoint ["/usr/bin/tunnel-client", "run"]`, no `Cmd`, **no `User`** (runs as root by default), one env `PATH`, `ExposedPorts 8080/tcp`, full OCI label set (`source`, `licenses: Apache-2.0`, `revision 0f870e50…`, `version v0.0.14`), base layer alpine-minirootfs-3.22.5, and copies `tunnel-client` + `cloudflared` to `/usr/bin/`. | GHCR config blob fetch (dereferenced via manifest config digest → `/blobs/<digest>`), 2026-09-05 |
| F6 | License: **Apache License 2.0** (repo `LICENSE` file; image label `org.opencontainers.image.licenses: Apache-2.0`). | https://raw.githubusercontent.com/openai/tunnel-client/main/LICENSE |
| F7 | Required config: control-plane API key + tunnel ID + a `main` MCP channel binding (`--mcp.server-url` or `--mcp.command`). Precedence: flags > env > YAML > defaults. | https://raw.githubusercontent.com/openai/tunnel-client/main/docs/configuration.md |
| F8 | Tunnel ID env var: `CONTROL_PLANE_TUNNEL_ID` (flag `--control-plane.tunnel-id`). Format: `tunnel_` + 32 lowercase hex chars. **`OPENAI_TUNNEL_ID` is not a recognized variable.** | configuration.md, "Control plane" section |
| F9 | Runtime API key env var: `CONTROL_PLANE_API_KEY` (preferred; `OPENAI_API_KEY` fallback). Flag form accepts `env:VARNAME` or `file:/path`. It is the key used by `doctor` and `run`. There is a *separate* `OPENAI_ADMIN_KEY` only for `admin tunnels` CRUD — explicitly not for the daemon. | configuration.md; README "Which value comes from where" |
| F10 | MCP target env var: `MCP_SERVER_URL` (flag `--mcp.server-url`, repeatable; legacy plain form defaults to channel `main`). Streamable HTTP is the transport for HTTP bindings; stdio (`--mcp.command`) and in-memory are the alternatives. | configuration.md, "MCP server" section; README |
| F11 | Minimal documented run is env-only: `CONTROL_PLANE_API_KEY`, `CONTROL_PLANE_TUNNEL_ID`, `MCP_SERVER_URL`, then `tunnel-client run --log.level=info --log.format=struct-text`. The official Docker example likewise passes only env vars and `HEALTH_LISTEN_ADDR`. | configuration.md "Minimal env-var run"; deployment/docker.md "Run container" |
| F12 | Health/admin server: `/healthz`, `/readyz`, `/metrics`, `/ui`; env `HEALTH_LISTEN_ADDR` (default `127.0.0.1:8080`); `:8080` is recommended when an orchestrator must reach the health endpoints remotely. | configuration.md "Health/admin server"; deployment/docker.md |
| F13 | Proxy: when no explicit proxy flag is set for a target, standard `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` semantics apply. Explicit flags (`--http-proxy`, `--control-plane.http-proxy`, `--mcp.http-proxy`, per-channel) accept `url` or `env:VAR` and, when set, override env vars and ignore `NO_PROXY` for that target. The enterprise-proxy sample uses `http_proxy: env:HTTPS_PROXY`. | deployment/overview.md "Outbound proxy environments"; configuration.md "Outbound HTTP proxy" |
| F14 | `tunnel-client init` writes a validated first-use profile (e.g. `init --sample sample_mcp_with_dcr --profile NAME --tunnel-id … --mcp-server-url …`); `doctor` validates config/profile before startup (`doctor --profile NAME --explain`). Profiles are optional — env-only run needs neither. | README "Binary-first flow"; configuration.md |
| F15 | Release assets: per-platform ZIPs (`tunnel-client-runtime-v0.0.14-linux-{amd64,arm64}.zip`), per-ZIP SPDX 2.3 sidecars, `-licenses.txt` (third-party license inventory), `SHA256SUMS.txt` covering all assets, `PUBLIC_URLS.txt`, a Sigstore provenance bundle, vulnerability report, OpenVEX, enterprise evidence. Checksums verified present for linux archives: runtime amd64 `29d29cf8…`, runtime arm64 `7a4a6a4e…`, client amd64 `15bd17e8…`, client arm64 `2de3fb87…`. | https://github.com/openai/tunnel-client/releases/tag/v0.0.14 assets; SHA256SUMS.txt fetched 2026-09-05 |
| F16 | Release ZIPs and images carry signed provenance; images are verifiable with `gh attestation verify oci://ghcr.io/openai/tunnel-client:vX.Y.Z -R openai/tunnel-client`. | deployment/docker.md "Use the published image" |
| F17 | No official "Hello, this is not allowed" statement exists permitting or forbidding third-party redistribution of the release binaries. The Apache-2.0 license *is* the redistribution grant. | LICENSE (F6); absence verified by reviewing repo docs and release notes |
| F18 | Upstream ships its own container-compat smoke that exercises default/overridden entrypoints with read-only profile/Secret mounts, hardened container settings, and SIGTERM shutdown, using intentionally unreachable local endpoints. This confirms the runtime binary is designed for hardened containers and that SIGTERM shutdown is a supported, tested path. | README, `make runtime-container-compatibility` description |

Notes on what was *not* verifiable from a plain-HTTP environment:

- We did not execute the official image (no Docker daemon on the research
  host). Entrypoint/env behavior above is from the image config blob and
  official docs, not from a live run.
- `gh attestation verify` of the image was not run (requires `gh` auth);
  the registry-side attestation manifests (F4) confirm attestations exist.

## 3. Unresolved questions / blockers

| # | Item | Impact | Resolution path |
| - | ---- | ------ | --------------- |
| U1 | No live run of the official image has happened in this environment (no Docker daemon). Behavior from docs + image config is high-confidence but not observed. | Low for design; blocks the "works end-to-end" claim. | Credentialed integration test (Section 11.3) before/with first deployment. |
| U2 | Exact `/healthz` vs `/readyz` status codes/bodies are documented by endpoint name but not by wire format (no openapi entry for the health server was checked). | Low: Docker healthchecks only need HTTP status. | Capture `curl -i` output of both endpoints during U1. |
| U3 | `tunnel-client-runtime` flavor exposes only `run` + `--help`/`--version` (no `doctor`). The **official image's entrypoint uses the full `tunnel-client` binary** (`/usr/bin/tunnel-client run`), so `doctor` is available — but we have not executed it in-container to confirm runtime flavor vs full flavor in the published image. | Cosmetic: if `doctor` were unavailable, the entrypoint skips it (draft handles both). | Check `/usr/bin/tunnel-client doctor --help` during U1. |
| U4 | Whether ChatGPT's tunnel association survives container restarts/reinstalls with the same `tunnel_id` + rotated runtime key is a product-side question, not a client question. | None for the image. | Out of scope; documented for the setup guide. |

None of these block writing the implementation drafts below; U1 blocks only
the *end-to-end works* claim.

## 4. Licensing and redistribution conclusion

- The official client and its published image are Apache-2.0 (F6). Apache-2.0
  §2 grants a copyright+patent license to "prepare Derivative Works" and
  "distribute" object form; binary redistribution in a public GHCR image is
  therefore permitted, *provided* the conditions of §4(a)-(b) are met:
  include a copy of the license, and retain all copyright, patent, trademark,
  and attribution notices from the source form.
- The wrapper image **distributes or references upstream image layers** — it
  is a derived work in the Apache-2.0 sense. Apache-2.0 permits this subject
  to its conditions; it would be inaccurate to describe the wrapper as
  involving "no binary redistribution". The obligations are met by keeping
  the upstream image's license/notice content intact (the wrapper does not
  remove any LICENSE/NOTICE files from the base image) and by the wrapper's
  OCI labels naming the base image and its license.
- Practically, our wrapper approach makes this trivial: the upstream image
  already contains the binary and (as the release layout shows) upstream
  treats license/notice distribution as part of the artifact contract
  (`-licenses.txt` sidecars, SPDX inventory). Our wrapper must **not** strip
  any LICENSE/NOTICE files the upstream image contains (it doesn't need to
  touch them at all), and our own repository keeps a copy of Apache-2.0 with
  a NOTICE-style attribution pointing at `github.com/openai/tunnel-client`
  for the embedded client (already Apache-2.0 in this repo — no change
  needed to the repo license).
- Downloading binaries during `docker build` (the original plan) is now the
  *fallback* design, not the primary. When wrapping the official image, no
  download occurs at all; the digest pin IS the supply-chain control. If a
  binary-copy image were ever needed (e.g. official image discontinued),
  the correct pattern is: download the pinned release ZIP during build,
  verify against the upstream `SHA256SUMS.txt` entry, and keep the
  `-licenses.txt` sidecar in the image. Never commit the binary to git.
- Recommended attribution in our image labels/docs:
  `org.opencontainers.image.description: "Wrapper around ghcr.io/openai/tunnel-client (Apache-2.0) for the chatgpt-youtube-mcp project"`.

Conclusion: **a public redistributable tunnel image is viable**, with the
wrapper design removing nearly all redistribution obligations (we distribute
a derivative image, upstream's license/notice files remain intact inside it).

## 5. Recommended architecture

```
                       ┌────────────────────────────────────────────┐
                       │ Compose network (internal)                 │
 ChatGPT/OpenAI        │                                            │
 control plane         │  ┌──────────────┐      ┌───────────────┐   │
      ▲                │  │ openai-tunnel│      │ youtube-mcp   │   │
      │ long-poll      │  │ (wrapper     │ HTTP │ :8765/mcp     │   │
      └────────────────┼──┤  image over  ├─────►│ (existing     │   │
        HTTPS out      │  │  official    │      │  image)       │   │
                       │  │  image)      │      │               │   │
                       │  └──────┬───────┘      └───────────────┘   │
                       │         │ /healthz (loopback in-container) │
                       │         ▼ healthcheck                     │
                       └────────────────────────────────────────────┘
```

- Base: `ghcr.io/openai/tunnel-client:v0.0.14` pinned additionally by
  per-arch digest (Section 10) — the tag alone is reviewable, the digest is
  the actual lock.
- Wrapper adds: `tunnel` user (uid/gid 10001), `/bin/sh` entrypoint
  (busybox sh is present in the alpine base), nothing else.
- Runtime properties: no Docker socket, unprivileged, `no-new-privileges`,
  read-only rootfs, tmpfs for `/tmp` only, zero published ports, outbound
  HTTPS to `api.openai.com:443` and internal HTTP to `youtube-mcp:8765`.
- The health listener stays on loopback *inside* the container; the
  Compose healthcheck runs `wget` (busybox, present) against it. No port is
  published to the host.
- Config volume: **none** (Section 8.4).

## 6. Proposed repository / file layout

Recommendation: **keep the tunnel image inside `chatgpt-youtube-mcp`, in a
`docker/openai-mcp-tunnel/` subdirectory**, do NOT create a separate repo.

Justification:

- *Independent versioning*: fully preserved — the tunnel image gets its own
  semver (`0.1.0`) and its own tag set in CI, independent of the MCP image
  version. Nothing about sharing a repository couples the versions.
- *Licensing*: the MCP repo is already Apache-2.0; wrapping an Apache-2.0
  component in the same repo is the simplest license story. A separate repo
  would buy nothing except an isolated LICENSE file we'd duplicate.
- *Maintenance*: one repo = one CI pipeline, one CODEOWNERS, one place
  where the Portainer generators, docs, and the image they reference live
  together. The generators' contract and the image's contract drift together
  if they live together.
- *GHCR ownership*: `ghcr.io/<owner>/<image>` follows the *GitHub account*,
  not the repository — `ghcr.io/dracoform/openai-mcp-tunnel:0.1.0` works
  identically whether built from `chatgpt-youtube-mcp` or a dedicated repo.
  The only nuance is that a cross-repo image needs the workflow to declare
  `permissions: packages: write` (it has that) — package visibility is
  configured on the package, not the repo.
- *Counter-argument considered*: a separate repo gives the tunnel image a
  clean slava-free history if it later grows (e.g. the yt-dlp updater).
  Rejected for now: the wrapper is ~3 files; if it ever grows beyond that,
  extraction is a cheap, mechanical move.

```
docker/openai-mcp-tunnel/
├── Dockerfile
├── entrypoint.sh
├── versions.env              # pinned upstream version + digests (reviewable)
└── tests/
    └── test_entrypoint.bats  # see Section 11.1
```

## 7. Exact environment-variable contract

### 7.1 Contract of the official client (verified)

| Variable | Status | Meaning |
| -------- | ------ | ------- |
| `CONTROL_PLANE_TUNNEL_ID` | required | Tunnel ID, format `tunnel_` + 32 lowercase hex (F8) |
| `CONTROL_PLANE_API_KEY` | required | Runtime API key (F9) |
| `MCP_SERVER_URL` | required (for main channel) | Streamable HTTP MCP target, e.g. `http://youtube-mcp:8765/mcp` (F10) |
| `CONTROL_PLANE_BASE_URL` | optional | Default `https://api.openai.com` — host root only |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | optional | Standard semantics when no explicit proxy flag is set (F13) |
| `HEALTH_LISTEN_ADDR` | optional | Default `127.0.0.1:8080` (F12) |
| `LOG_LEVEL` (`debug`/`info`/`warn`), `LOG_FORMAT` (`struct-text`/`json`) | optional | Logging (F11 note: config.md) |
| `OPENAI_ADMIN_KEY` | **not used** | Admin CRUD only; never set in the tunnel container |

### 7.2 Contract of our stack (generators, unchanged)

The generators (both current copies) ask for `OPENAI_TUNNEL_ID`. Two
options:

- **Option A (recommended, this design): the wrapper entrypoint adapts.**
  Generators keep asking for `OPENAI_TUNNEL_ID` (the name users saw in
  ChatGPT setup docs); the entrypoint maps
  `OPENAI_TUNNEL_ID` → `CONTROL_PLANE_TUNNEL_ID`. Zero generator churn;
  users keep the friendlier name; the mapping is one `if` in the entrypoint.
- **Option B: migrate generators to `CONTROL_PLANE_TUNNEL_ID`.** More
  churn, and breaks any deployed stacks; only worth it if the wrapper
  disappears someday.

The container accepts **both** names (explicitly preferring
`CONTROL_PLANE_TUNNEL_ID` if both are set, and rejecting ambiguity is
unnecessary — same value, two spellings; if both set and differ, fail with
EXIT_CONFIG_CONFLICT to avoid silently using the wrong tunnel).

| Our variable | Required | Validation |
| ------------ | -------- | ---------- |
| `OPENAI_TUNNEL_ID` or `CONTROL_PLANE_TUNNEL_ID` | yes (at least one) | `^tunnel_[0-9a-f]{32}$` |
| `CONTROL_PLANE_API_KEY` | yes | non-empty; trimmed of surrounding whitespace; no newline |
| `MCP_SERVER_URL` | yes | absolute http(s) URL |
| `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` | no | passed through untouched (client validates) |
| `LOG_LEVEL` | no | one of `debug\|info\|warn` |
| `LOG_FORMAT` | no | one of `struct-text\|json` |

## 8. Proposed container lifecycle

### 8.1 Startup

1. Entrypoint runs as root (image default), validates env (Section 8.2),
   drops privileges via `setpriv`/`su-exec`-equivalent, then `exec`s the
   client as the `tunnel` user. (BusyBox `su` is present in alpine base;
   the Dockerfile adds `su-exec` in the wrapper layer — single static
   binary, no shell dependency at runtime beyond entrypoint itself.)
2. `exec tunnel-client run --log.level=… --log.format=…` with
   `HEALTH_LISTEN_ADDR=127.0.0.1:8080` (loopback-only; Docker healthcheck
   probes from inside the same container). If we ever want
   orchestrator-side health checks from another pod, flip to `:8080` and
   add an internal-only port — not needed for Compose/Portainer.
3. `MCP_SERVER_URL=http://youtube-mcp:8765/mcp` is passed straight through;
   the client's own `MCP_STARTUP_WAIT_TIMEOUT` (default 0) could be raised
   via env to ride out MCP container startup races — left at default
   because Compose restart policy covers the tunnel restart anyway and a
   bounded wait avoids masking real failures. Documented as tunable.

### 8.2 Validation rules, exit codes, messages

| Condition | Exit | Message (stderr, one line) |
| --------- | ---- | -------------------------- |
| `OPENAI_TUNNEL_ID` and `CONTROL_PLANE_TUNNEL_ID` both set, different values | 78 (EX_CONFIG) | `config error: OPENAI_TUNNEL_ID and CONTROL_PLANE_TUNNEL_ID are both set but differ; set only one` |
| neither tunnel ID var set | 78 | `config error: OPENAI_TUNNEL_ID (or CONTROL_PLANE_TUNNEL_ID) is required` |
| tunnel ID malformed | 78 | `config error: tunnel id must match ^tunnel_[0-9a-f]{32}$, got '<redacted>'` |
| `CONTROL_PLANE_API_KEY` unset/empty/whitespace | 78 | `config error: CONTROL_PLANE_API_KEY is required and must not be empty` |
| `CONTROL_PLANE_API_KEY` contains newline | 78 | `config error: CONTROL_PLANE_API_KEY must not contain newlines` |
| `MCP_SERVER_URL` unset/empty | 78 | `config error: MCP_SERVER_URL is required` |
| `MCP_SERVER_URL` not http(s) absolute | 78 | `config error: MCP_SERVER_URL must be an absolute http(s) URL, got '<value>'` |
| `LOG_LEVEL` set but not in enum | 78 | `config error: LOG_LEVEL must be one of debug,info,warn` |
| `LOG_FORMAT` set but not in enum | 78 | `config error: LOG_FORMAT must be one of struct-text,json` |

The Runtime API key is **never** echoed: messages print variable *names*,
never values (the malformed-tunnel-ID message redacts to a fixed string).
Exit code 78 (sysexits `EX_CONFIG`) distinguishes config errors from client
failures; the container restart policy will keep retrying, but the operator
log line makes the cause obvious. No crash-loop ambiguity: the message is
emitted exactly once per start.

### 8.3 Doctor (optional, non-blocking)

`doctor` requires valid control-plane credentials and performs a metadata
lookup — a network call that can fail for reasons unrelated to config.
Design: run `tunnel-client doctor --profile …` **only if a profile file
exists** (not our default path) and never block startup on it. In the
env-only path (our default) there is nothing `doctor` can validate that the
client's own startup won't, so the entrypoint skips it. This is a
deliberate deviation from the original plan ("run doctor when practical")
— it is *not practical* in env-only mode without adding a network
dependency to container start.

### 8.4 The `/config` volume question

**Verdict: the named volume is unnecessary. Drop it.**

- The official client needs no writable state for env-only operation
  (F11: minimal run is pure env). Profiles are optional and we don't use
  them.
- There is no state to preserve across restarts: tunnel ID and API key
  arrive from the environment every start; the client keeps no session
  state that must survive (`client_instance_id` is regenerated per process
  by design — F1/README).
- A volume would (a) create a writable root-owned directory holding a copy
  of the runtime key if we ever wrote a profile there — exactly what the
  original design wanted to avoid — and (b) survive stack removal in
  Portainer by default, accumulating stale secrets.
- Read-only rootfs therefore works with one `tmpfs` for `/tmp` only.

The generators currently emit `tunnel-config:/config` and a top-level
volume — those lines should be removed when the tunnel image ships.

### 8.5 Shutdown

- Entrypoint uses `exec` → client is PID 1 → SIGTERM goes straight to it.
- `tunnel-client` handles SIGTERM (upstream tests it: F18; PID file removed
  on shutdown per docker.md notes). `restart: unless-stopped` covers
  anything unclean.
- `stop_grace_period: 30s` in Compose gives the client time to finish
  in-flight MCP requests before SIGKILL.

## 9. Security analysis

| Surface | Assessment | Control |
| ------- | ---------- | ------- |
| Runtime API key in env vars | Visible to `docker inspect` and the container's own `/proc/self/environ` — same exposure class as the YouTube API key in the existing stack; Portainer admins can read it either way. | Documented as accepted (generators already warn "treat the generated file as a secret"); `file:`-based secret refs (`--control-plane.api-key=file:...`) are the client-supported upgrade path for Compose `secrets:` if this ever matters. |
| Runtime API key in argv | Never occurs: env-only interface; wrapper uses no CLI secret args. | Entrypoint validation never echoes values; the client redacts keys from its own support bundle. |
| Key in image layers/build history | Impossible: nothing is baked into the wrapper image; build args unused. | Dockerfile has no ARGs touching credentials. |
| Key in logs | The client's log format redacts by default; `LOG_HTTP_RAW_UNSAFE` stays unset. | Entrypoint refuses to log values; log level capped at `info` by default. |
| Inbound exposure | None: zero published ports; health endpoint loopback-only inside the container. | Compose service has no `ports:`; healthcheck runs in-container. |
| Outbound | `api.openai.com:443` (long-poll) + `youtube-mcp:8765` (internal) + optional proxy. | Documented for firewall rules; `NO_PROXY=youtube-mcp,localhost,127.0.0.1` keeps MCP traffic direct when a proxy is configured. |
| Container hardening | Root by default upstream → wrapper adds non-root user. | `user:`, `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, tmpfs `/tmp`. |
| Supply chain | Upstream image digest pin + upstream SBOM/attestation. | `versions.env` records tag + per-arch digests + SHA256SUMS line for the runtime ZIP (used only if the binary-copy fallback is ever activated). |
| tunnel-config volume | Removed (Section 8.4). | N/A |

Residual risks, stated honestly:
- The upstream image is root-by-default; our wrapper adds `USER tunnel` but
  we cannot re-verify upstream's runtime behavior under non-root without
  U1. The client writes nothing to disk in env-only mode, so non-root
  should be inert — but this is exactly the kind of assumption U1 exists
  to test.
- `cap_drop: [ALL]` needs a runtime check too: the client binds a loopback
  port (8080) — unprivileged binding >1024, so no capability needed, but
  U1 should confirm.

## 10. Multi-architecture build strategy

- The official image is already multi-arch (F4: amd64 + arm64 children).
  Our wrapper build must preserve that: `docker buildx build
  --platform linux/amd64,linux/arm64`. Because the wrapper adds only
  labels + user + entrypoint (arch-independent), both platforms resolve to
  the same upstream multi-arch base by digest pinning in `versions.env`.
- **Digest pinning**: pin the *index digest* (the digest of the multi-arch
  manifest list), not per-arch child digests — a per-arch pin in the
  Dockerfile would defeat multi-arch resolution. The per-arch child digests
  still go into `versions.env` as reviewable provenance records.
- CI: QEMU for cross-arch build (wrapper is trivial; no compilation).
- `provenance: true` + `sbom: true` on buildx. Note upstream's release
  images carry their own SBOM attestation; ours adds an SBOM for the
  wrapper layers.
- Fail-closed: the workflow greps `versions.env` for the expected tag +
  index digest and verifies the runtime-ZIP checksum lines exist before
  building (if upstream re-tags/vanishes, build aborts with a clear
  message).

## 11. Testing strategy

### 11.1 No-credential tests (CI, every push)

BATS (`bats-core`) test file runs the entrypoint directly (it's POSIX sh)
with a stubbed final command — the entrypoint's last act is
`exec "$BIN" run …` where `BIN` defaults to `/usr/bin/tunnel-client` and is
overridable via `TUNNEL_CLIENT_BIN` for testing. Assertions:

1. missing both tunnel-ID vars → exit 78, message present, key not printed
2. only `OPENAI_TUNNEL_ID` set → env passed to child contains
   `CONTROL_PLANE_TUNNEL_ID` (mapping works)
3. malformed tunnel ID → exit 78
4. missing API key → exit 78; key (if partially present) never in output
5. missing MCP_SERVER_URL → exit 78
6. `MCP_SERVER_URL=ftp://x` → exit 78
7. proxy vars set → passed through to child env unchanged
8. `LOG_LEVEL=verbose` → exit 78
9. happy path: stub binary receives exactly
   `run --log.level=info --log.format=json` and env contains mapped vars;
   no secrets in argv (stub asserts argv contains no key value)
10. entrypoint never writes a file outside `$TMPDIR`

Container-level (run where Docker exists; manual step in CI):
- `docker run --rm --read-only --cap-drop ALL --user 10001:10001 …`
  with a stubbed command → confirms non-root + read-only fs compatibility.
- `docker kill --signal=TERM` against stub → confirms exec-chain signal
  delivery (stub records signal, exits 0).

### 11.2 Build tests

- Both architectures build (`buildx --platform linux/amd64,linux/arm64`).
- `docker buildx imagetools inspect` shows the two platform children +
  attestation manifests, mirroring F4's shape.
- OCI labels present on the built config (`source`, `revision`, `version`).

### 11.3 Optional credentialed integration test (manual, go/no-go gate)

Prerequisites: a **dedicated test tunnel** + dedicated runtime key created
in the OpenAI org running the test (never the production tunnel/key);
secrets supplied via GitHub Environment secrets (`TUNNEL_TEST_ID`,
`TUNNEL_TEST_KEY`) or a local `.env` that is `.gitignore`d.

```
workflow_dispatch →
  compose up: youtube-mcp + openai-tunnel (built from this branch) +
             OPENAI_TUNNEL_ID/TUNNEL_TEST_ID + CONTROL_PLANE_API_KEY/TUNNEL_TEST_KEY
  assert:    container healthy (healthcheck /healthz)
  assert:    /readyz returns 200 within timeout   ← proves control-plane auth + tunnel connection
  from another container on the same network:
    POST http://youtube-mcp:8765/mcp  initialize + tools/list        ← MCP layer proof
  then, through the real path (ChatGPT connector or a Responses-API call
  against the tunnel): list available tools, call one harmless read-only
  tool (e.g. channel metadata for a fixed channel ID)
  teardown: compose down; never echo secrets; CI log masking on
```

This closes U1 and U3 in one pass. It is the gate before claiming
"works end-to-end"; until it runs, the honest status is
"design verified against primary sources; runtime behavior pending live test."

## 12. Implementation sequence

1. Land `docker/openai-mcp-tunnel/{Dockerfile,entrypoint.sh,versions.env}` +
   BATS tests on this branch → merge to `main`.
2. Add `.github/workflows/publish-openai-mcp-tunnel.yml` (Section 13).
3. Update `generators/*`: replace the placeholder tunnel image with
   `ghcr.io/dracoform/openai-mcp-tunnel:${tunnel_tag}` (default `0.1.0`),
   drop the registry-namespace question **and** the `tunnel-config` volume
   lines.
4. Update `docs/CHATGPT_SETUP.md` + `docs/PORTAINER_STACK_GENERATORS.md`:
   official-name mapping note, healthcheck note, no-volume rationale.
5. Tag/build `0.1.0` via workflow_dispatch; verify image inspect output.
6. Run the credentialed integration test (11.3) once; record results in
   `docs/E2E_RESULT.md` following the existing doc's style.
7. Only after 6: claim end-to-end support publicly.

## 13. Implementation-ready drafts

> These drafts reflect the verified interface (F7–F13). Anything dependent
> on a live run is marked `# UNRESOLVED-…` inline.

### 13.1 `docker/openai-mcp-tunnel/versions.env`

```bash
# Pinned upstream tunnel-client release for openai-mcp-tunnel 0.1.0.
# Verified 2026-09-05 against:
#   https://github.com/openai/tunnel-client/releases/tag/v0.0.14
#   and the GHCR registry API (index + config blobs).
TUNNEL_CLIENT_VERSION=v0.0.14
# Index digest of the multi-arch manifest list (pin THIS in FROM).
TUNNEL_CLIENT_IMAGE_DIGEST=sha256:41d7c85dab37797a3eaa17c41b94a7206dd0bc186fd361034c9ec3863596ff6c
# Per-arch child digests (provenance record only; do NOT pin these in FROM).
TUNNEL_CLIENT_AMD64_DIGEST=sha256:77a76fb94e201ee503cb0af2933eec730a1a25ae3ce8ee36b6b7e8aff778089c
TUNNEL_CLIENT_ARM64_DIGEST=sha256:9fa9acabb76e8f4728b38c3507b6eec24b9f47e26ad92e76780ea727c315663b
# SHA256SUMS.txt entries (used only by the binary-copy fallback path).
TUNNEL_CLIENT_RUNTIME_LINUX_AMD64_SHA256=29d29cf860ada54e4d3c82c715f4fbfcff2abcdc2584c0fc26431308dfa2505b
TUNNEL_CLIENT_RUNTIME_LINUX_ARM64_SHA256=7a4a6a4eb995c175aa0243434ff79ae9e4c2675d1c25e0d983622c48098159fb
```

### 13.2 `docker/openai-mcp-tunnel/Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1
# Wrapper around the official OpenAI Secure MCP Tunnel client image.
# Upstream: https://github.com/openai/tunnel-client (Apache-2.0)
# Pinned by immutable digest (see versions.env for how it was derived).
ARG BASE_IMAGE=ghcr.io/openai/tunnel-client@sha256:41d7c85dab37797a3eaa17c41b94a7206dd0bc186fd361034c9ec3863596ff6c

FROM ${BASE_IMAGE}

# su-exec: tiny static setpriv helper (alpine package, no shell deps at runtime)
RUN addgroup -g 10001 -S tunnel \
 && adduser  -u 10001 -S -G tunnel -H -s /sbin/nologin tunnel \
 && apk add --no-cache su-exec \
 && chmod +x /entrypoint.sh

COPY entrypoint.sh /entrypoint.sh

# Run the client as the unprivileged user via the entrypoint (root only
# long enough to setpriv; the client itself never runs as root).
ENTRYPOINT ["/entrypoint.sh"]

# Health endpoint is loopback-only inside the container (HEALTH_LISTEN_ADDR
# below); wget is provided by busybox in the alpine base image.
ENV HEALTH_LISTEN_ADDR=127.0.0.1:8080 \
    LOG_LEVEL=info \
    LOG_FORMAT=json
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

LABEL org.opencontainers.image.title="openai-mcp-tunnel" \
      org.opencontainers.image.description="Secure MCP Tunnel client wrapper for the chatgpt-youtube-mcp project; based on ghcr.io/openai/tunnel-client (Apache-2.0)" \
      org.opencontainers.image.url="https://github.com/Dracoform/chatgpt-youtube-mcp" \
      org.opencontainers.image.source="https://github.com/Dracoform/chatgpt-youtube-mcp" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="ghcr.io/openai/tunnel-client" \
      org.opencontainers.image.base.digest="sha256:41d7c85dab37797a3eaa17c41b94a7206dd0bc186fd361034c9ec3863596ff6c"
```

### 13.3 `docker/openai-mcp-tunnel/entrypoint.sh`

```sh
#!/bin/sh
# Entrypoint for the openai-mcp-tunnel wrapper image.
# Adapts the project's env contract to the official tunnel-client env
# contract and execs `tunnel-client run` as an unprivileged user.
# Secrets are never printed.
set -u

TUNNEL_CLIENT_BIN="${TUNNEL_CLIENT_BIN:-/usr/bin/tunnel-client}"
TUNNEL_RUN_USER="${TUNNEL_RUN_USER:-tunnel}"

die() { # die EXIT MESSAGE  — message must be pre-redacted by caller
    printf '%s\n' "$2" >&2
    exit "$1"
}
EX_CONFIG=78

# ---------------------------------------------------------------- tunnel id
openai_tid="${OPENAI_TUNNEL_ID:-}"
control_tid="${CONTROL_PLANE_TUNNEL_ID:-}"
if [ -n "$openai_tid" ] && [ -n "$control_tid" ] && [ "$openai_tid" != "$control_tid" ]; then
    die $EX_CONFIG "config error: OPENAI_TUNNEL_ID and CONTROL_PLANE_TUNNEL_ID are both set but differ; set only one"
fi
TUNNEL_ID="${control_tid:-$openai_tid}"
[ -n "$TUNNEL_ID" ] || die $EX_CONFIG "config error: OPENAI_TUNNEL_ID (or CONTROL_PLANE_TUNNEL_ID) is required"
case "$TUNNEL_ID" in
    tunnel_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) die $EX_CONFIG "config error: tunnel id must match ^tunnel_[0-9a-f]{32}$ (value not shown)" ;;
esac

# ----------------------------------------------------------------- api key
[ -n "${CONTROL_PLANE_API_KEY:-}" ] || die $EX_CONFIG "config error: CONTROL_PLANE_API_KEY is required and must not be empty"
case "$CONTROL_PLANE_API_KEY" in
    *[[:space:]]) ;;
    *) : ;;
esac
# newline check (values with trailing whitespace are tolerated by the
# client, but embedded newlines would corrupt Compose env handling)
case "$CONTROL_PLANE_API_KEY" in
    *'
'*) die $EX_CONFIG "config error: CONTROL_PLANE_API_KEY must not contain newlines" ;;
esac

# ----------------------------------------------------------- mcp server url
[ -n "${MCP_SERVER_URL:-}" ] || die $EX_CONFIG "config error: MCP_SERVER_URL is required"
case "$MCP_SERVER_URL" in
    http://*|https://*) ;;
    *) die $EX_CONFIG "config error: MCP_SERVER_URL must be an absolute http(s) URL (value not shown)" ;;
esac

# ----------------------------------------------------------------- logging
LOG_LEVEL="${LOG_LEVEL:-info}"
LOG_FORMAT="${LOG_FORMAT:-json}"
case "$LOG_LEVEL" in
    debug|info|warn) : ;;
    *) die $EX_CONFIG "config error: LOG_LEVEL must be one of debug,info,warn" ;;
esac
case "$LOG_FORMAT" in
    struct-text|json) : ;;
    *) die $EX_CONFIG "config error: LOG_FORMAT must be one of struct-text,json" ;;
esac

# ------------------------------------------------- map to official env names
# Official client contract (docs/configuration.md):
#   CONTROL_PLANE_TUNNEL_ID / CONTROL_PLANE_API_KEY / MCP_SERVER_URL
export CONTROL_PLANE_TUNNEL_ID="$TUNNEL_ID"
export HEALTH_LISTEN_ADDR="${HEALTH_LISTEN_ADDR:-127.0.0.1:8080}"
# Proxy vars (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) pass through untouched —
# the client applies standard env semantics when no explicit proxy flag is set.

# -------------------------------------------------------------------- exec
# Drop privileges, then exec: client becomes PID 1 of the user process and
# receives SIGTERM directly (the exec replaces the entrypoint shell).
# su-exec is in the image; TUNNEL_RUN_USER is overridable for tests.
if command -v su-exec >/dev/null 2>&1; then
    exec su-exec "$TUNNEL_RUN_USER" "$TUNNEL_CLIENT_BIN" run \
        --log.level="$LOG_LEVEL" --log.format="$LOG_FORMAT"
else
    # test path: no su-exec on host, run as-is
    exec "$TUNNEL_CLIENT_BIN" run \
        --log.level="$LOG_LEVEL" --log.format="$LOG_FORMAT"
fi
```

### 13.4 GitHub Actions workflow (`.github/workflows/publish-openai-mcp-tunnel.yml`)

```yaml
name: publish-openai-mcp-tunnel

on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Image version tag (e.g. 0.1.0)'
        required: true
  push:
    paths:
      - 'docker/openai-mcp-tunnel/**'
      - '.github/workflows/publish-openai-mcp-tunnel.yml'

env:
  IMAGE: ghcr.io/dracoform/openai-mcp-tunnel

permissions:
  contents: read
  packages: write

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install bats
        run: sudo apt-get update -qq && sudo apt-get install -y -qq bats
      - name: Entrypoint unit tests (no credentials)
        run: bats docker/openai-mcp-tunnel/tests/test_entrypoint.bats
      - name: Verify version pin file is complete (fail closed)
        run: |
          cd docker/openai-mcp-tunnel
          grep -q '^TUNNEL_CLIENT_VERSION=v' versions.env
          grep -q '^TUNNEL_CLIENT_IMAGE_DIGEST=sha256:[0-9a-f]{64}$' versions.env
          grep -q '^TUNNEL_CLIENT_AMD64_DIGEST=sha256:[0-9a-f]{64}$' versions.env
          grep -q '^TUNNEL_CLIENT_ARM64_DIGEST=sha256:[0-9a-f]{64}$' versions.env

  build-and-publish:
    needs: test
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - name: Extract pinned digests (fail closed if missing)
        id: pins
        run: |
          cd docker/openai-mcp-tunnel
          {
            echo "digest=$(grep '^TUNNEL_CLIENT_IMAGE_DIGEST=' versions.env | cut -d= -f2)"
            echo "upver=$(grep '^TUNNEL_CLIENT_VERSION=' versions.env | cut -d= -f2)"
          } >> "$GITHUB_OUTPUT"
      - name: Verify digest against the registry before building
        run: |
          # resolve the tag's index digest live; abort if it moved
          TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:openai/tunnel-client:pull&service=ghcr.io" | jq -r .token)
          LIVE=$(curl -s -H "Authorization: Bearer $TOKEN" \
            -H 'Accept: application/vnd.oci.image.index.v1+json' \
            https://ghcr.io/v2/openai/tunnel-client/manifests/${{ steps.pins.outputs.upver }} \
            | jq -r .digest)
          [ "$LIVE" = "${{ steps.pins.outputs.digest }}" ] || {
            echo "FATAL: pinned digest moved upstream (pin=${{ steps.pins.outputs.digest }}, live=$LIVE)"; exit 1; }
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ${{ env.IMAGE }}
          tags: |
            type=raw,value=${{ inputs.version }}
            type=raw,value=latest
            # 0.1.x alias from version: split manually below if desired
      - uses: docker/build-push-action@v6
        with:
          context: docker/openai-mcp-tunnel
          platforms: linux/amd64,linux/arm64
          push: true
          provenance: true
          sbom: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          build-args: |
            BASE_IMAGE=ghcr.io/openai/tunnel-client@${{ steps.pins.outputs.digest }}
      - name: Verify multi-arch result (fail closed)
        run: |
          docker buildx imagetools inspect ${{ env.IMAGE }}:${{ inputs.version }} \
            | tee /dev/stderr | grep -q 'linux/amd64' || exit 1
          docker buildx imagetools inspect ${{ env.IMAGE }}:${{ inputs.version }} \
            | tee /dev/stderr | grep -q 'linux/arm64' || exit 1
```

### 13.5 Compose service (stack fragment)

```yaml
services:
  openai-tunnel:
    image: ghcr.io/dracoform/openai-mcp-tunnel:0.1.0
    restart: unless-stopped
    environment:
      OPENAI_TUNNEL_ID: "${OPENAI_TUNNEL_ID}"          # adapted to CONTROL_PLANE_TUNNEL_ID by entrypoint
      CONTROL_PLANE_API_KEY: "${CONTROL_PLANE_API_KEY}"
      MCP_SERVER_URL: "http://youtube-mcp:8765/mcp"
      NO_PROXY: "youtube-mcp,localhost,127.0.0.1"
      # HTTP_PROXY / HTTPS_PROXY: set only if the site requires an outbound proxy
    read_only: true
    tmpfs:
      - /tmp:size=16m
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    user: "10001:10001"   # belt-and-braces; entrypoint su-exec no-ops for non-root
    stop_grace_period: 30s
    # NO ports: tunnel is outbound-only; health is loopback inside the container.
    # NO volumes: env-only client needs no writable state (see research §8.4).
```

### 13.6 Representative BATS test (`tests/test_entrypoint.bats`)

```bash
#!/usr/bin/env bats
# Tests for the openai-mcp-tunnel entrypoint. No network, no credentials.
# Uses a stub "client" that records argv+env and exits 0.

setup() {
    BATS_TMPDIR_SAFE="$(mktemp -d)"
    STUB="$BATS_TMPDIR_SAFE/stub-client"
    cat > "$STUB" <<'EOF'
#!/bin/sh
{
  echo "ARGV: $*"
  env | grep -E '^(CONTROL_PLANE_TUNNEL_ID|CONTROL_PLANE_API_KEY|MCP_SERVER_URL|NO_PROXY|HTTP_PROXY|HTTPS_PROXY|HEALTH_LISTEN_ADDR)=' | sort
} > "$RECORD_FILE"
EOF
    chmod +x "$STUB"
    export RECORD_FILE="$BATS_TMPDIR_SAFE/record"
    export TUNNEL_CLIENT_BIN="$STUB"
    export TUNNEL_RUN_USER="$(id -un)"   # no su-exec on the test host
    VALID_ID="tunnel_0123456789abcdef0123456789abcdef"
}

teardown() { rm -rf "$BATS_TMPDIR_SAFE"; }

run_ep() { run env -i PATH="$PATH" "$BASH_TEST_ENV[@]" docker/openai-mcp-tunnel/entrypoint.sh; }

@test "missing tunnel id -> exit 78, no value printed" {
    run_ep
    [ "$status" -eq 78 ]
    [[ "$output" == *"OPENAI_TUNNEL_ID"* ]]
    [[ "$output" != *"tunnel_"* ]]   # no accidental value echo
}

@test "malformed tunnel id -> exit 78" {
    BASH_TEST_ENV=(OPENAI_TUNNEL_ID="tunnel_SHORT" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp)
    run_ep
    [ "$status" -eq 78 ]
}

@test "valid contract maps OPENAI_TUNNEL_ID -> CONTROL_PLANE_TUNNEL_ID and execs run" {
    BASH_TEST_ENV=(OPENAI_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://youtube-mcp:8765/mcp NO_PROXY=youtube-mcp,localhost,127.0.0.1)
    run_ep
    [ "$status" -eq 0 ]
    grep -q '^ARGV: run --log.level=info --log.format=json$' "$RECORD_FILE"
    grep -q "^CONTROL_PLANE_TUNNEL_ID=$VALID_ID" "$RECORD_FILE"
    ! grep -q 'sk-test' "$RECORD_FILE" || [ "$(grep -c sk-test "$RECORD_FILE")" -eq 1 ]
    # argv contains no secret:
    ! grep -q 'sk-test' <(grep '^ARGV' "$RECORD_FILE")
}

@test "proxy vars pass through" {
    BASH_TEST_ENV=(OPENAI_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp HTTPS_PROXY=http://proxy:3128 NO_PROXY=youtube-mcp,localhost,127.0.0.1)
    run_ep
    [ "$status" -eq 0 ]
    grep -q '^HTTPS_PROXY=http://proxy:3128' "$RECORD_FILE"
    grep -q '^NO_PROXY=youtube-mcp,localhost,127.0.0.1' "$RECORD_FILE"
}

@test "missing api key -> exit 78" {
    BASH_TEST_ENV=(OPENAI_TUNNEL_ID="$VALID_ID" MCP_SERVER_URL=http://x/mcp)
    run_ep
    [ "$status" -eq 78 ]
}

@test "bad LOG_LEVEL -> exit 78" {
    BASH_TEST_ENV=(OPENAI_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp LOG_LEVEL=verbose)
    run_ep
    [ "$status" -eq 78 ]
}
```

> Draft-status note for 13.4: the `metadata-action` tag block above shows
> `0.1.0` + `latest`; the `0.1` alias requires either a `type=semver`
> pattern (`type=semver,pattern={{major}}.{{minor}}` with the version as a
> git tag) or a second `type=raw,value=0.1` entry — the implementation
> should pick one and keep the three-tag contract from the task
> (0.1.0 / 0.1 / latest).

## 14. Go/no-go recommendation

**GO** — with the architecture changed from "build our own tunnel image"
to "wrap the official image":

- `ghcr.io/dracoform/openai-mcp-tunnel:0.1.0` = thin wrapper over
  `ghcr.io/openai/tunnel-client@sha256:41d7c85d…` (v0.0.14): non-root,
  read-only, env-contract adapter, loopback healthcheck, OCI labels.
- The public redistributable tunnel image is viable under Apache-2.0 with
  obligations met because the wrapper keeps the upstream image's
  LICENSE/NOTICE content intact and labels the base image).
- Environment contract: generators keep `OPENAI_TUNNEL_ID`; entrypoint maps
  to the official `CONTROL_PLANE_TUNNEL_ID`. `CONTROL_PLANE_API_KEY` and
  `MCP_SERVER_URL` are already correct as-is.
- `tunnel-config:/config` volume: **drop** — no writable state is needed.

Condition: the "works end-to-end" claim waits for the credentialed
integration test (11.3). Until then the honest claim is
"design verified against primary sources on 2026-09-05."

## Appendix A: verification commands used (reproducibility)

```bash
# Releases
curl -s https://api.github.com/repos/openai/tunnel-client/releases/latest | jq '{tag: .tag_name, published: .published_at, prerelease: .prerelease}'

# License
curl -s https://raw.githubusercontent.com/openai/tunnel-client/main/LICENSE | head -3

# Docs (fetched and read in full)
curl -s https://raw.githubusercontent.com/openai/tunnel-client/main/README.md
curl -s https://raw.githubusercontent.com/openai/tunnel-client/main/docs/configuration.md
curl -s https://raw.githubusercontent.com/openai/tunnel-client/main/docs/deployment/overview.md
curl -s https://raw.githubusercontent.com/openai/tunnel-client/main/docs/deployment/docker.md

# Release checksums
curl -sL https://github.com/openai/tunnel-client/releases/download/v0.0.14/SHA256SUMS.txt | grep linux

# GHCR: anonymous pull token -> index -> amd64 manifest -> config blob
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:openai/tunnel-client:pull&service=ghcr.io" | jq -r .token)
curl -s -H "Authorization: Bearer $TOKEN" -H 'Accept: application/vnd.oci.image.index.v1+json' \
  https://ghcr.io/v2/openai/tunnel-client/manifests/v0.0.14 | jq .
curl -sL -H "Authorization: Bearer $TOKEN" \
  "https://ghcr.io/v2/openai/tunnel-client/blobs/sha256:70d4299256ac9d34a7f1bbe186c3f966d6b05ef08cb96056b483ad4dba5564bb" | jq '{Entrypoint: .config.Entrypoint, User: .config.User, Labels: .config.Labels}'
```
