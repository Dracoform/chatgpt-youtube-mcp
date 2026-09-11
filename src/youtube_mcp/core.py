from __future__ import annotations

import base64
import binascii
import html
import json
import math
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


class YouTubeBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class Settings:
    api_key: str | None = None
    enable_ytdlp: bool = True
    transcript_max_chars: int = 60_000
    default_languages: tuple[str, ...] = ("de", "en")
    transcript_hard_max_chars: int = 120_000

    @classmethod
    def from_env(cls) -> "Settings":
        raw_enabled = os.getenv("YOUTUBE_ENABLE_YTDLP", "true").strip().lower()
        transcript_max_chars = int(os.getenv("YOUTUBE_TRANSCRIPT_MAX_CHARS", "60000"))
        transcript_hard_max_chars = max(
            int(os.getenv("YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS", "120000")),
            1_000,
        )
        return cls(
            api_key=os.getenv("YOUTUBE_API_KEY") or None,
            enable_ytdlp=raw_enabled in {"1", "true", "yes", "on"},
            transcript_max_chars=min(transcript_max_chars, transcript_hard_max_chars),
            transcript_hard_max_chars=transcript_hard_max_chars,
            default_languages=tuple(
                language.strip()
                for language in os.getenv("YOUTUBE_DEFAULT_LANGUAGES", "de,en").split(",")
                if language.strip()
            ) or ("de", "en"),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def provenance(source: str, *, official: bool, note: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": source,
        "official": official,
        "retrieved_at": utc_now(),
    }
    if note:
        result["note"] = note
    return result


def parse_video_id(value: str) -> str:
    candidate = value.strip()
    if VIDEO_ID_RE.fullmatch(candidate):
        return candidate
    parsed = urllib.parse.urlparse(candidate if "://" in candidate else f"https://{candidate}")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_YOUTUBE_HOSTS:
        raise YouTubeBridgeError("invalid_video_reference", "Expected a YouTube URL or 11-character video ID.")
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path == "/watch":
        video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        video_id = parts[1] if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"} else ""
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise YouTubeBridgeError("invalid_video_reference", "The URL does not contain a valid YouTube video ID.")
    return video_id


def parse_channel_reference(value: str) -> dict[str, str]:
    candidate = value.strip()
    if CHANNEL_ID_RE.fullmatch(candidate):
        return {"kind": "id", "value": candidate}
    if candidate.startswith("@") and len(candidate) > 1:
        return {"kind": "handle", "value": candidate[1:]}
    parsed = urllib.parse.urlparse(candidate if "://" in candidate else f"https://{candidate}")
    if (parsed.hostname or "").lower() not in ALLOWED_YOUTUBE_HOSTS:
        raise YouTubeBridgeError("invalid_channel_reference", "Expected a YouTube channel URL, channel ID, or @handle.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "channel" and CHANNEL_ID_RE.fullmatch(parts[1]):
        return {"kind": "id", "value": parts[1]}
    if parts and parts[0].startswith("@"):
        return {"kind": "handle", "value": parts[0][1:]}
    raise YouTubeBridgeError("invalid_channel_reference", "Use a channel ID, /channel/ URL, or @handle.")


def _seconds_to_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


_TIMESTAMP_PART = r"[0-9]{1,2}"
_TIMESTAMP_RE = re.compile(
    rf"^(?P<hours>{_TIMESTAMP_PART}):(?P<minutes>{_TIMESTAMP_PART}):"
    rf"(?P<seconds>{_TIMESTAMP_PART})(?:\.(?P<fraction>[0-9]+))?$|"
    rf"^(?P<minutes_only>{_TIMESTAMP_PART}):"
    rf"(?P<seconds_only>{_TIMESTAMP_PART})(?:\.(?P<fraction_only>[0-9]+))?$"
)


def parse_timestamp(value: float | int | str) -> float:
    """Parse a non-negative transcript timestamp expressed in seconds."""
    if isinstance(value, bool):
        raise YouTubeBridgeError("invalid_timestamp", f"invalid_timestamp: {value!r}")

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            raise YouTubeBridgeError("invalid_timestamp", f"invalid_timestamp: {value!r}")
        try:
            if ":" not in candidate:
                seconds = float(candidate)
            else:
                match = _TIMESTAMP_RE.fullmatch(candidate)
                if not match:
                    raise ValueError
                groups = match.groupdict()
                hours = int(groups["hours"] or 0)
                minutes = int(groups["minutes"] or groups["minutes_only"])
                seconds_part = groups["seconds"] or groups["seconds_only"]
                fraction = groups["fraction"] or groups["fraction_only"] or ""
                seconds = hours * 3600 + minutes * 60 + float(f"{seconds_part}.{fraction}" if fraction else seconds_part)
                if minutes > 59 or float(seconds_part) > 59:
                    raise ValueError
        except ValueError as exc:
            raise YouTubeBridgeError("invalid_timestamp", f"invalid_timestamp: {value!r}") from exc
    elif isinstance(value, (int, float)):
        seconds = float(value)
    else:
        raise YouTubeBridgeError("invalid_timestamp", f"invalid_timestamp: {value!r}")

    if not math.isfinite(seconds) or seconds < 0:
        raise YouTubeBridgeError("invalid_timestamp", f"invalid_timestamp: {value!r}")
    return seconds


_CONTINUATION_PREFIX = "tr1."


def encode_continuation(start_seconds: float, end_seconds: float | None) -> str:
    """Encode an opaque transcript range token."""
    payload = {
        "v": 1,
        "s": float(start_seconds),
        "e": None if end_seconds is None else float(end_seconds),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return _CONTINUATION_PREFIX + encoded


def decode_continuation(token: str) -> dict[str, float | None]:
    """Decode and validate a transcript range token."""
    try:
        if not isinstance(token, str) or not token.startswith(_CONTINUATION_PREFIX):
            raise ValueError
        encoded = token[len(_CONTINUATION_PREFIX):]
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or set(payload) != {"v", "s", "e"}
            or isinstance(payload["s"], bool)
            or isinstance(payload["e"], bool)
            or not isinstance(payload["s"], (int, float))
            or payload["e"] is not None and not isinstance(payload["e"], (int, float))
        ):
            raise ValueError
        start_seconds = float(payload["s"])
        end_seconds = None if payload["e"] is None else float(payload["e"])
        if (
            not math.isfinite(start_seconds)
            or start_seconds < 0
            or end_seconds is not None
            and (not math.isfinite(end_seconds) or end_seconds < 0)
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise YouTubeBridgeError("invalid_continuation", "invalid_continuation: malformed transcript token") from exc

    return {"start_seconds": start_seconds, "end_seconds": end_seconds}


def parse_json3_transcript(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data)
    segments: list[dict[str, Any]] = []
    previous = None
    for event in payload.get("events", []):
        pieces = event.get("segs") or []
        text = "".join(piece.get("utf8", "") for piece in pieces)
        text = html.unescape(text).replace("\n", " ").strip()
        if not text or text == previous:
            continue
        start = float(event.get("tStartMs", 0)) / 1000
        duration = float(event.get("dDurationMs", 0)) / 1000
        segments.append({"start_seconds": start, "duration_seconds": duration, "text": text})
        previous = text
    return segments


def normalize_search_text(value: str) -> str:
    """Deterministic normalization for transcript text search.

    Unicode compatibility decomposition + casefold + whitespace collapse,
    so that matching is insensitive to case and insignificant spacing
    differences. No stemming, no fuzzy behavior.
    """
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.casefold()
    normalized = unicodedata.normalize("NFKC", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


PLAYLIST_ID_RE = re.compile(r"^(PL|UU|RD|OL|FL|LL)[A-Za-z0-9_-]{10,}$")


def parse_playlist_reference(value: str) -> dict[str, Any]:
    """Parse a playlist ID or a YouTube URL that carries playlist context.

    Accepts:
    - a bare playlist ID (PL..., UU..., RD..., OL..., FL..., LL...);
    - a /playlist?list=... URL;
    - a watch URL with list= context (the video ID, when present and valid,
      is returned as ``video_id`` so callers can pass it to the existing
      video tools);
    - youtu.be, shorts/live/embed URLs with list= context.

    The URL ``index`` parameter, when present, is returned only as
    contextual information (``url_index``); the authoritative position
    comes from playlist enumeration.

    A bare video URL without list= context is deliberately NOT accepted:
    playlist membership must not be inferred without explicit list context.
    """
    candidate = value.strip()
    if PLAYLIST_ID_RE.fullmatch(candidate):
        return {"kind": "id", "playlist_id": candidate, "video_id": None, "url_index": None}
    parsed = urllib.parse.urlparse(candidate if "://" in candidate else f"https://{candidate}")
    if (parsed.hostname or "").lower() not in ALLOWED_YOUTUBE_HOSTS:
        raise YouTubeBridgeError(
            "invalid_playlist_reference",
            "Expected a YouTube playlist URL, a playlist ID, or a YouTube URL with list= context.",
        )
    query = urllib.parse.parse_qs(parsed.query)
    # Tolerate a trailing slash (e.g. /watch/?v=...) without losing context.
    path = parsed.path.rstrip("/") or "/"
    parts = [part for part in parsed.path.split("/") if part]
    playlist_id = ""
    if path == "/playlist":
        playlist_id = query.get("list", [""])[0]
    else:
        playlist_id = query.get("list", [""])[0]
        if not playlist_id:
            raise YouTubeBridgeError(
                "invalid_playlist_reference",
                "The URL does not contain list= playlist context; playlist membership cannot be inferred from a bare video URL.",
            )
    if not PLAYLIST_ID_RE.fullmatch(playlist_id):
        raise YouTubeBridgeError(
            "invalid_playlist_reference",
            f"The URL list parameter is not a recognized playlist ID: {playlist_id!r}.",
        )
    video_id = ""
    if path == "/watch":
        video_id = query.get("v", [""])[0]
    elif parsed.path == "/embed" or (parts and parts[0] in {"embed"}):
        video_id = parts[1] if len(parts) >= 2 else ""
    elif host_is_short_link(parsed.hostname):
        video_id = parts[0] if parts else ""
    elif parts and parts[0] in {"shorts", "live"}:
        video_id = parts[1] if len(parts) >= 2 else ""
    if video_id and not VIDEO_ID_RE.fullmatch(video_id):
        video_id = ""
    raw_index = query.get("index", [None])[0]
    url_index: int | None = None
    if raw_index is not None:
        try:
            url_index = int(raw_index)
            if url_index < 1:
                url_index = None
        except ValueError:
            url_index = None
    return {"kind": "url", "playlist_id": playlist_id, "video_id": video_id or None, "url_index": url_index}


def host_is_short_link(hostname: str | None) -> bool:
    return bool(hostname) and hostname.endswith("youtu.be")  # type: ignore[union-attr]


class YouTubeService:
    API_ROOT = "https://www.googleapis.com/youtube/v3"
    FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        ytdlp_env_extra: Callable[[], dict[str, str] | None] | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._urlopen = urlopen
        self._run = run
        self._ytdlp_env_extra = ytdlp_env_extra
        self._video_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _get_bytes(self, url: str, *, timeout: int = 25) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "youtube-current-data-mcp/0.1"})
        try:
            with self._urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            raise YouTubeBridgeError("upstream_request_failed", f"Upstream request failed: {exc}", retryable=True) from exc

    def _api_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.api_key:
            raise YouTubeBridgeError("api_key_required", "This operation requires YOUTUBE_API_KEY.")
        query = {**params, "key": self.settings.api_key}
        url = f"{self.API_ROOT}/{path}?{urllib.parse.urlencode(query)}"
        return json.loads(self._get_bytes(url))

    def _yt_dlp_json(self, target: str, *, flat: bool = False, playlist_end: int | None = None) -> dict[str, Any]:
        if not self.settings.enable_ytdlp:
            raise YouTubeBridgeError("unofficial_fallback_disabled", "yt-dlp fallback is disabled by policy.")
        command = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", "--quiet", "--no-warnings"]
        # yt-dlp otherwise prefers certifi and can ignore an operator-supplied
        # corporate CA bundle. no-certifi makes it use Python/system TLS settings;
        # certificate verification remains enabled.
        if os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE"):
            command += ["--compat-options", "no-certifi"]
        if flat:
            command.append("--flat-playlist")
        if playlist_end is not None:
            command += ["--playlist-end", str(playlist_end)]
        command.append(target)
        # A staged yt-dlp version (if any active layer is installed) takes
        # effect via PYTHONPATH on this subprocess, so requests resolve the
        # promoted copy without changing the interpreter or site-packages.
        extra_env = self._ytdlp_env_extra() if self._ytdlp_env_extra else None
        run_kwargs = {}
        if extra_env:
            merged = {**os.environ, **extra_env}
            run_kwargs["env"] = merged
        try:
            completed = self._run(command, capture_output=True, text=True, timeout=90, check=False, **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            raise YouTubeBridgeError("ytdlp_timeout", "yt-dlp timed out while extracting.", retryable=True) from exc
        except Exception as exc:
            raise YouTubeBridgeError("ytdlp_failed", f"yt-dlp execution failed: {exc}", retryable=True) from exc
        if completed.returncode != 0:
            combined = (completed.stderr or completed.stdout or "").strip()
            lines = combined.splitlines()
            detail = lines[-1] if lines else "yt-dlp failed"
            code, retryable = self._classify_ytdlp_failure(combined)
            raise YouTubeBridgeError(code, detail, retryable=retryable)
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise YouTubeBridgeError("ytdlp_invalid_output", "yt-dlp returned no valid JSON; the extraction result cannot be trusted.", retryable=True) from exc
        if not isinstance(payload, dict):
            raise YouTubeBridgeError("ytdlp_invalid_output", "yt-dlp returned a non-object result; the extraction result cannot be trusted.", retryable=True)
        return payload

    @staticmethod
    def _classify_ytdlp_failure(stderr: str) -> tuple[str, bool]:
        """Classify a yt-dlp failure into a stable error code and retryability.

        The retryable flag is honest about transience: rate limiting and
        network-type conditions are retryable; permanent conditions such as
        a sign-in/bot wall are not, so consumers do not retry in vain. The
        classification never returns a caption-absence result — every return
        is an explicit failure code.
        """
        text = (stderr or "").lower()
        if "sign in to confirm" in text or "sign in to verify" in text or "confirm you are not a robot" in text:
            return "sign_in_required", False
        if "http error 429" in text or "too many requests" in text or "rate limit" in text:
            return "rate_limited", True
        return "ytdlp_failed", True

    def _get_video_info(self, video_id: str) -> dict[str, Any]:
        cached = self._video_info_cache.get(video_id)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        info = self._yt_dlp_json(f"https://www.youtube.com/watch?v={video_id}")
        # Validate the extraction BEFORE caching or reporting absence. A
        # degraded exit-zero result (e.g. missing id) is a retrieval failure,
        # not evidence that captions do not exist, so it must not be cached
        # and must not be surfaced as transcript_unavailable downstream.
        if not isinstance(info, dict) or not info.get("id"):
            raise YouTubeBridgeError(
                "ytdlp_invalid_output",
                "yt-dlp returned incomplete video information; captions cannot be determined reliably.",
                retryable=True,
            )
        self._video_info_cache[video_id] = (time.monotonic(), info)
        return info

    def _fetch_transcript_segments(self, candidates: list[tuple[str, bool, dict[str, Any]]]) -> tuple[list[dict[str, Any]], str, bool]:
        """Retrieve the first usable caption track, preserving failure causes.

        Returns (segments, language, automatic) for the first candidate that
        yields transcript text. When every candidate fails to retrieve, the
        underlying failure is re-raised with its original classification and
        cause preserved — a retrieval failure is never reported as caption
        absence. A genuinely empty parse (all tracks returned zero segments
        without error) returns an empty segment list so the caller can raise
        ``transcript_empty`` explicitly.
        """
        last_error: Exception | None = None
        last_track = ""
        for language, automatic, track in candidates:
            try:
                segments = parse_json3_transcript(self._get_bytes(track["url"], timeout=60))
            except Exception as exc:  # noqa: BLE001 - wrapping to classify
                last_error = exc
                last_track = track.get("url", "")
                continue
            if segments:
                return segments, language, automatic
        if last_error is not None:
            if isinstance(last_error, YouTubeBridgeError):
                # Preserve the original classification (e.g. upstream_request_failed
                # for a network error) and its retryability, not just the message.
                raise YouTubeBridgeError(
                    last_error.code,
                    f"Caption track retrieval failed: {last_error}",
                    retryable=last_error.retryable,
                ) from last_error
            raise YouTubeBridgeError(
                "transcript_fetch_failed",
                f"Caption tracks were listed but could not be retrieved: {last_error} (track: {last_track})",
                retryable=True,
            ) from last_error
        return [], "", False

    @staticmethod
    def _video_from_api(item: dict[str, Any]) -> dict[str, Any]:
        snippet = item.get("snippet", {})
        details = item.get("contentDetails", {})
        stats = item.get("statistics", {})
        return {
            "video_id": item.get("id"),
            "url": f"https://www.youtube.com/watch?v={item.get('id')}",
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "channel_id": snippet.get("channelId"),
            "channel_title": snippet.get("channelTitle"),
            "published_at": snippet.get("publishedAt"),
            "duration_iso8601": details.get("duration"),
            # The official `caption` flag only reports manual caption tracks.
            # A "false" here is NOT proof that captions are unavailable:
            # automatic (ASR) tracks can still exist and yt-dlp discovers
            # them. Use None ("unknown") for official false; positive
            # knowledge only when the flag is true. list_caption_tracks is
            # authoritative for actual caption discovery.
            "youtube_api_caption_flag": details.get("caption") == "true",
            "caption_available": True if details.get("caption") == "true" else None,
            "view_count": int(stats["viewCount"]) if stats.get("viewCount") else None,
            "like_count": int(stats["likeCount"]) if stats.get("likeCount") else None,
            "tags": snippet.get("tags", []),
            "thumbnails": snippet.get("thumbnails", {}),
        }

    @staticmethod
    def _video_from_ytdlp(info: dict[str, Any]) -> dict[str, Any]:
        upload_date = info.get("upload_date")
        published = None
        if upload_date and len(upload_date) == 8:
            published = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if info.get("timestamp") is not None:
            published = datetime.fromtimestamp(float(info["timestamp"]), timezone.utc).isoformat()
        return {
            "video_id": info.get("id"),
            "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id')}",
            "title": info.get("title"),
            "description": info.get("description"),
            "channel_id": info.get("channel_id"),
            "channel_title": info.get("channel") or info.get("uploader"),
            "channel_url": info.get("channel_url"),
            "published_at": published,
            "duration_seconds": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "tags": info.get("tags") or [],
            "chapters": info.get("chapters") or [],
            "thumbnail": info.get("thumbnail"),
        }

    def get_video(self, reference: str) -> dict[str, Any]:
        video_id = parse_video_id(reference)
        if self.settings.api_key:
            response = self._api_get(
                "videos",
                {"part": "snippet,contentDetails,statistics,status", "id": video_id},
            )
            items = response.get("items", [])
            if not items:
                raise YouTubeBridgeError("video_not_found", "YouTube returned no matching public video.")
            return {"ok": True, "video": self._video_from_api(items[0]), "provenance": provenance("youtube_data_api_v3", official=True)}
        info = self._get_video_info(video_id)
        return {
            "ok": True,
            "video": self._video_from_ytdlp(info),
            "provenance": provenance("yt_dlp", official=False, note="Fallback used because YOUTUBE_API_KEY is not configured."),
        }

    def list_caption_tracks(self, reference: str) -> dict[str, Any]:
        video_id = parse_video_id(reference)
        info = self._get_video_info(video_id)
        manual = info.get("subtitles") or {}
        automatic = info.get("automatic_captions") or {}
        manual_tracks = [
            {"language": language, "formats": sorted({item.get("ext") for item in formats if item.get("ext")})}
            for language, formats in manual.items()
        ]
        original_auto = {
            language: formats for language, formats in automatic.items()
            if language.endswith("-orig") or len(automatic) == 1
        }
        automatic_original_tracks = [
            {"language": language, "formats": sorted({item.get("ext") for item in formats if item.get("ext")})}
            for language, formats in original_auto.items()
        ]
        translated_languages = sorted(set(automatic) - set(original_auto))
        return {
            "ok": True,
            "video_id": video_id,
            "manual_tracks": manual_tracks,
            "automatic_original_tracks": automatic_original_tracks,
            "automatic_translation_languages": translated_languages[:50],
            "automatic_translation_language_count": len(translated_languages),
            "translation_languages_truncated": len(translated_languages) > 50,
            "provenance": provenance("yt_dlp", official=False, note="YouTube does not offer public third-party transcript download through Data API v3."),
        }

    @staticmethod
    def _track_candidates(info: dict[str, Any], languages: list[str]) -> list[tuple[str, bool, dict[str, Any]]]:
        manual = info.get("subtitles") or {}
        automatic = info.get("automatic_captions") or {}
        order: list[tuple[str, bool]] = []
        for language in languages:
            order += [(language, False), (language, True), (f"{language}-orig", True)]
        order += [(language, False) for language in manual if language not in languages]
        order += [(language, True) for language in automatic if language.endswith("-orig")]
        order += [(language, True) for language in automatic]
        candidates: list[tuple[str, bool, dict[str, Any]]] = []
        seen: set[tuple[str, bool, str]] = set()
        for language, is_auto in order:
            formats = (automatic if is_auto else manual).get(language) or []
            for item in formats:
                identity = (language, is_auto, item.get("url", ""))
                if item.get("ext") == "json3" and item.get("url") and identity not in seen:
                    seen.add(identity)
                    candidates.append((language, is_auto, item))
        return candidates

    def get_video_transcript(
        self,
        reference: str,
        *,
        languages: list[str] | None = None,
        include_timestamps: bool = True,
        max_chars: int | None = None,
        start: float | str | None = None,
        end: float | str | None = None,
        continuation: str | None = None,
    ) -> dict[str, Any]:
        video_id = parse_video_id(reference)

        if continuation is not None:
            decoded_continuation = decode_continuation(continuation)
            range_start = decoded_continuation["start_seconds"]
            range_end = decoded_continuation["end_seconds"]
        else:
            range_start = None if start is None else parse_timestamp(start)
            range_end = None if end is None else parse_timestamp(end)

        if range_start is not None and range_end is not None and range_start >= range_end:
            raise YouTubeBridgeError("invalid_time_range", "invalid_time_range: transcript start must be before end")

        preferred = [lang.strip() for lang in (languages or list(self.settings.default_languages)) if lang.strip()]
        info = self._get_video_info(video_id)
        candidates = self._track_candidates(info, preferred)
        if not candidates:
            raise YouTubeBridgeError("transcript_unavailable", "No manual or automatic captions are available for this video.")
        segments, language, automatic = self._fetch_transcript_segments(candidates)
        if not segments:
            raise YouTubeBridgeError("transcript_empty", "Caption tracks were available but contained no transcript text.")

        filtered_segments = [
            segment
            for segment in segments
            if (
                range_start is None
                or float(segment["start_seconds"]) >= range_start - 1e-6
            )
            and (
                range_end is None
                or float(segment["start_seconds"]) < range_end
            )
        ]
        lines = [
            (f"[{_seconds_to_timestamp(segment['start_seconds'])}] " if include_timestamps else "")
            + segment["text"]
            for segment in filtered_segments
        ]
        full_transcript = "\n".join(lines)
        limit = min(max_chars or self.settings.transcript_max_chars, self.settings.transcript_hard_max_chars)

        transcript_parts: list[str] = []
        returned_segments = 0
        oversized_first = False
        for line in lines:
            candidate = "\n".join(transcript_parts + [line])
            if len(candidate) > limit:
                break
            transcript_parts.append(line)
            returned_segments += 1
        transcript = "\n".join(transcript_parts)
        if not transcript_parts and lines:
            # A single segment longer than the limit would otherwise trap a
            # paginating client on an empty page that never advances. Return
            # that segment hard-truncated, count it as returned, and treat it
            # as consumed so the next page starts after it.
            transcript = lines[0][:limit]
            returned_segments = 1
            oversized_first = True

        total_segments = len(filtered_segments)
        total_chars = len(full_transcript)
        returned_chars = len(transcript)
        truncated = total_chars > returned_chars
        first_omitted = None
        if returned_segments < total_segments:
            first_omitted = filtered_segments[returned_segments]
        elif oversized_first:
            # The hard-truncated segment itself was returned and consumed; it
            # has no follow-up page inside the selected range.
            first_omitted = None
            truncated = False
        has_more = first_omitted is not None
        next_start = None
        next_continuation = None
        if first_omitted is not None:
            next_start = float(first_omitted["start_seconds"])
            next_continuation = encode_continuation(next_start, range_end)

        return {
            "ok": True,
            "video": {"video_id": video_id, "title": info.get("title"), "channel": info.get("channel") or info.get("uploader")},
            "language": language,
            "automatic": automatic,
            "transcript": transcript,
            "segment_count": returned_segments,
            "truncated": truncated,
            "max_chars": limit,
            "pagination": {
                "has_more": has_more,
                "next_start": next_start,
                "next_continuation": next_continuation,
                "returned_segments": returned_segments,
                "total_segments": total_segments,
                "returned_chars": returned_chars,
                "total_chars": total_chars,
                "range_start": range_start,
                "range_end": range_end,
            },
            "provenance": provenance("yt_dlp_youtube_caption_endpoint", official=False, note="Unofficial fallback; availability can change or be rate-limited."),
        }

    def search_video_transcript(
        self,
        reference: str,
        *,
        queries: list[str],
        languages: list[str] | None = None,
        limit: int = 10,
        context_before: float = 10.0,
        context_after: float = 20.0,
    ) -> dict[str, Any]:
        """Locate regions in a transcript via deterministic substring search.

        Search is purely textual (normalized substring match). Results are
        LOCATORS: small context snippets for the consumer to inspect further
        with get_video_transcript(start, end). Zero matches do NOT prove that
        a topic is absent — exhaustive pagination remains available.
        """
        video_id = parse_video_id(reference)
        if not isinstance(queries, list):
            raise YouTubeBridgeError("invalid_query", "invalid_query: queries must be a list of non-empty strings.")
        normalized_queries = []
        for query in queries:
            if not isinstance(query, str):
                raise YouTubeBridgeError("invalid_query", "invalid_query: queries must be a list of non-empty strings.")
            normalized = normalize_search_text(query)
            if not normalized:
                raise YouTubeBridgeError("invalid_query", f"invalid_query: queries must contain at least one non-whitespace string; got {query!r}.")
            normalized_queries.append(normalized)
        # Deterministic dedupe: preserve first-seen order.
        # Deterministic dedupe by normalized form; keep the first original
        # spelling of each query for reporting.
        seen: set[str] = set()
        unique_queries: list[str] = []
        original_by_normalized: dict[str, str] = {}
        for original, normalized in zip(queries, normalized_queries):
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_queries.append(normalized)
            original_by_normalized[normalized] = original
        if not unique_queries:
            raise YouTubeBridgeError("invalid_query", "invalid_query: queries must not be empty.")

        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit):
            raise YouTubeBridgeError("invalid_limit", f"invalid_limit: {limit!r}")
        limit_value = int(limit)
        if limit_value != limit:
            raise YouTubeBridgeError("invalid_limit", f"invalid_limit: limit must be an integer, got {limit!r}")
        safe_limit = max(1, min(limit_value, 50))
        if safe_limit != limit:
            raise YouTubeBridgeError(
                "invalid_limit",
                f"invalid_limit: limit must be between 1 and 50, got {limit!r}",
            )

        try:
            ctx_before = float(context_before)
            ctx_after = float(context_after)
        except (TypeError, ValueError):
            raise YouTubeBridgeError("invalid_context", f"invalid_context: context_before={context_before!r}, context_after={context_after!r}")
        if not math.isfinite(ctx_before) or not math.isfinite(ctx_after) or ctx_before < 0 or ctx_after < 0:
            raise YouTubeBridgeError("invalid_context", f"invalid_context: context_before={context_before!r}, context_after={context_after!r}")

        preferred = [lang.strip() for lang in (languages or list(self.settings.default_languages)) if lang.strip()]
        info = self._get_video_info(video_id)
        candidates = self._track_candidates(info, preferred)
        if not candidates:
            raise YouTubeBridgeError("transcript_unavailable", "transcript_unavailable: No manual or automatic captions are available for this video.")
        segments, language, automatic = self._fetch_transcript_segments(candidates)
        if not segments:
            raise YouTubeBridgeError("transcript_empty", "transcript_empty: Caption tracks were available but contained no transcript text.")

        normalized_segments = [normalize_search_text(segment["text"]) for segment in segments]

        # Cross-segment matching: build the joined normalized transcript with
        # a char-offset -> segment-index map, so phrases split across caption
        # segments are found too.
        offset_to_segment: list[int] = []
        joined_parts: list[str] = []
        for index, normalized_text in enumerate(normalized_segments):
            offset_to_segment.extend([index] * (len(normalized_text) + 1))  # +1 for the join space
            joined_parts.append(normalized_text)
        joined_transcript = " ".join(joined_parts)
        # offset_to_segment[i] = index of the segment that char offset i belongs to

        # Deterministic chronological hit accumulation, merged/deduped by
        # overlap. Each region tracks which queries matched inside it.
        merged: list[dict[str, Any]] = []  # keys: first, last, matched_queries (ordered)
        for query in unique_queries:
            search_from = 0
            while True:
                pos = joined_transcript.find(query, search_from)
                if pos < 0:
                    break
                search_from = pos + 1
                match_first_segment = offset_to_segment[pos]
                match_last_segment = offset_to_segment[min(pos + len(query) - 1, len(offset_to_segment) - 1)]
                match_start = float(segments[match_first_segment]["start_seconds"])
                match_end = float(segments[match_last_segment]["start_seconds"]) + max(float(segments[match_last_segment].get("duration_seconds") or 0.0), 0.0)
                region_first = match_start - ctx_before
                region_last = match_start + ctx_after
                if merged and region_first <= merged[-1]["last"]:
                    # Overlapping or adjacent region: extend it, keep contributing
                    # queries in first-seen order.
                    region = merged[-1]
                    region["last"] = max(region["last"], region_last)
                    region["first"] = min(region["first"], region_first)
                    region["match_start"] = min(region["match_start"], match_start)
                    region["match_end"] = max(region["match_end"], match_end)
                    for q in (query,):
                        if q not in region["matched_queries"]:
                            region["matched_queries"].append(q)
                else:
                    merged.append({
                        "first": region_first,
                        "last": region_last,
                        "match_start": match_start,
                        "match_end": match_end,
                        "matched_queries": [query],
                    })
        # Per-query accumulation can interleave chronologically; sort, then
        # run a merge pass so overlapping hits (including across different
        # queries) are combined deterministically.
        merged.sort(key=lambda region: (region["match_start"], region["first"]))
        combined: list[dict[str, Any]] = []
        for region in merged:
            if combined and region["first"] <= combined[-1]["last"]:
                target = combined[-1]
                target["last"] = max(target["last"], region["last"])
                target["first"] = min(target["first"], region["first"])
                target["match_start"] = min(target["match_start"], region["match_start"])
                target["match_end"] = max(target["match_end"], region["match_end"])
                for query in region["matched_queries"]:
                    if query not in target["matched_queries"]:
                        target["matched_queries"].append(query)
            else:
                combined.append(region)
        merged = combined

        total_matches = len(merged)
        truncated_by_limit = False
        if total_matches > safe_limit:
            merged = merged[:safe_limit]
            truncated_by_limit = True

        transcript_start = float(segments[0]["start_seconds"])
        transcript_end = float(segments[-1]["start_seconds"]) + max(float(segments[-1].get("duration_seconds") or 0.0), 0.0)
        regions = []
        for region in merged:
            # The reported window is the requested context around the match,
            # clipped to the actual transcript bounds and always covering at
            # least the matching segment(s); the snippet text comes from the
            # real transcript segments overlapping that window (never
            # fabricated text).
            start = min(max(region["first"], transcript_start), region["match_start"])
            end = max(min(region["last"], transcript_end), region["match_end"])
            snippet_parts = [
                normalize_search_text(segment["text"])
                for segment in segments
                if float(segment["start_seconds"]) < end
                and float(segment["start_seconds"]) + max(float(segment.get("duration_seconds") or 0.0), 0.0) > start
            ]
            regions.append({
                "match_start": region["match_start"],
                "match_end": region["match_end"],
                "start": start,
                "end": end,
                "timestamp": _seconds_to_timestamp(region["match_start"]),
                "text": " ".join(snippet_parts),
                "matched_queries": [original_by_normalized[query] for query in region["matched_queries"]],
            })

        return {
            "ok": True,
            "video": {"video_id": video_id, "title": info.get("title"), "channel": info.get("channel") or info.get("uploader")},
            "language": language,
            "automatic": automatic,
            "queries": unique_queries,
            "matches": regions,
            "match_count": len(regions),
            "total_matches_before_limit": total_matches,
            "results_truncated_by_limit": truncated_by_limit,
            "limit": safe_limit,
            "search_semantics": {
                "type": "textual_substring",
                "note": "Results are locators, not authoritative passages. Zero matches do not prove the topic is absent; use get_video_transcript with ranges or full pagination for exhaustive review.",
            },
            "provenance": provenance("yt_dlp_youtube_caption_endpoint", official=False, note="Unofficial fallback; availability can change or be rate-limited."),
        }

    def _resolve_channel_id(self, reference: str) -> str:
        parsed = parse_channel_reference(reference)
        if parsed["kind"] == "id":
            return parsed["value"]
        if self.settings.api_key:
            response = self._api_get("channels", {"part": "id", "forHandle": parsed["value"]})
            items = response.get("items", [])
            if items:
                return items[0]["id"]
            raise YouTubeBridgeError("channel_not_found", "YouTube returned no channel for that handle.")
        info = self._yt_dlp_json(f"https://www.youtube.com/@{parsed['value']}", flat=True, playlist_end=1)
        channel_id = info.get("channel_id") or info.get("uploader_id")
        if not channel_id:
            raise YouTubeBridgeError("channel_not_found", "Could not resolve the channel handle without the Data API.")
        return channel_id

    def get_channel(self, reference: str) -> dict[str, Any]:
        channel_id = self._resolve_channel_id(reference)
        if self.settings.api_key:
            response = self._api_get("channels", {"part": "snippet,contentDetails,statistics", "id": channel_id})
            items = response.get("items", [])
            if not items:
                raise YouTubeBridgeError("channel_not_found", "YouTube returned no matching channel.")
            item = items[0]
            snippet, stats = item.get("snippet", {}), item.get("statistics", {})
            channel = {
                "channel_id": item.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "custom_url": snippet.get("customUrl"),
                "published_at": snippet.get("publishedAt"),
                "country": snippet.get("country"),
                "uploads_playlist_id": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
                "view_count": int(stats["viewCount"]) if stats.get("viewCount") else None,
                "subscriber_count": int(stats["subscriberCount"]) if stats.get("subscriberCount") else None,
                "video_count": int(stats["videoCount"]) if stats.get("videoCount") else None,
                "thumbnails": snippet.get("thumbnails", {}),
            }
            return {"ok": True, "channel": channel, "provenance": provenance("youtube_data_api_v3", official=True)}
        info = self._yt_dlp_json(f"https://www.youtube.com/channel/{channel_id}", flat=True, playlist_end=1)
        channel = {
            "channel_id": channel_id,
            "title": info.get("channel") or info.get("uploader") or info.get("title"),
            "description": info.get("description"),
            "url": info.get("webpage_url") or f"https://www.youtube.com/channel/{channel_id}",
        }
        return {"ok": True, "channel": channel, "provenance": provenance("yt_dlp", official=False)}

    def get_recent_uploads(self, reference: str, *, limit: int = 10) -> dict[str, Any]:
        channel_id = self._resolve_channel_id(reference)
        safe_limit = max(1, min(limit, 50))
        raw = self._get_bytes(self.FEED_URL.format(channel_id=channel_id))
        root = ET.fromstring(raw)
        ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        uploads = []
        for entry in root.findall("atom:entry", ns)[:safe_limit]:
            video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
            uploads.append({
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": entry.findtext("atom:title", default="", namespaces=ns),
                "published_at": entry.findtext("atom:published", default="", namespaces=ns),
                "updated_at": entry.findtext("atom:updated", default="", namespaces=ns),
            })
        return {
            "ok": True,
            "channel_id": channel_id,
            "uploads": uploads,
            "provenance": provenance("youtube_atom_feed", official=True, note="Public channel upload feed; limited recent history."),
        }

    def search_videos(self, query: str, *, limit: int = 10, order: str = "relevance") -> dict[str, Any]:
        clean_query = query.strip()
        if not clean_query:
            raise YouTubeBridgeError("invalid_query", "Search query must not be empty.")
        safe_limit = max(1, min(limit, 25))
        if self.settings.api_key:
            allowed_orders = {"date", "rating", "relevance", "title", "videoCount", "viewCount"}
            response = self._api_get("search", {
                "part": "snippet", "type": "video", "q": clean_query,
                "maxResults": safe_limit, "order": order if order in allowed_orders else "relevance",
            })
            videos = []
            for item in response.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                snippet = item.get("snippet", {})
                videos.append({
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "title": snippet.get("title"),
                    "description": snippet.get("description"),
                    "channel_id": snippet.get("channelId"),
                    "channel_title": snippet.get("channelTitle"),
                    "published_at": snippet.get("publishedAt"),
                })
            return {"ok": True, "query": clean_query, "videos": videos, "provenance": provenance("youtube_data_api_v3", official=True)}
        info = self._yt_dlp_json(f"ytsearch{safe_limit}:{clean_query}", flat=True)
        videos = []
        for item in info.get("entries", []):
            video_id = item.get("id")
            videos.append({
                "video_id": video_id,
                "url": item.get("url") if str(item.get("url", "")).startswith("http") else f"https://www.youtube.com/watch?v={video_id}",
                "title": item.get("title"),
                "channel_id": item.get("channel_id"),
                "channel_title": item.get("channel") or item.get("uploader"),
                "duration_seconds": item.get("duration"),
            })
        return {"ok": True, "query": clean_query, "videos": videos, "provenance": provenance("yt_dlp_search", official=False)}

    # ---- playlists ---------------------------------------------------------
    # Official path: playlists.list (metadata) + playlistItems.list (ordered
    # entries). Fallback path: yt-dlp --flat-playlist on the playlist URL.
    # Positions come from the enumeration itself, never from a URL index.

    PLAYLIST_PAGE_SIZE = 50  # official API maxResults maximum
    PLAYLIST_MAX_ITEMS = 500  # hard bound on one enumeration request
    PLAYLIST_DESCRIPTION_MAX_CHARS = 1000
    PLAYLIST_NOTE_MAX_CHARS = 200

    @staticmethod
    def _bounded_text(value: Any, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def _playlist_from_api(playlists_response: dict[str, Any], playlist_id: str) -> dict[str, Any]:
        items = playlists_response.get("items", [])
        if not items:
            raise YouTubeBridgeError("playlist_not_found", "YouTube returned no matching public playlist.")
        snippet = items[0].get("snippet", {})
        return {
            "playlist_id": playlist_id,
            "title": snippet.get("title"),
            "description": YouTubeService._bounded_text(snippet.get("description"), YouTubeService.PLAYLIST_DESCRIPTION_MAX_CHARS),
            "channel_id": snippet.get("channelId"),
            "channel_title": snippet.get("channelTitle"),
            "published_at": snippet.get("publishedAt"),
        }

    @staticmethod
    def _entry_from_api(item: dict[str, Any], position: int) -> dict[str, Any]:
        snippet = item.get("snippet", {})
        details = item.get("contentDetails", {})
        video_id = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
        # Only private/deleted videos lack a videoId; such items are reported
        # with video_id None rather than skipped, so positions stay aligned
        # with the playlist order.
        entry: dict[str, Any] = {
            "position": position,
            "video_id": video_id,
        }
        if video_id:
            entry["url"] = f"https://www.youtube.com/watch?v={video_id}"
        title = snippet.get("title")
        if title and title != "Private video" and title != "Deleted video":
            entry["title"] = title
        elif title in {"Private video", "Deleted video"}:
            entry["title"] = None
            entry["availability_note"] = title
        else:
            entry["title"] = title
        if snippet.get("videoOwnerChannelTitle"):
            entry["channel_title"] = snippet["videoOwnerChannelTitle"]
            if snippet.get("videoOwnerChannelId"):
                entry["channel_id"] = snippet["videoOwnerChannelId"]
        elif snippet.get("channelTitle"):
            entry["channel_title"] = snippet["channelTitle"]
        if snippet.get("videoPublishedAt"):
            entry["video_published_at"] = snippet["videoPublishedAt"]
        elif snippet.get("publishedAt"):
            entry["video_published_at"] = snippet["publishedAt"]
        if details.get("videoPublishedAt"):
            entry["video_published_at"] = details["videoPublishedAt"]
        if snippet.get("publishedAt"):
            entry["playlist_added_at"] = snippet["publishedAt"]
        note = snippet.get("description")
        if note:
            entry["note"] = YouTubeService._bounded_text(note, YouTubeService.PLAYLIST_NOTE_MAX_CHARS)
        return entry

    def _enumerate_playlist_api(self, playlist_id: str, limit: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(entries) < limit:
            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(self.PLAYLIST_PAGE_SIZE, limit - len(entries)),
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._api_get("playlistItems", params)
            for item in response.get("items", []):
                if len(entries) >= limit:
                    break
                entries.append(self._entry_from_api(item, len(entries) + 1))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return entries

    @staticmethod
    def _playlist_from_ytdlp(info: dict[str, Any], playlist_id: str) -> dict[str, Any]:
        return {
            "playlist_id": playlist_id,
            "title": info.get("title"),
            "description": YouTubeService._bounded_text(info.get("description"), YouTubeService.PLAYLIST_DESCRIPTION_MAX_CHARS),
            "channel_id": info.get("channel_id") or info.get("uploader_id"),
            "channel_title": info.get("channel") or info.get("uploader"),
            # yt-dlp flat extraction does not expose the playlist creation date.
            "published_at": None,
            "item_count": info.get("playlist_count"),
        }

    @staticmethod
    def _entry_from_ytdlp(item: dict[str, Any], position: int) -> dict[str, Any]:
        video_id = item.get("id")
        entry: dict[str, Any] = {"position": position, "video_id": video_id}
        if video_id:
            entry["url"] = f"https://www.youtube.com/watch?v={video_id}"
        if item.get("title") is not None:
            entry["title"] = item["title"]
        channel_title = item.get("channel") or item.get("uploader")
        if channel_title:
            entry["channel_title"] = channel_title
        if item.get("channel_id"):
            entry["channel_id"] = item["channel_id"]
        if item.get("duration") is not None:
            entry["duration_seconds"] = item["duration"]
        # flat extraction does not provide per-video publish dates, playlist
        # added_at timestamps, or playlist item notes; those fields are
        # omitted rather than inferred.
        return entry

    def get_playlist(self, reference: str) -> dict[str, Any]:
        parsed = parse_playlist_reference(reference)
        playlist_id = parsed["playlist_id"]
        result: dict[str, Any] = {
            "ok": True,
            "playlist_reference": {"kind": parsed["kind"], "playlist_id": playlist_id},
            "pagination": {"has_more": False, "next_continuation": None, "returned_items": 0, "total_items": None},
            "items": [],
            "provenance": None,
        }
        if parsed["video_id"]:
            result["playlist_reference"]["video_id"] = parsed["video_id"]
        if parsed["url_index"] is not None:
            result["playlist_reference"]["url_index_context"] = parsed["url_index"]
        if self.settings.api_key:
            metadata_response = self._api_get("playlists", {"part": "snippet,contentDetails", "id": playlist_id})
            playlist = self._playlist_from_api(metadata_response, playlist_id)
            playlist["item_count"] = metadata_response["items"][0].get("contentDetails", {}).get("itemCount")
            entries = self._enumerate_playlist_api(playlist_id, self.PLAYLIST_MAX_ITEMS)
            result["playlist"] = playlist
            result["items"] = entries
            result["provenance"] = provenance("youtube_data_api_v3", official=True)
        else:
            info = self._yt_dlp_json(f"https://www.youtube.com/playlist?list={playlist_id}", flat=True)
            if not info or info.get("_type") not in (None, "playlist") or not info.get("entries"):
                raise YouTubeBridgeError("playlist_not_found", "YouTube returned no public playlist for that ID.")
            playlist = self._playlist_from_ytdlp(info, playlist_id)
            # The yt-dlp fallback must respect the same hard enumeration
            # bound as the official path; oversized playlists are truncated,
            # not returned unbounded.
            entries = [
                self._entry_from_ytdlp(item, position)
                for position, item in enumerate(info.get("entries") or [], start=1)
                if position <= self.PLAYLIST_MAX_ITEMS
            ]
            result["playlist"] = playlist
            result["items"] = entries
            result["provenance"] = provenance(
                "yt_dlp",
                official=False,
                note="Fallback used because YOUTUBE_API_KEY is not configured.",
            )
        result["playlist"]["item_count_reported"] = len(entries)
        result["pagination"]["returned_items"] = len(entries)
        result["pagination"]["total_items"] = result["playlist"].get("item_count")
        return result

    def find_playlist_position(self, reference: str) -> dict[str, Any]:
        """Locate a video's actual position within a playlist by enumeration.

        The URL ``index`` parameter is not trusted; the position reported
        here comes from enumerating the playlist (1-based). When the
        reference carries both a video ID and list= context, only that
        video's position is looked up.
        """
        parsed = parse_playlist_reference(reference)
        if not parsed["video_id"]:
            raise YouTubeBridgeError(
                "invalid_playlist_reference",
                "find_playlist_position requires a watch URL with both video and list= context (or use get_playlist to enumerate).",
            )
        playlist = self.get_playlist(parsed["playlist_id"])
        video_id = parsed["video_id"]
        for item in playlist.get("items", []):
            if item.get("video_id") == video_id:
                return {
                    "ok": True,
                    "playlist_id": parsed["playlist_id"],
                    "video_id": video_id,
                    "position": item["position"],
                    "url_index_context": parsed["url_index"],
                    "playlist_title": playlist["playlist"].get("title"),
                    "provenance": playlist["provenance"],
                }
        raise YouTubeBridgeError("video_not_in_playlist", f"The video {video_id} was not found in playlist {parsed['playlist_id']} (by enumeration, not by the URL index).")


def error_result(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, YouTubeBridgeError):
        return {"ok": False, "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}}
    return {"ok": False, "error": {"code": "internal_error", "message": str(exc), "retryable": False}}
