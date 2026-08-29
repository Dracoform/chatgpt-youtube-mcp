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

1. Create a tunnel in [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels) and retain its `tunnel_id`.
2. Download the current `tunnel-client` from that page or the [official releases](https://github.com/openai/tunnel-client/releases/latest).
3. Follow `tunnel-client help quickstart`; initialize an HTTP profile pointing to `http://127.0.0.1:8765/mcp`.
4. Run `tunnel-client doctor --profile PROFILE --explain`, then keep `tunnel-client run --profile PROFILE` healthy.
5. Associate the tunnel with the ChatGPT workspace/account that will create the connection.

The tunnel host needs outbound HTTPS to `api.openai.com:443` and local access to the MCP endpoint. It needs no inbound Internet port.

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

