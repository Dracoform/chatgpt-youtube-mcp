import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from youtube_mcp.core import (
    Settings,
    YouTubeBridgeError,
    YouTubeService,
    decode_continuation,
    encode_continuation,
    parse_channel_reference,
    parse_json3_transcript,
    parse_timestamp,
    parse_video_id,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class CoreTests(unittest.TestCase):
    def test_video_reference_formats(self):
        expected = "dQw4w9WgXcQ"
        values = [
            expected,
            f"https://youtu.be/{expected}?t=3",
            f"https://www.youtube.com/watch?v={expected}&list=abc",
            f"https://youtube.com/shorts/{expected}",
            f"https://youtube.com/live/{expected}",
            f"https://youtube.com/embed/{expected}",
        ]
        for value in values:
            self.assertEqual(parse_video_id(value), expected)

    def test_rejects_non_youtube_url(self):
        with self.assertRaises(YouTubeBridgeError):
            parse_video_id("https://example.org/watch?v=dQw4w9WgXcQ")

    def test_channel_reference(self):
        channel_id = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
        self.assertEqual(parse_channel_reference(channel_id), {"kind": "id", "value": channel_id})
        self.assertEqual(parse_channel_reference("https://youtube.com/@GoogleDevelopers"), {"kind": "handle", "value": "GoogleDevelopers"})

    def test_json3_parser_deduplicates(self):
        raw = json.dumps({"events": [
            {"tStartMs": 1000, "dDurationMs": 500, "segs": [{"utf8": "Hello"}]},
            {"tStartMs": 1500, "dDurationMs": 500, "segs": [{"utf8": "Hello"}]},
            {"tStartMs": 2000, "dDurationMs": 750, "segs": [{"utf8": "world\nagain"}]},
        ]}).encode()
        self.assertEqual(parse_json3_transcript(raw), [
            {"start_seconds": 1.0, "duration_seconds": 0.5, "text": "Hello"},
            {"start_seconds": 2.0, "duration_seconds": 0.75, "text": "world again"},
        ])

    def test_official_video_api_mapping(self):
        payload = {"items": [{
            "id": "dQw4w9WgXcQ",
            "snippet": {"title": "Title", "description": "Desc", "channelId": "UC1", "channelTitle": "Channel", "publishedAt": "2026-01-01T00:00:00Z"},
            "contentDetails": {"duration": "PT1M", "caption": "true"},
            "statistics": {"viewCount": "12"},
        }]}

        def fake_urlopen(request, timeout=0):
            self.assertIn("key=secret", request.full_url)
            return Response(json.dumps(payload).encode())

        service = YouTubeService(Settings(api_key="secret"), urlopen=fake_urlopen)
        result = service.get_video("dQw4w9WgXcQ")
        self.assertTrue(result["ok"])
        self.assertEqual(result["video"]["view_count"], 12)
        self.assertTrue(result["provenance"]["official"])

    def test_atom_feed(self):
        channel_id = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
        feed = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>dQw4w9WgXcQ</yt:videoId><title>Example</title><published>2026-01-01T00:00:00Z</published><updated>2026-01-02T00:00:00Z</updated></entry></feed>'''
        service = YouTubeService(Settings(enable_ytdlp=False), urlopen=lambda request, timeout=0: Response(feed))
        result = service.get_recent_uploads(channel_id)
        self.assertEqual(result["uploads"][0]["title"], "Example")
        self.assertEqual(result["provenance"]["source"], "youtube_atom_feed")

    def test_caption_candidates_prefer_requested_manual_then_auto_original(self):
        info = {
            "subtitles": {"de": [{"ext": "json3", "url": "https://captions/de"}]},
            "automatic_captions": {
                "en": [{"ext": "json3", "url": "https://captions/en-translation"}],
                "en-orig": [{"ext": "json3", "url": "https://captions/en-original"}],
            },
        }
        candidates = YouTubeService._track_candidates(info, ["de", "en"])
        self.assertEqual([(lang, auto) for lang, auto, _ in candidates[:3]], [
            ("de", False), ("en", True), ("en-orig", True),
        ])

    # ---- regression: official caption flag is non-authoritative -------------
    # Live evidence (2026-09-06): a video with contentDetails.caption=false
    # still had an automatic German "de-orig" track that yt-dlp discovered
    # and downloaded (767 transcript segments). The official flag only
    # reports manual captions.

    OFFICIAL_PAYLOAD_CAPTION_FALSE = {"items": [{
        "id": "dQw4w9WgXcQ",
        "snippet": {"title": "StarCraft", "description": "DE", "channelId": "UC1",
                    "channelTitle": "Channel", "publishedAt": "2026-01-01T00:00:00Z"},
        "contentDetails": {"duration": "PT30M", "caption": "false"},
        "statistics": {"viewCount": "12"},
    }]}

    YTDLP_INFO_DE_ORIG = {
        "id": "dQw4w9WgXcQ",
        "title": "StarCraft",
        "subtitles": {},  # no manual tracks
        "automatic_captions": {
            "de": [{"ext": "json3", "url": "https://captions/de-translation"}],
            "de-orig": [{"ext": "json3", "url": "https://captions/de-orig"}],
        },
    }

    JSON3_SEGMENTS = (
        b'{"events": ['
        b'{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Hallo"}]}, '
        b'{"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "Welt"}]}'
        b']}'
    )

    def _service_caption_false(self):
        payload = self.OFFICIAL_PAYLOAD_CAPTION_FALSE

        def fake_urlopen(request, timeout=0):
            return Response(json.dumps(payload).encode())

        return YouTubeService(Settings(api_key="secret"), urlopen=fake_urlopen)

    def test_official_caption_false_is_not_caption_unavailable(self):
        service = self._service_caption_false()
        result = service.get_video("dQw4w9WgXcQ")
        video = result["video"]
        # raw official value preserved, clearly named
        self.assertFalse(video["youtube_api_caption_flag"])
        # an official false must NOT become an authoritative "no captions"
        self.assertIsNone(video["caption_available"])
        # and get_video must not have spawned yt-dlp just to resolve this
        self.assertTrue(result["provenance"]["official"])

    def test_official_caption_true_is_positive_knowledge(self):
        payload = json.loads(json.dumps(self.OFFICIAL_PAYLOAD_CAPTION_FALSE))
        payload["items"][0]["contentDetails"]["caption"] = "true"

        def fake_urlopen(request, timeout=0):
            return Response(json.dumps(payload).encode())

        service = YouTubeService(Settings(api_key="secret"), urlopen=fake_urlopen)
        video = service.get_video("dQw4w9WgXcQ")["video"]
        self.assertTrue(video["youtube_api_caption_flag"])
        self.assertTrue(video["caption_available"])

    def test_list_caption_tracks_reports_de_orig_as_automatic(self):
        info = self.YTDLP_INFO_DE_ORIG

        def fake_run(command, capture_output, text, timeout, check):
            completed = subprocess.CompletedProcess(command, 0, json.dumps(info), "")
            return completed

        service = YouTubeService(Settings(api_key="secret"), run=fake_run)
        result = service.list_caption_tracks("dQw4w9WgXcQ")
        self.assertTrue(result["ok"])
        self.assertEqual(result["manual_tracks"], [])
        self.assertEqual(
            [t["language"] for t in result["automatic_original_tracks"]],
            ["de-orig"],
        )
        self.assertIn("de", result["automatic_translation_languages"])

    def test_transcript_retrieval_succeeds_via_de_orig(self):
        info = self.YTDLP_INFO_DE_ORIG
        requested_urls = []

        def fake_run(command, capture_output, text, timeout, check):
            return subprocess.CompletedProcess(command, 0, json.dumps(info), "")

        def fake_urlopen(request, timeout=0):
            requested_urls.append(request.full_url)
            return Response(self.JSON3_SEGMENTS)

        service = YouTubeService(Settings(api_key="secret"),
                                 urlopen=fake_urlopen, run=fake_run)
        result = service.get_video_transcript("dQw4w9WgXcQ", languages=["de"])
        self.assertTrue(result["ok"])
        # an automatic track was used, not a manual one
        self.assertTrue(result["automatic"])
        self.assertTrue(result["language"].startswith("de"))
        self.assertGreaterEqual(result["segment_count"], 2)
        self.assertIn("Hallo", result["transcript"])
        # de-orig was among the track candidates yt-dlp discovered
        self.assertTrue(result["automatic"])
        candidates = YouTubeService._track_candidates(info, ["de"])
        self.assertIn(("de-orig", True), [(lang, auto) for lang, auto, _ in candidates])

    def _service_transcript(self):
        info = self.YTDLP_INFO_DE_ORIG

        def fake_run(command, capture_output, text, timeout, check):
            return subprocess.CompletedProcess(command, 0, json.dumps(info), "")

        def fake_urlopen(request, timeout=0):
            return Response(self.JSON3_SEGMENTS)

        return YouTubeService(Settings(), urlopen=fake_urlopen, run=fake_run)

    def test_parse_timestamp_formats(self):
        cases = {
            "1": 1.0,
            "01": 1.0,
            "1:02": 62.0,
            "1:02.5": 62.5,
            "1:02:03": 3723.0,
            "1:02:03.5": 3723.5,
            "12.25": 12.25,
            "  2.5  ": 2.5,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_timestamp(value), expected)
        self.assertEqual(parse_timestamp(2.5), 2.5)
        self.assertEqual(parse_timestamp(3), 3.0)

    def test_parse_timestamp_rejects_invalid_values(self):
        invalid = ["", "1:", ":02", "1:2:3:4", "-1", "-1:02", "abc", "1:60", "01:02:03:04"]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(YouTubeBridgeError, "invalid_timestamp"):
                    parse_timestamp(value)
        with self.assertRaisesRegex(YouTubeBridgeError, "invalid_timestamp"):
            parse_timestamp(-1)

    def test_transcript_range_filters_segments_by_start_and_end(self):
        service = self._service_transcript()
        result = service.get_video_transcript(
            "dQw4w9WgXcQ", start="1", end="3", include_timestamps=False, max_chars=100,
        )
        self.assertEqual(result["transcript"], "Welt")
        self.assertEqual(result["segment_count"], 1)
        self.assertEqual(result["pagination"]["returned_segments"], 1)
        self.assertEqual(result["pagination"]["total_segments"], 1)
        self.assertEqual(result["pagination"]["range_start"], 1.0)
        self.assertEqual(result["pagination"]["range_end"], 3.0)

    def test_transcript_pagination_continues_without_repeating_content(self):
        service = self._service_transcript()
        first = service.get_video_transcript(
            "dQw4w9WgXcQ", start="0", end="5", include_timestamps=False, max_chars=7,
        )
        self.assertEqual(first["transcript"], "Hallo")
        self.assertTrue(first["pagination"]["has_more"])
        self.assertEqual(first["pagination"]["next_start"], 2.0)
        self.assertEqual(first["pagination"]["returned_segments"], 1)
        self.assertEqual(first["pagination"]["total_segments"], 2)

        second = service.get_video_transcript(
            "dQw4w9WgXcQ", start=first["pagination"]["next_start"], end="5",
            include_timestamps=False, max_chars=100,
        )
        self.assertEqual(second["transcript"], "Welt")
        self.assertFalse(second["pagination"]["has_more"])
        self.assertIsNone(second["pagination"]["next_start"])
        self.assertEqual(second["pagination"]["returned_segments"], 1)

    def test_transcript_oversized_first_segment_terminates_pagination(self):
        # A segment longer than max_chars must not trap a paginating client:
        # the page returns the hard-truncated segment, consumes it, and the
        # next call advances until has_more turns false.
        json3 = (
            b'{"events": ['
            b'{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Aaaa"}]}, '
            b'{"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "Bbbb"}]}'
            b']}'
        )

        info = self.YTDLP_INFO_DE_ORIG

        def fake_run(command, capture_output, text, timeout, check):
            return subprocess.CompletedProcess(command, 0, json.dumps(info), "")

        def fake_urlopen(request, timeout=0):
            return Response(json3)

        service = YouTubeService(Settings(), urlopen=fake_urlopen, run=fake_run)
        transcript_parts = []
        continuation = None
        pages = 0
        while True:
            result = service.get_video_transcript(
                "dQw4w9WgXcQ", languages=["de"], include_timestamps=False,
                max_chars=2, continuation=continuation,
            )
            pages += 1
            pagination = result["pagination"]
            transcript_parts.append(result["transcript"])
            self.assertLess(pages, 5, "pagination must terminate on a 2-segment fixture")
            if not pagination["has_more"]:
                break
            self.assertIsNotNone(pagination["next_start"])
            self.assertIsNotNone(pagination["next_continuation"])
            continuation = pagination["next_continuation"]
        self.assertEqual(pages, 2)
        self.assertEqual(transcript_parts[0], "Aa")
        self.assertEqual(transcript_parts[1], "Bb")

    def test_transcript_oversized_last_segment_does_not_claim_more(self):
        # When only an oversized segment remains in the selected range, that
        # hard-truncated page is the final one: has_more must be false and no
        # next_start/next_continuation may be offered.
        json3 = (
            b'{"events": ['
            b'{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Aaaa"}]}, '
            b'{"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "Bbbb"}]}'
            b']}'
        )

        info = self.YTDLP_INFO_DE_ORIG

        def fake_run(command, capture_output, text, timeout, check):
            return subprocess.CompletedProcess(command, 0, json.dumps(info), "")

        def fake_urlopen(request, timeout=0):
            return Response(json3)

        service = YouTubeService(Settings(), urlopen=fake_urlopen, run=fake_run)
        first = service.get_video_transcript(
            "dQw4w9WgXcQ", languages=["de"], include_timestamps=False, max_chars=2, start="2",
        )
        self.assertEqual(first["transcript"], "Bb")
        self.assertFalse(first["pagination"]["has_more"])
        self.assertIsNone(first["pagination"]["next_start"])
        self.assertIsNone(first["pagination"]["next_continuation"])

    def test_transcript_continuation_round_trip_and_override(self):
        service = self._service_transcript()
        first = service.get_video_transcript(
            "dQw4w9WgXcQ", start="0", end="5", include_timestamps=False, max_chars=7,
        )
        continuation = first["pagination"]["next_continuation"]
        self.assertIsNotNone(continuation)
        decoded = decode_continuation(continuation)
        self.assertEqual(decoded, {"start_seconds": 2.0, "end_seconds": 5.0})

        second = service.get_video_transcript(
            "dQw4w9WgXcQ", start="0", end="2", continuation=continuation,
            include_timestamps=False, max_chars=100,
        )
        self.assertEqual(second["transcript"], "Welt")
        self.assertEqual(second["pagination"]["range_start"], 2.0)
        self.assertEqual(second["pagination"]["range_end"], 5.0)

    def test_transcript_invalid_continuation_and_time_range(self):
        service = self._service_transcript()
        with self.assertRaisesRegex(YouTubeBridgeError, "invalid_continuation"):
            service.get_video_transcript("dQw4w9WgXcQ", continuation="not-a-token")
        with self.assertRaisesRegex(YouTubeBridgeError, "invalid_time_range"):
            service.get_video_transcript("dQw4w9WgXcQ", start="2", end="1")

    def test_transcript_hard_max_chars_is_clamped(self):
        previous_max = os.environ.get("YOUTUBE_TRANSCRIPT_MAX_CHARS")
        previous_hard = os.environ.get("YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS")
        try:
            os.environ["YOUTUBE_TRANSCRIPT_MAX_CHARS"] = "200000"
            os.environ["YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS"] = "120000"
            settings = Settings.from_env()
            self.assertEqual(settings.transcript_max_chars, 120_000)
            self.assertEqual(settings.transcript_hard_max_chars, 120_000)
        finally:
            if previous_max is None:
                os.environ.pop("YOUTUBE_TRANSCRIPT_MAX_CHARS", None)
            else:
                os.environ["YOUTUBE_TRANSCRIPT_MAX_CHARS"] = previous_max
            if previous_hard is None:
                os.environ.pop("YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS", None)
            else:
                os.environ["YOUTUBE_TRANSCRIPT_HARD_MAX_CHARS"] = previous_hard

    def test_transcript_no_args_remains_backward_compatible(self):
        service = self._service_transcript()
        result = service.get_video_transcript("dQw4w9WgXcQ", include_timestamps=False, max_chars=100)
        self.assertEqual(result["transcript"], "Hallo\nWelt")
        self.assertFalse(result["truncated"])
        self.assertEqual(result["segment_count"], 2)
        self.assertEqual(result["max_chars"], 100)
        pagination = result["pagination"]
        self.assertFalse(pagination["has_more"])
        self.assertIsNone(pagination["next_start"])
        self.assertIsNone(pagination["next_continuation"])
        self.assertEqual(pagination["returned_segments"], 2)
        self.assertEqual(pagination["total_segments"], 2)
        self.assertEqual(pagination["returned_chars"], len("Hallo\nWelt"))
        self.assertEqual(pagination["total_chars"], len("Hallo\nWelt"))

    def test_default_languages_from_environment(self):
        previous = os.environ.get("YOUTUBE_DEFAULT_LANGUAGES")
        try:
            os.environ["YOUTUBE_DEFAULT_LANGUAGES"] = "fr, de"
            self.assertEqual(Settings.from_env().default_languages, ("fr", "de"))
        finally:
            if previous is None:
                os.environ.pop("YOUTUBE_DEFAULT_LANGUAGES", None)
            else:
                os.environ["YOUTUBE_DEFAULT_LANGUAGES"] = previous

    def test_bash_generator_writes_private_stack_without_echoing_secrets(self):
        script = Path(__file__).parents[1] / "generators" / "generate_docker-compose_for_ChatGPT_MCP.sh"
        script.read_bytes().decode("ascii")
        # new prompt flow: tags, youtube key, key confirm, ytdlp(no),
        # languages, max chars, tunnel id, runtime key, key confirm, proxy
        answers = "\n".join([
            "", "", "", "y", "n", "", "",
            "tunnel_0123456789abcdef0123456789abcdef",
            "sk-runtime-secret-value", "y", "",   # sk- prefix avoids the warning
        ])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "stack.yml"
            completed = subprocess.run(
                ["bash", str(script), "--output", str(output)],
                input=answers,
                text=True,
                capture_output=True,
                check=True,
            )
            generated = output.read_text()
            self.assertIn("ghcr.io/dracoform/chatgpt-youtube-mcp:latest", generated)
            self.assertIn("ghcr.io/dracoform/openai-mcp-tunnel:0.1.0", generated)
            self.assertIn("CONTROL_PLANE_TUNNEL_ID: 'tunnel_0123456789abcdef0123456789abcdef'", generated)
            self.assertIn("CONTROL_PLANE_API_KEY: 'sk-runtime-secret-value'", generated)
            self.assertNotIn("sk-runtime-secret-value", completed.stdout + completed.stderr)
            messages = completed.stdout + completed.stderr
            self.assertIn("[MANDATORY]", messages)
            self.assertIn("[OPTIONAL]", messages)
            self.assertIn("[CONDITIONAL]", messages)
            script_text = script.read_text(encoding="ascii")
            self.assertIn("[CONDITIONAL - MANDATORY]", script_text)
            self.assertNotIn("OPENAI_TUNNEL_ID", generated)
            self.assertNotIn("tunnel-config", generated)
            self.assertNotIn("ports:", generated)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_powershell_generator_contains_same_stack_contract(self):
        script = (Path(__file__).parents[1] / "generators" / "generate_docker-compose_for_ChatGPT_MCP.ps1").read_text()
        for required in (
            "ghcr.io/dracoform/chatgpt-youtube-mcp", "ghcr.io/dracoform/openai-mcp-tunnel",
            "YOUTUBE_API_KEY", "YOUTUBE_ENABLE_YTDLP", "YOUTUBE_DEFAULT_LANGUAGES",
            "CONTROL_PLANE_TUNNEL_ID", "CONTROL_PLANE_API_KEY", "MCP_SERVER_URL",
            "read_only: true", "cap_drop:", "no-new-privileges:true", "stop_grace_period: 30s",
            "NO_PROXY: 'youtube-mcp,localhost,127.0.0.1'",
        ):
            self.assertIn(required, script)
        self.assertNotIn("OPENAI_TUNNEL_ID", script)
        self.assertNotIn("tunnel-config", script)
        self.assertIn("Read-SecretText", script)
        self.assertIn("[CONDITIONAL - MANDATORY]", script)
        self.assertIn("[OPTIONAL]", script)


if __name__ == "__main__":
    unittest.main()
