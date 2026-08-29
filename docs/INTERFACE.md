# MCP interface v0.1

All tools return JSON-compatible structured data. Every successful result has `ok: true` and `provenance`; failures have `ok: false` and a stable error object.

## Tools

### `get_video`

Input:

```json
{"video":"https://www.youtube.com/watch?v=VIDEO_ID"}
```

Returns title, description, channel, publication time, duration, counts when available, tags, thumbnails, and chapters when available. A bare 11-character video ID is accepted.

### `list_caption_tracks`

Input: `{"video":"VIDEO_ID"}`

Returns manual tracks, original automatic tracks, and a bounded list/count of automatically translated languages. Caption discovery is unofficial.

### `get_video_transcript`

Input:

```json
{
  "video":"VIDEO_ID",
  "languages":["de","en"],
  "include_timestamps":true,
  "max_chars":40000
}
```

Language order is a preference. The bridge tries preferred manual/automatic tracks, original automatic tracks, then other available tracks. Server policy caps `max_chars` even if a larger value is requested. Returns selected language, whether it is automatic, transcript text, segment count, and truncation state.

### `get_channel`

Input: `{"channel":"@handle"}` or a channel ID/`/channel/` URL.

Returns channel title, description, counts, uploads playlist ID, and thumbnails when the official API is configured. No-key fallback returns a smaller shape.

### `get_recent_uploads`

Input: `{"channel":"CHANNEL_ID_OR_HANDLE","limit":10}`

Returns the newest public feed entries. Limit is clamped to 1..50. A handle requires resolution, which is most reliable with the API key.

### `search_videos`

Input: `{"query":"local AI benchmark","limit":10,"order":"date"}`

Official mode accepts YouTube's supported order values. Limit is clamped to 1..25. Without an API key, the response is marked as `yt_dlp_search` and unofficial.

## Error envelope

```json
{
  "ok": false,
  "error": {
    "code": "transcript_unavailable",
    "message": "No manual or automatic captions are available for this video.",
    "retryable": false
  }
}
```

Representative codes: `invalid_video_reference`, `invalid_channel_reference`, `api_key_required`, `video_not_found`, `channel_not_found`, `transcript_unavailable`, `transcript_fetch_failed`, `upstream_request_failed`, `ytdlp_failed`, and `unofficial_fallback_disabled`.

## Recommended model workflow

For "Schau dir dieses Video an und sag mir, was interessant ist":

1. Call `get_video` for identity, description, chapters, and current metadata.
2. Call `get_video_transcript` because the user's question concerns the video content, not just its description.
3. Base substantive claims on transcript text; identify when only metadata/description was available.
4. Mention automatic-caption uncertainty when `automatic=true`.
5. Do not infer that a transcript is creator-approved merely because it exists.

