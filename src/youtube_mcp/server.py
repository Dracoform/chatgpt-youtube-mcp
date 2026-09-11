from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core import YouTubeService, error_result


mcp = FastMCP(
    "YouTube Current Data (read-only)",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8765")),
    stateless_http=True,
    json_response=True,
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Any) -> Any:
    """HTTP readiness endpoint on the MCP port itself.

    Served by the same uvicorn listener that serves /mcp, so a 200 response
    proves that port 8765 is accepting connections and the application is
    up — not merely that a process exists. Docker HEALTHCHECK and the
    generated Compose `service_healthy` dependency use this endpoint.
    """
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "service": "youtube-current-data-mcp"})


service = YouTubeService()
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


def _call(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return getattr(service, method)(*args, **kwargs)
    except Exception as exc:
        return error_result(exc)


@mcp.tool(annotations=READ_ONLY)
def get_video(video: str) -> dict[str, Any]:
    """Get current metadata for one YouTube video. Input may be a video URL or video ID.

    Caption semantics: `youtube_api_caption_flag` mirrors the official
    YouTube Data API `caption` field, which only reports MANUAL caption
    tracks. An official false value is reported as
    `caption_available: null` (unknown) — it must NOT be interpreted as
    proof that captions do not exist, because automatic (ASR) tracks can
    still be present and are invisible to this flag. `list_caption_tracks`
    is authoritative for manual AND automatic caption discovery; call it
    before concluding that no captions exist. Call `get_video_transcript`
    when transcript retrieval is requested.
    """
    return _call("get_video", video)


@mcp.tool(annotations=READ_ONLY)
def list_caption_tracks(video: str) -> dict[str, Any]:
    """List manual tracks, original automatic tracks, and a bounded set of translation languages for a YouTube video.

    This is the authoritative caption-discovery operation: unlike the
    official API caption flag, it reports manual AND automatic (ASR)
    tracks via yt-dlp.
    """
    return _call("list_caption_tracks", video)


@mcp.tool(annotations=READ_ONLY)
def get_video_transcript(
    video: str,
    languages: list[str] | None = None,
    include_timestamps: bool = True,
    max_chars: int | None = None,
    start: float | str | None = None,
    end: float | str | None = None,
    continuation: str | None = None,
) -> dict[str, Any]:
    """Get a caption-based transcript. Prefer requested languages in order; manual captions win over automatic captions.

    Ranges: `start`/`end` (seconds, "MM:SS" or "HH:MM:SS") select segments
    whose start lies in [start, end). For long transcripts, pass `continuation`
    from a previous response's `pagination.next_continuation` (it overrides
    start/end) to fetch the next page. The response's `pagination` object
    reports `has_more`, `next_start`, `next_continuation`, returned/total
    segments and chars, and the effective range; when `pagination.has_more`
    is true, call again with `start=next_start` or `continuation=next_continuation`.
    Server caps `max_chars` at YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS (default 120000).
    """
    return _call(
        "get_video_transcript",
        video,
        languages=languages,
        include_timestamps=include_timestamps,
        max_chars=max_chars,
        start=start,
        end=end,
        continuation=continuation,
    )


@mcp.tool(annotations=READ_ONLY)
def search_video_transcript(
    video: str,
    queries: list[str],
    languages: list[str] | None = None,
    limit: int = 10,
    context_before: float = 10.0,
    context_after: float = 20.0,
) -> dict[str, Any]:
    """Search a video transcript for query terms with deterministic textual substring matching (NOT semantic search).

    Pass one or more `queries` (a list, e.g. ["persistent memory", "long-term
    memory"]). Matching normalizes case, Unicode compatibility forms, and
    whitespace, but performs no fuzzy or semantic matching: only exact
    normalized substrings are found. Returns candidate regions (locators)
    with `match_start`/`match_end` (the matching segment), `start`/`end`
    (context in seconds around the match), `timestamp`, a locator snippet
    `text`, and `matched_queries`. Results are merged/deduplicated and
    ordered chronologically; `limit` (default 10, max 50) bounds the number
    of returned regions.

    IMPORTANT: results are LOCATORS, not authoritative passages. Follow
    promising hits with bounded `get_video_transcript(start=..., end=...)`
    to read the actual passage. Zero matches only means these query strings
    were not found — it does NOT prove the topic is absent; use full
    transcript pagination via `get_video_transcript` as the exhaustive
    fallback.
    """
    return _call(
        "search_video_transcript",
        video,
        queries=queries,
        languages=languages,
        limit=limit,
        context_before=context_before,
        context_after=context_after,
    )


@mcp.tool(annotations=READ_ONLY)
def get_channel(channel: str) -> dict[str, Any]:
    """Get current information for a YouTube channel ID, /channel/ URL, or @handle."""
    return _call("get_channel", channel)


@mcp.tool(annotations=READ_ONLY)
def get_recent_uploads(channel: str, limit: int = 10) -> dict[str, Any]:
    """Get the newest public uploads from a YouTube channel. Limit is clamped to 1..50."""
    return _call("get_recent_uploads", channel, limit=limit)


@mcp.tool(annotations=READ_ONLY)
def search_videos(query: str, limit: int = 10, order: str = "relevance") -> dict[str, Any]:
    """Search YouTube videos. Official Data API is used when configured; otherwise an explicitly marked yt-dlp fallback is used."""
    return _call("search_videos", query, limit=limit, order=order)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    if transport not in {"streamable-http", "stdio", "sse"}:
        raise SystemExit("MCP_TRANSPORT must be streamable-http, stdio, or sse")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
