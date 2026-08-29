from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
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

    @classmethod
    def from_env(cls) -> "Settings":
        raw_enabled = os.getenv("YOUTUBE_ENABLE_YTDLP", "true").strip().lower()
        return cls(
            api_key=os.getenv("YOUTUBE_API_KEY") or None,
            enable_ytdlp=raw_enabled in {"1", "true", "yes", "on"},
            transcript_max_chars=int(os.getenv("YOUTUBE_TRANSCRIPT_MAX_CHARS", "60000")),
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


class YouTubeService:
    API_ROOT = "https://www.googleapis.com/youtube/v3"
    FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._urlopen = urlopen
        self._run = run
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
        completed = self._run(command, capture_output=True, text=True, timeout=90, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "yt-dlp failed").strip().splitlines()[-1]
            raise YouTubeBridgeError("ytdlp_failed", detail, retryable=True)
        return json.loads(completed.stdout)

    def _get_video_info(self, video_id: str) -> dict[str, Any]:
        cached = self._video_info_cache.get(video_id)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        info = self._yt_dlp_json(f"https://www.youtube.com/watch?v={video_id}")
        self._video_info_cache[video_id] = (time.monotonic(), info)
        return info

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
            "caption_available": details.get("caption") == "true",
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
    ) -> dict[str, Any]:
        video_id = parse_video_id(reference)
        preferred = [lang.strip() for lang in (languages or list(self.settings.default_languages)) if lang.strip()]
        info = self._get_video_info(video_id)
        candidates = self._track_candidates(info, preferred)
        if not candidates:
            raise YouTubeBridgeError("transcript_unavailable", "No manual or automatic captions are available for this video.")
        last_error: Exception | None = None
        language = ""
        automatic = False
        segments: list[dict[str, Any]] = []
        for language, automatic, track in candidates:
            try:
                segments = parse_json3_transcript(self._get_bytes(track["url"], timeout=60))
                if segments:
                    break
            except Exception as exc:
                last_error = exc
        if not segments:
            if last_error:
                raise YouTubeBridgeError("transcript_fetch_failed", f"Caption tracks were listed but could not be retrieved: {last_error}", retryable=True) from last_error
            raise YouTubeBridgeError("transcript_empty", "Caption tracks were available but contained no transcript text.")
        lines = []
        for segment in segments:
            prefix = f"[{_seconds_to_timestamp(segment['start_seconds'])}] " if include_timestamps else ""
            lines.append(prefix + segment["text"])
        transcript = "\n".join(lines)
        limit = min(max_chars or self.settings.transcript_max_chars, self.settings.transcript_max_chars)
        truncated = len(transcript) > limit
        if truncated:
            transcript = transcript[:limit].rsplit("\n", 1)[0]
        return {
            "ok": True,
            "video": {"video_id": video_id, "title": info.get("title"), "channel": info.get("channel") or info.get("uploader")},
            "language": language,
            "automatic": automatic,
            "transcript": transcript,
            "segment_count": len(segments),
            "truncated": truncated,
            "max_chars": limit,
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


def error_result(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, YouTubeBridgeError):
        return {"ok": False, "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}}
    return {"ok": False, "error": {"code": "internal_error", "message": str(exc), "retryable": False}}
