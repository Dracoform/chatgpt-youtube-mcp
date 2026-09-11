# YouTube MCP for ChatGPT

**Give ChatGPT direct, structured access to YouTube videos, transcripts, channels, search, and ordered playlists — including long videos and specific time ranges that are otherwise difficult to inspect reliably.**

Ask about the last 30 minutes of a four-hour video, retrieve only a relevant chapter instead of consuming the transcript from the beginning, search for candidate passages inside a long transcript, or continue through long transcripts page by page. Transcript retrieval supports bounded `start`/`end` ranges and continuation-based pagination, so ChatGPT can request the part it actually needs rather than wasting context on everything that came before it.

Playlist support adds `get_playlist` and `find_playlist_position` for explicit, ordered playlist context. A playlist can be enumerated in authoritative 1-based order, after which its video IDs can be passed directly to the existing metadata and transcript tools.

`find_playlist_position` deliberately does **not** trust YouTube's `index=` URL parameter. Given a watch URL containing both a video ID and explicit `list=` playlist context, it enumerates the playlist and determines the video's actual position from membership. An `index=` parameter, when present, is returned only as contextual information. A watch URL with `list=` but without `index=` works the same way.

Membership is never inferred from a bare video URL.

Optionally, the server can self-update its bundled `yt-dlp` in the background from PyPI. Updates are staged, SHA-256-verified, validated before promotion, and promoted atomically. This is **disabled by default** and uses the `stable` channel unless `nightly` is explicitly opted into.

Under the hood, this is a read-only MCP bridge for current YouTube metadata, captions, channels, recent uploads, playlists, search, and transcript retrieval. It is deliberately a small proof of concept for the path:

`ChatGPT -> MCP -> YouTube -> structured data -> ChatGPT`

The bridge uses the **YouTube Data API v3** when possible, the public channel Atom feed for recent uploads, and clearly marked `yt-dlp` fallbacks for public transcripts and no-key operation.

> **Naming note:** “YouTube Data API v3” refers to Google's public YouTube API version. It is unrelated to the version of this MCP server or connector.

## Published container

GitHub Actions tests the project and publishes a public multi-architecture image for AMD64 and ARM64:

```text
ghcr.io/dracoform/chatgpt-youtube-mcp:latest
```

Versioned releases use Git tags such as `v0.1.0` and publish the corresponding `0.1.0` and `0.1` image tags. After the first workflow run, the package visibility must be changed to **Public** once in the GitHub package settings; repository visibility and package visibility are separate settings.

## OpenAI Secure MCP Tunnel wrapper

The repository also prepares a thin wrapper image around the official OpenAI `tunnel-client` container (`ghcr.io/openai/tunnel-client`, Apache-2.0, pinned by digest) for the Secure MCP Tunnel path:

```text
ghcr.io/dracoform/openai-mcp-tunnel:0.1.0
```

Status: the wrapper is live-validated — the complete ChatGPT → Secure MCP Tunnel → YouTube path passed a credentialed end-to-end test and a cold Portainer deployment (see [docs/END_TO_END_VALIDATION.md](docs/END_TO_END_VALIDATION.md)). See [docs/OPENAI_SECURE_MCP_TUNNEL.md](docs/OPENAI_SECURE_MCP_TUNNEL.md) and [docs/OPENAI_MCP_TUNNEL_IMAGE_RESEARCH.md](docs/OPENAI_MCP_TUNNEL_IMAGE_RESEARCH.md) for design details.

## Quick start

```bash
cp .env.example .env
# Add YOUTUBE_API_KEY to .env for official metadata/channel/search/playlist calls.
# How to obtain one: docs/YOUTUBE_API_KEY.md
docker compose up --build -d
```

The Streamable HTTP endpoint is:

```text
http://127.0.0.1:8765/mcp
```

For a Python development environment:

```bash
uv sync
uv run python -m unittest discover -s tests -v
uv run python scripts/live_smoke.py 'https://www.youtube.com/watch?v=VIDEO_ID'
uv run youtube-current-data-mcp
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INTERFACE.md](docs/INTERFACE.md), [docs/YOUTUBE_API_KEY.md](docs/YOUTUBE_API_KEY.md), [docs/CHATGPT_SETUP.md](docs/CHATGPT_SETUP.md), and [docs/END_TO_END_VALIDATION.md](docs/END_TO_END_VALIDATION.md).

For a guided Portainer Web Editor stack, use the hosted web generator at **https://dracoform.github.io/chatgpt-youtube-mcp/** — it runs entirely client-side in your browser: no values are uploaded, submitted, or persisted by the application, and it makes no network requests after loading. The generated YAML is displayed for copy/paste into Portainer; nothing is downloaded.

The Bash (`generators/generate_docker-compose_for_ChatGPT_MCP.sh`) and PowerShell (`generators/generate_docker-compose_for_ChatGPT_MCP.ps1`) generators remain available as offline alternatives — all three produce the same stack. See [docs/PORTAINER_STACK_GENERATORS.md](docs/PORTAINER_STACK_GENERATORS.md).

## Transcript sizing and pagination

Transcript responses are deliberately bounded.

```env
# Default transcript response size.
YOUTUBE_TRANSCRIPT_MAX_CHARS=60000

# Absolute maximum allowed per request.
YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS=120000
```

`YOUTUBE_TRANSCRIPT_MAX_CHARS` is the normal page size, not the total transcript limit. Long transcripts are continued through pagination using `pagination.has_more` and `next_continuation`.

Keeping the default at 60,000 characters avoids unnecessarily large MCP responses while still allowing a caller to request up to 120,000 characters where useful. Transcripts longer than either value can be retrieved page by page.

For known sections of long videos, prefer bounded `start` and `end` ranges rather than retrieving content from the beginning.

## Optional yt-dlp self-updater

The server includes an optional staged updater for `yt-dlp`. It is intended to provide a faster recovery path when upstream YouTube changes break extraction without requiring the MCP itself to implement or maintain a custom YouTube extractor.

The updater is **disabled by default**. When disabled, the `yt-dlp` version bundled with the container is used normally and no updater background work or PyPI requests are performed.

The available configuration options are:

```env
# Enable the background updater. Default: false
YTDLP_AUTO_UPDATE=false

# Update channel: stable or nightly. Default: stable
YTDLP_UPDATE_CHANNEL=stable

# Seconds between update checks. Default: 86400 (24 hours)
YTDLP_UPDATE_INTERVAL=86400

# Persistent directory for staged yt-dlp versions.
# Default: /app/state/ytdlp
YTDLP_STATE_DIR=/app/state/ytdlp

# Run an additional live YouTube smoke test before promotion.
# Default: false
YTDLP_UPDATE_SMOKE=false

# Video used by the optional promotion smoke test.
# Normally there is no reason to change this.
YTDLP_UPDATE_SMOKE_URL=https://www.youtube.com/watch?v=BaW1PyD2tbw
```

`stable` is the recommended channel. `nightly` is available as an explicit opt-in for situations where an upstream YouTube change requires a newer `yt-dlp` build.

`YTDLP_STATE_DIR` should reside on persistent writable storage when the updater is enabled. The supplied Docker Compose configuration provides this through the `/app/state` volume.

`YTDLP_UPDATE_SMOKE_URL` is only used when `YTDLP_UPDATE_SMOKE=true`.

When automatic updating is enabled, an update is handled as a staged promotion rather than replacing the bundled package in place:

1. release metadata is retrieved from PyPI;
2. the candidate wheel is downloaded with a bounded maximum size;
3. its SHA-256 digest is verified against the PyPI metadata;
4. the candidate is extracted into its own immutable version directory;
5. the candidate must successfully run and report the expected `yt-dlp` version;
6. if `YTDLP_UPDATE_SMOKE=true`, an additional live YouTube smoke check must succeed;
7. only then is the candidate atomically promoted to the active version.

The previous promoted version is retained for rollback. A failed download, checksum verification, extraction, validation, or smoke test leaves the currently active version untouched.

The bundled container version is never overwritten and remains the known-good fallback.

Active requests resolve the selected `yt-dlp` version when their subprocess starts, so atomic promotion cannot expose a partially installed version to an in-flight request.

Updater status is available through the read-only `get_ytdlp_updater_status` MCP tool. It reports configuration, bundled and active versions, the previous version, staged versions, and the result of the latest update check.

> **Security note:** Enabling automatic updates permits newly downloaded `yt-dlp` code to execute inside the MCP container. SHA-256 verification, validation-before-promotion, bounded downloads, rollback, and the permanent bundled fallback reduce the risk, but automatic updating remains an explicit trust decision. Leave `YTDLP_AUTO_UPDATE=false` if you prefer to update only by deploying newly built container images.

## Playlist behavior

### `get_playlist`

Accepts:

* a playlist ID such as `PL...`, `UU...`, `RD...`, or `OL...`
* a `youtube.com/playlist?list=...` URL
* a YouTube watch, Shorts, or embed URL containing explicit `list=` playlist context

The tool enumerates the playlist in **1-based playlist order** and returns structured entries including the position, video ID, URL, and title. Additional metadata such as channel, publication time, playlist insertion time, and playlist-item notes is returned where the selected upstream source provides it.

Enumeration is bounded to prevent unbounded responses.

For watch URLs containing `list=` context, any video ID and `index=` value from the URL are treated only as input context. The playlist enumeration remains authoritative.

### `find_playlist_position`

Accepts a YouTube watch, Shorts, or embed URL containing both:

* a video ID
* explicit `list=` playlist context

The tool enumerates the playlist and finds the matching video ID.

Example:

```text
https://www.youtube.com/watch?v=rZ_9TQo0N6g&list=PLP1Zv1tsQgEhmO_osGoRLreKh1OuXt0zE
```

The absence of `index=` does not matter. The actual position is determined from playlist enumeration.

For a URL such as:

```text
https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID&index=99
```

the supplied `index=99` is **not trusted as playlist position**. It may be returned as `url_index_context`, but the reported `position` is derived independently by enumerating the playlist.

This prevents stale, modified, misleading, or absent URL indices from being mistaken for authoritative playlist membership or ordering.

## Security boundary

* Every MCP tool is annotated read-only and idempotent.
* `get_playlist` and `find_playlist_position` are deterministic, read-only, idempotent playlist tools with bounded enumeration.
* Playlist membership is never inferred from a bare video URL; explicit `list=` context is required when determining a video's playlist position.
* URL `index=` values are never treated as authoritative playlist positions.
* There are no post, comment, subscribe, delete, upload, or account-modification tools.
* API keys remain server-side environment variables and are never returned.
* Arbitrary URLs are rejected; only recognized YouTube URL shapes are accepted.
* Tool output and transcript size are bounded (`YOUTUBE_TRANSCRIPT_MAX_CHARS`, default 60000, hard-capped by `YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS`, default 120000).
* Long transcripts are paginated: `get_video_transcript` accepts `start`/`end` (seconds, `"MM:SS"`, `"HH:MM:SS"`) and returns `pagination.has_more` / `next_continuation` so a client can continue through the transcript without receiving repeated content.
* `search_video_transcript` locates regions in long transcripts via **deterministic textual substring search** — multiple `queries` per call, no semantic/fuzzy/LLM matching.
* Search results are locators (`match_start`/`match_end`, `timestamp`, snippet text), not authoritative transcript passages. Follow promising hits with a bounded `get_video_transcript(start, end)`.
* Zero search matches mean only that the supplied query strings were not found. They do **not** prove a topic is absent; full pagination via `get_video_transcript` remains the exhaustive fallback. See [docs/INTERFACE.md](docs/INTERFACE.md).
* yt-dlp updates are opt-in (`YTDLP_AUTO_UPDATE` defaults to false). When enabled, downloads are SHA-256-verified, size-bounded, validated before promotion, staged versions are immutable, the previous version is retained for rollback, and update failures never remove the bundled fallback or affect server availability.

## Upstream behavior and failures

The MCP distinguishes its own tool interface from the upstream services it uses.

Official metadata and playlist operations normally use Google's **YouTube Data API v3** when an API key is configured. Public transcript discovery and retrieval may use `yt-dlp`, and selected operations can provide documented no-key fallbacks.

A transport-level failure such as:

```text
Network is unreachable
```

indicates that the MCP runtime could not reach an upstream service at that moment. It is different from a valid YouTube/API response such as an invalid video, missing playlist, quota error, or unavailable captions.

Clients may retry transient transport failures. The MCP does not silently reinterpret such failures as an empty playlist, missing video, or negative result.

Where useful, responses expose provenance describing which upstream source produced the result.

## Deliberate PoC limits

* Google OAuth for personal subscriptions is not included in v0.1. It needs a real per-user authorization broker, not a token pasted into the model.
* Transcript retrieval for third-party videos is necessarily unofficial. YouTube Data API v3 only permits caption download when the caller can edit the video.
* A public plugin submission needs a stable public HTTPS deployment. Developer-mode testing can use OpenAI Secure MCP Tunnel instead.
* The yt-dlp self-updater is an opt-in, unaudited-by-you code path intended for rapid recovery when YouTube changes break extraction. It is disabled by default, and the bundled image version remains the trusted baseline.
