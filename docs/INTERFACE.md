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
  "max_chars":40000,
  "start":null,
  "end":null,
  "continuation":null
}
```

Language order is a preference. The bridge tries preferred manual/automatic tracks, original automatic tracks, then other available tracks. Server policy caps `max_chars` even if a larger value is requested (hard cap: `YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS`, default 120000; default page size `YOUTUBE_TRANSCRIPT_MAX_CHARS`, default 60000). Returns selected language, whether it is automatic, transcript text, segment count, truncation state, and a `pagination` object.

#### Ranges and pagination

- `start`/`end` select segments whose start lies in `[start, end)` (half-open; `end` is exclusive). Accepted forms: seconds (`90`, `"90.5"`), `"MM:SS"`, `"HH:MM:SS"` (fractional seconds allowed, e.g. `"1:02.5"`).
- Truncation always breaks at a segment boundary. The `pagination` object reports: `has_more`, `next_start` (seconds; start for the next call), `next_continuation` (opaque token), `returned_segments`/`total_segments`, `returned_chars`/`total_chars` (chars of the selected range), `range_start`/`range_end`.
- To page through a long transcript, repeat the call with `start: <pagination.next_start>` (or simply pass `continuation: <pagination.next_continuation>`, which overrides `start`/`end`) until `pagination.has_more` is `false`. No content is repeated between pages; pages are line-aligned, so the full range text is `"\n".join(page.transcript for each page)` and `returned_chars + <number of page breaks>` equals `total_chars`.
- New error codes: `invalid_timestamp`, `invalid_time_range`, `invalid_continuation`.
- Backward compatibility: omitting the new parameters behaves exactly as before; `segment_count` now counts the segments actually returned; a new `pagination` object is always present.

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

Use the smallest transcript range that answers the user's question. Do not retrieve earlier portions of a long video merely to reach a later section.

For "Schau dir dieses Video an und sag mir, was interessant ist":

1. Call `get_video` for identity, duration, description, chapters, and current metadata.
2. Call `get_video_transcript` because the user's question concerns the video content, not just its description.
3. When the request identifies a specific section, timestamp, chapter, or relative portion such as "the last 30 minutes", use the video's duration/chapters to determine the relevant range and pass `start`/`end` to `get_video_transcript`.
4. If `pagination.has_more` is `true` and more of the selected range is needed, continue with `pagination.next_continuation`. Do not restart transcript retrieval from the beginning.
5. Base substantive claims on transcript text; identify when only metadata/description was available.
6. Mention automatic-caption uncertainty when `automatic=true`.
7. Do not infer that a transcript is creator-approved merely because it exists.

Example: for a 3:52:14 video and a request about "the last 30 minutes", retrieve approximately `start: "03:22:14", end: "03:52:14"` rather than paging through the transcript from `00:00`.

