# End-to-end validation: ChatGPT → Secure MCP Tunnel → YouTube MCP

Status: **validated and confirmed live** with real credentials on
2026-09-06. Both defects found during the first validation round were
fixed (PR #3), published to `ghcr.io/dracoform/chatgpt-youtube-mcp:latest`,
and then **confirmed with a genuine cold Portainer deployment** of the
regenerated stack (PR #4 dependency fix merged first). No secrets, API
keys, runtime keys, full tunnel IDs, container IDs, private hostnames, IP
addresses, or complete transcripts are recorded here.

## Round 1: initial live validation (found two defects)

The complete path worked end to end, but validation exposed:

1. **Container startup race** — the tunnel started before youtube-mcp
   listened on 8765 (`connection refused`, then `Uvicorn running`).
   Fixed by the MCP `/healthz` readiness endpoint, a Python-stdlib
   Docker HEALTHCHECK, and
   `depends_on: youtube-mcp: {condition: service_healthy}`.
2. **Misleading caption availability** — the official Data API
   `caption=false` for a video whose automatic German captions existed
   and downloaded successfully. Fixed by the
   `youtube_api_caption_flag` / `caption_available: null (unknown)`
   contract.

Both fixes were regression-tested offline and published.

## Round 2: cold-deploy confirmation (all checks passed)

Observed after a genuine cold Portainer deployment of the regenerated
stack:

1. Startup/readiness:
   - youtube-mcp became **healthy before openai-tunnel started**.
   - The tunnel initialized the MCP session **on its first attempt**.
   - **No connection-refused error occurred.**
   - MCP protocol version: `2025-11-25`.
   - Server: `YouTube Current Data (read-only)`, version `1.29.1`.
2. Caption-semantics regression check:
   - `get_video`: `youtube_api_caption_flag: false`,
     `caption_available: null` — the official false is no longer
     presented as proof of absence.
   - `list_caption_tracks`: no manual tracks; automatic original track
     `de-orig` present with formats `json3, srt, srv1, srv2, srv3,
     ttml, vtt`.
   - `get_video_transcript`: returned language `de`, `automatic: true`,
     **767 segments**, transcript retrieval successful.

The two original defects are therefore **live-validated and closed**.

## Health endpoint semantics

- **MCP `/healthz`** proves **MCP listener readiness**: the uvicorn
  listener on 8765 accepts connections and the application is serving.
  This is what the Docker HEALTHCHECK and the
  `service_healthy` dependency wait for.
- **Tunnel `/healthz`** proves **tunnel process liveness only**. It does
  not prove that the MCP dependency is reachable — which is why the
  startup race was invisible to the tunnel healthcheck.
- Tunnel `/readyz` would additionally gate on control-plane/MCP
  readiness, but its exact semantics have not been tested in a
  container. Switching the tunnel healthcheck to `/readyz` remains
  deliberately **not done**; it requires its own live test first.

## OAuth note

The upstream OAuth discovery warning from the tunnel is non-blocking:
this MCP intentionally exposes no app-level OAuth; all tools are
read-only and unauthenticated at the MCP layer.
