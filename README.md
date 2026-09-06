# YouTube Current Data MCP

Read-only MCP bridge for current YouTube metadata, captions, channels, recent uploads, and search. It is deliberately a small proof of concept for the path:

`ChatGPT -> MCP -> YouTube -> structured data -> ChatGPT`

The bridge uses the official YouTube Data API v3 when possible, the public channel Atom feed for recent uploads, and clearly marked `yt-dlp` fallbacks for public transcripts and no-key operation.

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

Status: the wrapper implementation is prepared and tested without production credentials. The public image and complete ChatGPT-to-YouTube path are not yet claimed as validated. See [docs/OPENAI_SECURE_MCP_TUNNEL.md](docs/OPENAI_SECURE_MCP_TUNNEL.md) and [docs/OPENAI_MCP_TUNNEL_IMAGE_RESEARCH.md](docs/OPENAI_MCP_TUNNEL_IMAGE_RESEARCH.md).

## Quick start

```bash
cp .env.example .env
# Add YOUTUBE_API_KEY to .env for official metadata/channel/search calls.
# How to obtain one: docs/YOUTUBE_API_KEY.md
docker compose up --build -d
```

The Streamable HTTP endpoint is `http://127.0.0.1:8765/mcp`.

For a Python development environment:

```bash
uv sync
uv run python -m unittest discover -s tests -v
uv run python scripts/live_smoke.py 'https://www.youtube.com/watch?v=VIDEO_ID'
uv run youtube-current-data-mcp
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INTERFACE.md](docs/INTERFACE.md), [docs/YOUTUBE_API_KEY.md](docs/YOUTUBE_API_KEY.md), and [docs/CHATGPT_SETUP.md](docs/CHATGPT_SETUP.md).

For a guided Portainer Web Editor stack, use either `generators/generate_docker-compose_for_ChatGPT_MCP.sh` on Bash or `generators/generate_docker-compose_for_ChatGPT_MCP.ps1` on PowerShell. Both scripts only generate YAML; they install and start nothing. See [docs/PORTAINER_STACK_GENERATORS.md](docs/PORTAINER_STACK_GENERATORS.md).

## Security boundary

- Every MCP tool is annotated read-only and idempotent.
- There are no post, comment, subscribe, delete, upload, or account-modification tools.
- API keys remain server-side environment variables and are never returned.
- Arbitrary URLs are rejected; only recognized YouTube URL shapes are accepted.
- Tool output and transcript size are bounded.

## Deliberate PoC limits

- Google OAuth for personal subscriptions is not included in v0.1. It needs a real per-user authorization broker, not a token pasted into the model.
- Transcript retrieval for third-party videos is necessarily unofficial. YouTube Data API v3 only permits caption download when the caller can edit the video.
- A public plugin submission needs a stable public HTTPS deployment. Developer-mode testing can use OpenAI Secure MCP Tunnel instead.
