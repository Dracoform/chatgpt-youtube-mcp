# Connect and test with ChatGPT

## 1. Run the MCP server

```bash
cp .env.example .env
# Put a YouTube Data API v3 key in .env if available.
docker compose up --build -d
```

The local endpoint is `http://127.0.0.1:8765/mcp`. Confirm it with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector@latest
```

Choose Streamable HTTP and enter the endpoint above. Verify initialization, the six tools, schemas, read-only annotations, valid calls, and invalid-reference errors.

## 2A. Recommended private/home-lab connection

Use OpenAI Secure MCP Tunnel so no inbound port or public reverse proxy is needed.

Instead of running `tunnel-client` by hand, deploy the wrapper container from this project (see `docs/OPENAI_SECURE_MCP_TUNNEL.md` and the Portainer stack generators). It wraps the official `ghcr.io/openai/tunnel-client` image (Apache-2.0, pinned by digest) and is configured entirely through environment variables:

1. Create a tunnel in [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels) and retain its `tunnel_id`.
2. Create a Runtime API key under [Runtime API keys](https://platform.openai.com/settings/organization/api-keys) (the tunnel needs Tunnels Read + Use permission).
3. Provide the stack environment:
   - `CONTROL_PLANE_TUNNEL_ID` — the official variable name for the tunnel ID (the wrapper also accepts the legacy alias `OPENAI_TUNNEL_ID`, but generated stacks use the official name);
   - `CONTROL_PLANE_API_KEY` — the Runtime API key (environment variable only; it never appears in command-line arguments or logs);
   - `MCP_SERVER_URL=http://youtube-mcp:8765/mcp` — the internal Streamable HTTP endpoint over the Compose network.
4. The tunnel host needs outbound HTTPS to `api.openai.com:443` and internal access to the MCP endpoint. It needs no inbound Internet port and no published host port.

Manual alternative (bare binary on a workstation): download `tunnel-client` from the [official releases](https://github.com/openai/tunnel-client/releases/latest), run `tunnel-client help quickstart`, initialize an HTTP profile pointing to `http://127.0.0.1:8765/mcp`, then `tunnel-client doctor --profile PROFILE --explain` and `tunnel-client run --profile PROFILE`.
5. Associate the tunnel with the ChatGPT workspace/account that will create the connection.

Note: the wrapper container's Docker healthcheck verifies process health only (`/healthz`). It does not prove control-plane authentication or end-to-end MCP readiness — see `docs/OPENAI_SECURE_MCP_TUNNEL.md`.

## 2B. Public HTTPS connection

Deploy the container behind a stable HTTPS origin and expose `/mcp` with Streamable HTTP. Do this only with proper authentication, rate limiting, secret management, logs that exclude credentials/results, and a maintained domain. A temporary forwarding URL is acceptable for development but not plugin submission.

## 3. Add it in ChatGPT

1. Settings -> Security and login -> enable Developer mode (if permitted by the account/workspace).
2. Open ChatGPT Plugins/Apps and select the plus button.
3. Enter a name such as `YouTube Current Data` and a short read-only description.
4. Choose either Tunnel and the tunnel ID, or Public endpoint and the full HTTPS `/mcp` URL.
5. Review the six discovered tools and confirm the annotations.
6. Start a new conversation and enable the connection from the tools menu.

## 4. End-to-end evaluation

Use a recent public video that has captions:

> Schau dir dieses aktuelle YouTube-Video an und sag mir, was daran interessant ist: https://www.youtube.com/watch?v=-ulRSvQIObk

Expected behavior:

1. ChatGPT calls `get_video`.
2. It calls `get_video_transcript` with English/German preferences.
3. It distinguishes metadata/description from spoken transcript evidence.
4. It notes if captions were automatic.

Also test:

- a video without captions;
- a private/deleted ID;
- `https://example.org/watch?v=...` (must be rejected);
- a channel ID with `get_recent_uploads`;
- search with and without `YOUTUBE_API_KEY`;
- `YOUTUBE_ENABLE_YTDLP=false` to confirm the unofficial path is policy-disableable.

## 5. Responses API alternative

An API application can attach the same public/tunneled remote MCP server as an `mcp` tool using its `server_url`. Configure allowed tools and approval behavior in the application. If authentication is added, the application must manage the OAuth access token passed to the API; do not put YouTube credentials into prompts.

