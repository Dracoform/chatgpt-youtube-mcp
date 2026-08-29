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
service = YouTubeService()
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


def _call(method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return getattr(service, method)(*args, **kwargs)
    except Exception as exc:
        return error_result(exc)


@mcp.tool(annotations=READ_ONLY)
def get_video(video: str) -> dict[str, Any]:
    """Get current metadata for one YouTube video. Input may be a video URL or video ID."""
    return _call("get_video", video)


@mcp.tool(annotations=READ_ONLY)
def list_caption_tracks(video: str) -> dict[str, Any]:
    """List manual tracks, original automatic tracks, and a bounded set of translation languages for a YouTube video."""
    return _call("list_caption_tracks", video)


@mcp.tool(annotations=READ_ONLY)
def get_video_transcript(
    video: str,
    languages: list[str] | None = None,
    include_timestamps: bool = True,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Get a caption-based transcript. Prefer requested languages in order; manual captions win over automatic captions."""
    return _call(
        "get_video_transcript",
        video,
        languages=languages,
        include_timestamps=include_timestamps,
        max_chars=max_chars,
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
