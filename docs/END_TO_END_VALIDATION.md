# End-to-end validation: ChatGPT → Secure MCP Tunnel → YouTube MCP

Status: validated live with real credentials on 2026-09-06. Two defects
found during validation are fixed on this branch; the fixes are pending a
cold redeploy of the live Portainer stack (see "Pending" below). No
secrets, API keys, full tunnel IDs, container IDs, or private
infrastructure details are recorded here.

## Successful path

- ChatGPT tool discovery over the Secure MCP Tunnel: six read-only tools
  discovered, annotations confirmed.
- Tunnel connection: tunnel metadata fetched, `tunnel-client` started,
  MCP session initialized.
- MCP protocol version: `2025-11-25`; server reported as
  `YouTube Current Data (read-only)`.
- Official YouTube Data API v3 request succeeded with the configured key.
- Caption discovery via yt-dlp for a German StarCraft video:
  `get_video` returned no usable availability signal (see Issue 2),
  `list_caption_tracks` discovered the automatic original track
  `de-orig`; no manual track existed; yt-dlp reported translation
  support.
- Transcript retrieval: `get_video_transcript` returned a successful
  automatic German transcript with **767 segments**.

## Issue 1 found during validation: container startup race

On cold deployment the tunnel started before youtube-mcp listened on
port 8765. Evidence: `Post http://youtube-mcp:8765/mcp: dial tcp
…:8765: connect: connection refused` from the tunnel, followed shortly
by `Uvicorn running on http://0.0.0.0:8765` in the MCP logs. Restarting
only the tunnel container immediately initialized the MCP session.

Addressed on this branch: a real `/healthz` HTTP readiness endpoint on
the MCP port itself (FastMCP custom route on the same uvicorn listener),
a Python-stdlib Docker HEALTHCHECK against it (no curl/wget), and
`depends_on: youtube-mcp: {condition: service_healthy}` in both
generated Compose variants.

## Issue 2 found during validation: misleading caption availability

The official Data API `caption=false` for the test video even though
automatic German captions existed and downloaded successfully. The old
`caption_available: false` made a stronger claim than the source
supports.

Addressed on this branch: `get_video` now reports the raw value as
`youtube_api_caption_flag` and maps an official false to
`caption_available: null` (unknown) — only positive knowledge is `true`,
and `false` is reserved for an authoritative discovery operation.
`list_caption_tracks` is documented as authoritative for manual and
automatic tracks; the `get_video` tool description instructs ChatGPT
accordingly. `get_video` does not spawn yt-dlp just to resolve the
flag. Regression fixtures cover the observed contradiction.

## Health endpoint semantics

- Tunnel `/healthz` is **process liveness only**. It does not prove that
  the MCP dependency is reachable — which is exactly why the startup
  race was invisible to the tunnel healthcheck.
- Tunnel `/readyz` would additionally gate on control-plane/MCP
  readiness, but its exact semantics were not tested in a container
  during this validation. Changing the tunnel healthcheck to `/readyz`
  is therefore deliberately **not** done here; it requires its own
  live test first.
- MCP `/healthz` (new) proves the uvicorn listener on 8765 accepts
  connections and the application is serving — this is what the
  `service_healthy` dependency waits for.

## OAuth note

The upstream OAuth discovery warning from the tunnel is non-blocking:
this MCP intentionally exposes no app-level OAuth; all tools are
read-only and unauthenticated at the MCP layer.

## Pending

- Merge this branch; the `publish-container` workflow on `main` will
  rebuild and replace `ghcr.io/dracoform/chatgpt-youtube-mcp:latest`
  (documented, intended behavior — see PR description).
- Cold-redeploy the live Portainer stack from the regenerated YAML and
  confirm: youtube-mcp becomes healthy before the tunnel starts, the
  tunnel initializes the MCP session on the first attempt, and a
  German automatic-caption transcript still retrieves successfully.
  This final live confirmation is pending.
