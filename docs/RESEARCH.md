# Research snapshot: ChatGPT extensions and YouTube

Status: 2026-08-29. This is a product snapshot, not a promise that account-level feature flags or workspace policy are identical everywhere.

## What works in current ChatGPT/OpenAI products

| Mechanism | What it is good for | Live external data by itself? | Relevant constraint |
| --- | --- | --- | --- |
| Skill | Instructions, references, templates, scripts, and repeatable workflow guidance | No | A skill can teach the model when and how to call tools, but it is not the network data source. |
| MCP server | Typed tools, structured results, authentication, controlled server-side behavior | Yes | ChatGPT needs a public HTTPS Streamable HTTP endpoint or Secure MCP Tunnel in developer mode. |
| Plugin | Installable bundle containing skills, an MCP server/connector, or both; optional UI | Yes, when MCP-backed | Public distribution requires review and a stable public HTTPS MCP endpoint. |
| Connector/App | Productized MCP-backed integration, often with OAuth and optional UI | Yes | Available services and permissions vary by account/workspace and surface. |
| Responses API MCP tool | An application attaches a remote MCP server with `server_url`, or an OpenAI connector with `connector_id` | Yes | The calling application handles OAuth tokens for connectors; approvals should be configured deliberately. |

OpenAI's current plugin architecture explicitly separates the workflow layer (skills) from the live capability layer (MCP). A plugin may contain either or both. ChatGPT and Codex share the plugin directory, although individual capabilities can remain surface-specific.

### Can ChatGPT call a custom MCP server?

Yes. In ChatGPT developer mode, create a plugin/app connection and provide either:

1. a public HTTPS URL ending in `/mcp`, using Streamable HTTP; or
2. a Secure MCP Tunnel ID for a server on a workstation, private network, or home lab.

Developer mode is enabled under Settings -> Security and login when the account/workspace policy permits it. The tunnel client needs only outbound HTTPS to `api.openai.com:443` and local reachability to the MCP server. It does not open an inbound firewall port. Secure MCP Tunnel is for private/developer use, not public plugin submission.

### Authentication distinctions

There are two independent authentication boundaries:

- **ChatGPT -> MCP bridge:** An MCP plugin can expose anonymous tools or implement MCP OAuth discovery. ChatGPT performs authorization-code + PKCE when the server advertises the required metadata and challenges.
- **MCP bridge -> YouTube/Google:** Public YouTube Data API calls use a server-side API key. Personal subscriptions require a Google OAuth token with YouTube scopes. A production multi-user bridge needs to broker and store each user's Google authorization server-side; the token must never appear in tool arguments or results.

In the Responses API, the application supplies the MCP `server_url`; when it passes an OAuth access token in `authorization`, the application owns that OAuth flow. This differs from ChatGPT's interactive MCP OAuth linking.

### Web, desktop, Work, Codex, and API

- **ChatGPT Web/Work:** Current documented connection flow is developer-mode Plugins/Apps. Workspace administrators can restrict developer mode, plugins, skills, and connectors.
- **ChatGPT desktop:** Uses the same account/plugin ecosystem, but OpenAI warns that individual capabilities can be surface-specific. Verify the connection in the exact client before treating desktop support as guaranteed.
- **Codex:** Shares plugin packages and can also use MCP in its own configuration. A skill may include coding workflow guidance around the same MCP tools.
- **API:** The Responses API can attach remote MCP servers directly. It supports Streamable HTTP and HTTP/SSE transports and can require approval per call/tool.

## YouTube source evaluation

| Requirement | Preferred source | Stability | Key/auth | Important limitation |
| --- | --- | --- | --- | --- |
| Video metadata | YouTube Data API `videos.list` | Official | API key | Quota; key must remain server-side. |
| Channel metadata | `channels.list` | Official | API key | Handle resolution is most reliable with the API. |
| Recent uploads | Public channel Atom feed | Public YouTube endpoint | None | Limited recent history; requires channel ID. |
| Search | `search.list` | Official | API key | Separate search quota/bucket; results are search resources, not full video resources. |
| Own subscriptions | `subscriptions.list?mine=true` | Official | Google OAuth | Optional phase 2; per-user token storage required. |
| Transcript of own/editable video | Captions API | Official | Google OAuth | `captions.download` requires permission to edit that video. |
| Transcript of arbitrary public video | `yt-dlp` caption discovery/download | Unofficial | Usually none | Can be rate-limited or broken by YouTube changes; no availability guarantee. |
| No-key metadata/search fallback | `yt-dlp` | Unofficial | None | Slower and less stable than Data API. |

The key finding is that the official Captions API cannot satisfy the central "summarize this arbitrary current video" use case. Listing/downloading another creator's public captions is not an officially supported general-purpose transcript API. The PoC therefore exposes provenance and labels `yt-dlp` results as unofficial rather than pretending all tool outputs have equal stability.

## Existing open-source MCP implementations reviewed

- [spinalshock/youtube-transcript-mcp](https://github.com/spinalshock/youtube-transcript-mcp): Go, stdio, transcript and basic metadata via `yt-dlp`.
- [Anarcyst/youtube-mcp-server](https://github.com/Anarcyst/youtube-mcp-server): broader Python server with Streamable HTTP, search/channels/transcripts, no key; explicitly uses internal APIs for transcripts.
- [akp-tools/yt-dlp-mcp](https://github.com/akp-tools/yt-dlp-mcp): Node server for subtitles, metadata, and comments across `yt-dlp` sites.
- [AliAlpOezer/youtube-mcp-server](https://github.com/AliAlpOezer/youtube-mcp-server): clean chapter-structured transcripts and metadata via `yt-dlp`.
- [dprothero/yt-transcript-mcp](https://github.com/dprothero/yt-transcript-mcp): TypeScript, URL validation, metadata and transcripts, stdio-first.

They demonstrate that the extraction path is viable, but none changes the official API limitation. This project uses its own small bridge because the design goal is explicit source priority, structured provenance, a read-only security boundary, Streamable HTTP for ChatGPT, and a provider architecture beyond YouTube.

## Primary references

- [OpenAI: Plugin architecture](https://developers.openai.com/plugins/concepts/plugins)
- [OpenAI: Skills](https://developers.openai.com/plugins/concepts/skills)
- [OpenAI: Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [OpenAI: Connect and test a plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [OpenAI: MCP and Connectors in the Responses API](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
- [OpenAI: Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Google: `videos.list`](https://developers.google.com/youtube/v3/docs/videos/list)
- [Google: `search.list`](https://developers.google.com/youtube/v3/docs/search/list)
- [Google: channel upload retrieval](https://developers.google.com/youtube/v3/guides/implementation/videos)
- [Google: `subscriptions.list`](https://developers.google.com/youtube/v3/docs/subscriptions/list)
- [Google: `captions.download`](https://developers.google.com/youtube/v3/docs/captions/download)

