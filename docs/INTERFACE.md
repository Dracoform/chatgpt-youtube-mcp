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

### `search_video_transcript`

Input:

```json
{
  "video":"VIDEO_ID",
  "queries":["persistent memory","long-term memory"],
  "languages":["de","en"],
  "limit":10,
  "context_before":10.0,
  "context_after":20.0
}
```

Deterministic **textual** transcript search: queries are matched as normalized substrings (Unicode NFKC + casefold + whitespace collapse). There is **no semantic, fuzzy, embedding, or LLM-based matching** — a query finds only text that literally contains it after normalization. Accepts multiple `queries` (always a list) in one call, e.g. several formulations of one topic.

Returns `matches`: candidate regions ordered chronologically, each with:

- `match_start`/`match_end` — start and end (seconds) of the matching transcript segment;
- `start`/`end` — locator window (`match_start - context_before` .. `match_end + context_after`, clipped to the transcript bounds) covered by the returned snippet;- `timestamp` — `HH:MM:SS` of the match;
- `text` — the actual transcript text of the segments overlapping the window (locator snippet, not an authoritative passage);
- `matched_queries` — the query strings (original spelling) that matched inside this region.

Overlapping or nearby hits are merged into one region, recording all contributing queries. `limit` (default 10, hard max 50) bounds returned regions; `results_truncated_by_limit` and `total_matches_before_limit` report when hits were cut. Error codes: `invalid_query`, `invalid_limit`, plus the transcript errors (`transcript_unavailable`, `transcript_empty`, `transcript_fetch_failed`).

**Semantics and limits — read before relying on results:**

- Search is textual, not semantic. Synonyms, paraphrases, or differently inflected forms are NOT found unless supplied as additional queries.
- Results are **locators**, not authoritative passages. Follow promising hits with a bounded `get_video_transcript(start=..., end=...)` call to read the actual passage before drawing conclusions.
- **Zero matches do not prove the topic is absent.** They only mean the supplied query strings were not found as substrings. For exhaustive review, page through the full transcript with `get_video_transcript` and its `pagination` continuation tokens (this remains available and authoritative).

### `get_playlist`

Input:

```json
{
  "playlist": "https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID&index=3"
}
```

Accepts one `playlist` string: a bare playlist ID with a `PL`, `UU`, `RD`, `OL`, `FL`, or `LL` prefix; a `youtube.com/playlist?list=...` URL; or a watch, shorts, embed (or youtu.be) URL carrying `list=` context. A bare video URL without `list=` is rejected; playlist membership is never inferred.

For a watch URL with `list=` context, `playlist_reference` includes the `video_id` from the URL and `url_index_context` from its `index` parameter. Both are context only: item positions come from playlist enumeration and are 1-based.

Returns `ok`, `playlist_reference`, `playlist`, ordered `items[]`, `pagination`, and `provenance`. `playlist` contains `playlist_id`, `title`, `description` (bounded to 1000 characters), `channel_id`, `channel_title`, `published_at`, `item_count`, and `item_count_reported`. Each item can contain `position`, `video_id`, `url`, `title`, `channel_title`, `channel_id`, `video_published_at`, `playlist_added_at`, `note`, `duration_seconds`, and `availability_note`; source-dependent fields are omitted when unavailable rather than invented. `pagination` contains `has_more`, `next_continuation`, `returned_items`, and `total_items`.

- **Official path (`YOUTUBE_API_KEY` configured):** calls Data API `playlists.list` and `playlistItems.list` with `part=snippet,contentDetails`, uses `maxResults <= 50`, follows `nextPageToken` automatically, and hard-bounds each call at 500 items.
- **No-key fallback:** runs `yt-dlp --flat-playlist` on the playlist URL. It provides fewer fields: no `video_published_at`, `playlist_added_at`, or `note`, and playlist `published_at` is `null`. Missing fields are omitted, never inferred.

The tools are deterministic, read-only, and idempotent. They enumerate explicit playlist data; there is no semantic search or LLM matching.

### `find_playlist_position`

Input:

```json
{
  "watch_url_with_list": "https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID&index=7"
}
```

Requires a watch, shorts, or embed URL containing **both** a video ID and `list=` playlist context. A bare video URL is rejected because membership cannot be inferred, and a bare playlist ID is insufficient because no target video was supplied.

Enumerates the playlist (within the 500-item call bound) and returns the target video's actual 1-based `position`. The URL's `index` is returned as `url_index_context` for comparison only and never determines the result. If enumeration does not contain the video, the tool returns `video_not_in_playlist`.

#### Playlist workflow

Call `get_playlist` to enumerate and select videos, then pass the returned video IDs to `search_video_transcript` and `get_video_transcript` as needed.

### `get_channel`

Input: `{"channel":"@handle"}` or a channel ID/`/channel/` URL.

Returns channel title, description, counts, uploads playlist ID, and thumbnails when the official API is configured. No-key fallback returns a smaller shape.

### `get_recent_uploads`

Input: `{"channel":"CHANNEL_ID_OR_HANDLE","limit":10}`

Returns the newest public feed entries. Limit is clamped to 1..50. A handle requires resolution, which is most reliable with the API key.

### `search_videos`

Input: `{"query":"local AI benchmark","limit":10,"order":"date"}`

Official mode accepts YouTube's supported order values. Limit is clamped to 1..25. Without an API key, the response is marked as `yt_dlp_search` and unofficial.

### `get_ytdlp_updater_status`

Input: none.

Read-only diagnostics about the optional staged yt-dlp self-updater. The tool is annotated read-only and makes no network requests; it only reports the updater's current configuration and the outcome of the last update check.

```json
{
  "ok": true,
  "updater": {
    "enabled": false,
    "channel": "stable",
    "interval_seconds": 86400,
    "state_dir": "/app/state/ytdlp",
    "active_version": "bundled",
    "previous_version": null,
    "versions_available": [],
    "smoke_test": false,
    "last_check_iso8601": null,
    "last_check_result": null,
    "last_error": null,
    "status_note": "updater initialized"
  }
}
```

Field semantics:

- `enabled` — whether auto-update is on. **Default OFF** (`YTDLP_AUTO_UPDATE` defaults to false). When disabled, the tool simply reports the bundled yt-dlp (installed in the image's site-packages) as `active_version: "bundled"` and does nothing else.
- `channel` — `stable` (default) or `nightly`; `nightly` is opt-in. An invalid value falls back to `stable` rather than failing startup.
- `interval_seconds` — seconds between background update checks (default 86400, minimum 60).
- `state_dir` — writable directory holding the immutable staged versions and the `active`/`previous` pointer files (default `/app/state/ytdlp`).
- `active_version` — the yt-dlp version currently serving requests: `bundled` (site-packages) or a staged version.
- `previous_version` — the previous version retained for rollback, or `null` when none was recorded.
- `versions_available` — sorted list of staged versions on disk.
- `smoke_test` — whether promotion additionally runs a small live YouTube check (`YTDLP_UPDATE_SMOKE`, default false).
- `last_check_iso8601` / `last_check_result` / `last_error` / `status_note` — the time and outcome of the last update check (`up_to_date`, `promoted`, `metadata_failed`, `validation_failed`, `rolled_back`, `update_error`, or `null` before the first check).

When enabled, the updater runs in a background daemon thread (never blocking startup): it fetches release metadata, downloads the wheel, verifies its SHA-256, extracts it into an immutable version directory, validates it (`python -m yt_dlp --version`), and then atomically promotes it via the `active` pointer. The bundled site-packages copy is the always-available fallback. Any failure is logged and non-fatal: the current active version is untouched and the server keeps serving. This tool is operator-facing; the model does not need to call it for normal usage.

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

Representative codes: `invalid_video_reference`, `invalid_channel_reference`, `invalid_playlist_reference`, `playlist_not_found`, `video_not_in_playlist`, `api_key_required`, `video_not_found`, `channel_not_found`, `transcript_unavailable`, `transcript_empty`, `transcript_fetch_failed`, `upstream_request_failed`, `ytdlp_failed`, `ytdlp_timeout`, `ytdlp_invalid_output`, `sign_in_required`, `rate_limited`, and `unofficial_fallback_disabled`.

#### Failure vs. absence

A **retrieval/extraction failure is never reported as evidence that captions do not exist.** `transcript_unavailable` is raised only after a *successful* extraction found no caption tracks. If extraction itself failed, the bridge returns one of the classified errors below instead, so a broken yt-dlp or a YouTube-side outage can never look like a video that simply has no captions:

- `ytdlp_failed` — yt-dlp ran but failed (default, retryable). The `message` carries the last stderr line from yt-dlp.
- `ytdlp_timeout` — yt-dlp did not finish within its 90-second timeout (retryable).
- `ytdlp_invalid_output` — yt-dlp exited zero but produced JSON that is malformed, missing the expected `id`, or not an object; the result cannot be trusted as an absence signal (retryable).
- `sign_in_required` — yt-dlp hit a sign-in/bot-confirmation wall (not retryable; the condition is permanent until an operator intervenes).
- `rate_limited` — yt-dlp reported HTTP 429/too-many-requests (retryable).
- `transcript_fetch_failed` — caption tracks were listed but every download failed; when the underlying failure is itself one of the classified codes (e.g. `upstream_request_failed` from a network error), that original code and retryability are preserved rather than collapsed.

Within `get_video_transcript`, demanding a time range that contains no segments returns `ok: true` with a valid `transcript_unavailable`-free empty result (`segment_count: 0`, empty transcript) — that is a *range-empty* condition, not an extraction failure.

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

Note: `get_ytdlp_updater_status` is an operator/observability tool for the optional yt-dlp self-updater. The model does not need to call it for normal usage; use it only when explicitly asked about the server's yt-dlp version or updater state.

Example: for a 3:52:14 video and a request about "the last 30 minutes", retrieve approximately `start: "03:22:14", end: "03:52:14"` rather than paging through the transcript from `00:00`.

