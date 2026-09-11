import io
import json
import subprocess
import sys
import unittest

from youtube_mcp.core import (
    Settings,
    YouTubeBridgeError,
    YouTubeService,
    error_result,
)


VIDEO_ID = "dQw4w9WgXcQ"
PLAYLIST_ID = "PLP1Zv1tsQgEhmO_osGoRLreKh1OuXt0zE"
CAPTION_JSON3 = (
    b'{"events": ['
    b'{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "Caption text"}]}'
    b']}'
)
EMPTY_JSON3 = b'{"events": []}'


class ResponseBytes:
    """Minimal context-manager response stub."""

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.data


def completed_process(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        [sys.executable, "-m", "yt_dlp"],
        returncode,
        stdout,
        stderr,
    )


def valid_video_info(video_id=VIDEO_ID):
    return {
        "id": video_id,
        "title": "Failure semantics fixture",
        "subtitles": {},
        "automatic_captions": {},
    }


class YtDlpFailureClassificationTests(unittest.TestCase):
    def test_sign_in_and_bot_challenges_are_not_retryable(self):
        messages = (
            "Sign in to confirm you are not a bot",
            "Please confirm you are not a robot",
            "Sign in to verify your account",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(
                    YouTubeService._classify_ytdlp_failure(message),
                    ("sign_in_required", False),
                )

    def test_rate_limit_failures_are_retryable(self):
        messages = (
            "HTTP Error 429: Too Many Requests",
            "ERROR: Too Many Requests",
            "ERROR: rate limit exceeded",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(
                    YouTubeService._classify_ytdlp_failure(message),
                    ("rate_limited", True),
                )

    def test_unclassified_failure_is_retryable(self):
        self.assertEqual(
            YouTubeService._classify_ytdlp_failure("ERROR: generic extraction failure"),
            ("ytdlp_failed", True),
        )


class YtDlpJsonFailureTests(unittest.TestCase):
    def test_nonzero_exit_raises_classified_bridge_errors(self):
        cases = (
            (
                "ERROR: [youtube] Sign in to confirm you are not a bot",
                "sign_in_required",
                False,
            ),
            (
                "ERROR: HTTP Error 429: Too Many Requests",
                "rate_limited",
                True,
            ),
        )
        for stderr, expected_code, expected_retryable in cases:
            with self.subTest(stderr=stderr):
                def fake_run(command, capture_output, text, timeout, check):
                    self.assertTrue(capture_output)
                    self.assertTrue(text)
                    self.assertEqual(timeout, 90)
                    self.assertFalse(check)
                    return completed_process(stderr=stderr, returncode=1)

                service = YouTubeService(Settings(api_key=None), run=fake_run)
                with self.assertRaises(YouTubeBridgeError) as raised:
                    service._yt_dlp_json(f"https://www.youtube.com/watch?v={VIDEO_ID}")
                exc = raised.exception
                self.assertEqual(exc.code, expected_code)
                self.assertEqual(exc.retryable, expected_retryable)

                envelope = error_result(exc)
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["code"], expected_code)
                self.assertEqual(envelope["error"]["retryable"], expected_retryable)
                self.assertEqual(envelope["error"]["message"], stderr)

    def test_blank_stdout_and_stderr_raise_nonempty_default_failure(self):
        cases = (
            ("", ""),
            ("", "\n"),
            ("\n", ""),
            ("\n", "\n"),
        )
        for stdout, stderr in cases:
            with self.subTest(stdout=repr(stdout), stderr=repr(stderr)):
                def fake_run(command, capture_output, text, timeout, check):
                    return completed_process(stdout=stdout, stderr=stderr, returncode=1)

                service = YouTubeService(Settings(), run=fake_run)
                with self.assertRaises(YouTubeBridgeError) as raised:
                    service._yt_dlp_json(VIDEO_ID)
                self.assertEqual(raised.exception.code, "ytdlp_failed")
                self.assertTrue(raised.exception.retryable)
                self.assertTrue(str(raised.exception).strip())

    def test_subprocess_timeout_is_classified_as_retryable(self):
        def fake_run(command_arg, capture_output, text, timeout, check):
            self.assertEqual(timeout, 90)
            raise subprocess.TimeoutExpired(command_arg, 90)

        service = YouTubeService(Settings(), run=fake_run)
        with self.assertRaises(YouTubeBridgeError) as raised:
            service._yt_dlp_json(VIDEO_ID)
        exc = raised.exception
        self.assertEqual(exc.code, "ytdlp_timeout")
        self.assertTrue(exc.retryable)

    def test_exit_zero_malformed_json_is_invalid_output(self):
        for stdout in ("", "not json", "   "):
            with self.subTest(stdout=repr(stdout)):
                def fake_run(command, capture_output, text, timeout, check):
                    return completed_process(stdout=stdout, returncode=0)

                service = YouTubeService(Settings(), run=fake_run)
                with self.assertRaises(YouTubeBridgeError) as raised:
                    service._yt_dlp_json(VIDEO_ID)
                self.assertEqual(raised.exception.code, "ytdlp_invalid_output")
                self.assertTrue(raised.exception.retryable)

    def test_exit_zero_json_non_object_is_invalid_output(self):
        for stdout in ("[]", "42", '"text"'):
            with self.subTest(stdout=stdout):
                def fake_run(command, capture_output, text, timeout, check):
                    return completed_process(stdout=stdout, returncode=0)

                service = YouTubeService(Settings(), run=fake_run)
                with self.assertRaises(YouTubeBridgeError) as raised:
                    service._yt_dlp_json(VIDEO_ID)
                self.assertEqual(raised.exception.code, "ytdlp_invalid_output")
                self.assertTrue(raised.exception.retryable)


class VideoInfoCacheValidationTests(unittest.TestCase):
    def test_degraded_exit_zero_result_is_not_cached(self):
        payload = {"title": "degraded result"}
        run_calls = 0

        def fake_run(command, capture_output, text, timeout, check):
            nonlocal run_calls
            run_calls += 1
            return completed_process(stdout=json.dumps(payload), returncode=0)

        service = YouTubeService(Settings(), run=fake_run)
        with self.assertRaises(YouTubeBridgeError) as degraded:
            service._get_video_info(VIDEO_ID)
        self.assertEqual(degraded.exception.code, "ytdlp_invalid_output")
        self.assertEqual(service._video_info_cache, {})

        payload = valid_video_info()
        info = service._get_video_info(VIDEO_ID)
        self.assertEqual(info["id"], VIDEO_ID)
        self.assertEqual(run_calls, 2)
        self.assertEqual(service._video_info_cache[VIDEO_ID][1], info)

    def test_empty_exit_zero_object_is_degraded_output(self):
        def fake_run(command, capture_output, text, timeout, check):
            return completed_process(stdout="{}", returncode=0)

        service = YouTubeService(Settings(), run=fake_run)
        with self.assertRaises(YouTubeBridgeError) as raised:
            service._get_video_info(VIDEO_ID)
        self.assertEqual(raised.exception.code, "ytdlp_invalid_output")
        self.assertEqual(service._video_info_cache, {})

    def test_valid_id_bearing_info_is_cached_within_ttl(self):
        run_calls = 0

        def fake_run(command, capture_output, text, timeout, check):
            nonlocal run_calls
            run_calls += 1
            return completed_process(
                stdout=json.dumps(valid_video_info()),
                returncode=0,
            )

        service = YouTubeService(Settings(), run=fake_run)
        first = service._get_video_info(VIDEO_ID)
        second = service._get_video_info(VIDEO_ID)
        self.assertIs(first, second)
        self.assertEqual(run_calls, 1)
        self.assertEqual(service._video_info_cache[VIDEO_ID][1]["id"], VIDEO_ID)


class CaptionFailureSemanticsTests(unittest.TestCase):
    def test_extraction_failure_never_becomes_caption_absence(self):
        def fake_urlopen(request, timeout=0):
            self.fail("urlopen must not be called")

        def fake_run(command, capture_output, text, timeout, check):
            return completed_process(
                stderr="ERROR: HTTP Error 429: Too Many Requests",
                returncode=1,
            )

        service = YouTubeService(
            Settings(api_key=None),
            urlopen=fake_urlopen,
            run=fake_run,
        )
        operations = (
            (
                "get_video_transcript",
                lambda: service.get_video_transcript(VIDEO_ID),
            ),
            (
                "search_video_transcript",
                lambda: service.search_video_transcript(
                    VIDEO_ID,
                    queries=["rate limited"],
                ),
            ),
            (
                "list_caption_tracks",
                lambda: service.list_caption_tracks(VIDEO_ID),
            ),
            ("get_video", lambda: service.get_video(VIDEO_ID)),
        )
        for name, operation in operations:
            with self.subTest(operation=name):
                with self.assertRaises(YouTubeBridgeError) as raised:
                    operation()
                envelope = error_result(raised.exception)
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["code"], "rate_limited")
                self.assertNotEqual(envelope["error"]["code"], "transcript_unavailable")
                self.assertTrue(envelope["error"]["retryable"])

    def test_caption_download_failure_is_classified(self):
        def fake_urlopen(request, timeout=90):
            self.fail("urlopen must not be called")

        run_calls = 0

        def fake_run(command, capture_output, text, timeout, check):
            nonlocal run_calls
            run_calls += 1
            return completed_process(
                stderr="ERROR: HTTP Error 429: Too Many Requests",
                returncode=1,
            )

        operations = (
            (
                "get_video_transcript",
                lambda service: service.get_video_transcript(VIDEO_ID),
            ),
            (
                "search_video_transcript",
                lambda service: service.search_video_transcript(
                    VIDEO_ID,
                    queries=["rate limited"],
                ),
            ),
            (
                "list_caption_tracks",
                lambda service: service.list_caption_tracks(VIDEO_ID),
            ),
            ("get_video", lambda service: service.get_video(VIDEO_ID)),
        )
        for name, operation in operations:
            with self.subTest(operation=name):
                service = YouTubeService(
                    Settings(api_key=None),
                    urlopen=fake_urlopen,
                    run=fake_run,
                )
                with self.assertRaises(YouTubeBridgeError) as raised:
                    operation(service)
                envelope = error_result(raised.exception)
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["code"], "rate_limited")
                self.assertNotEqual(envelope["error"]["code"], "transcript_unavailable")
                self.assertTrue(envelope["error"]["retryable"])
        self.assertEqual(run_calls, len(operations))

    def test_plain_caption_download_failure_is_not_caption_absence(self):
        info = {
            **valid_video_info(),
            "subtitles": {
                "en": [{"ext": "json3", "url": "https://captions.example/only"}],
            },
        }

        def fake_run(command, capture_output, text, timeout, check):
            return completed_process(stdout=json.dumps(info), returncode=0)

        def fake_urlopen(request, timeout=0):
            self.assertEqual(timeout, 60)
            raise RuntimeError("caption endpoint exploded")

        service = YouTubeService(
            Settings(),
            urlopen=fake_urlopen,
            run=fake_run,
        )
        with self.assertRaises(YouTubeBridgeError) as raised:
            service.get_video_transcript(VIDEO_ID, languages=["en"])
        self.assertEqual(raised.exception.code, "upstream_request_failed")
        self.assertTrue(raised.exception.retryable)
        self.assertIn("caption endpoint exploded", str(raised.exception))

    def test_caption_bridge_error_preserves_code_and_retryability(self):
        info = {
            **valid_video_info(),
            "subtitles": {
                "en": [{"ext": "json3", "url": "https://captions.example/only"}],
            },
        }

        def fake_run(command, capture_output, text, timeout, check):
            return completed_process(stdout=json.dumps(info), returncode=0)

        def fake_urlopen(request, timeout=0):
            raise YouTubeBridgeError(
                "upstream_request_failed",
                "Upstream request failed: boom",
                retryable=True,
            )

        service = YouTubeService(
            Settings(),
            urlopen=fake_urlopen,
            run=fake_run,
        )
        with self.assertRaises(YouTubeBridgeError) as raised:
            service.get_video_transcript(VIDEO_ID, languages=["en"])
        exc = raised.exception
        self.assertEqual(exc.code, "upstream_request_failed")
        self.assertTrue(exc.retryable)
        self.assertIn("Upstream request failed: boom", str(exc))
        self.assertIsInstance(exc.__cause__, YouTubeBridgeError)
        self.assertEqual(exc.__cause__.code, "upstream_request_failed")

    def test_caption_candidate_falls_back_after_one_download_failure(self):
        info = {
            **valid_video_info(),
            "subtitles": {
                "en": [
                    {"ext": "json3", "url": "https://captions.example/first"},
                    {"ext": "json3", "url": "https://captions.example/second"},
                ],
            },
        }
        requested_urls = []

        def fake_run(command, capture_output, text, timeout, check):
            return completed_process(stdout=json.dumps(info), returncode=0)

        def fake_urlopen(request, timeout=0):
            requested_urls.append(request.full_url)
            if len(requested_urls) == 1:
                raise RuntimeError("first caption failed")
            return ResponseBytes(CAPTION_JSON3)

        service = YouTubeService(
            Settings(),
            urlopen=fake_urlopen,
            run=fake_run,
        )
        result = service.get_video_transcript(
            VIDEO_ID,
            languages=["en"],
            include_timestamps=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["transcript"], "Caption text")
        self.assertEqual(result["segment_count"], 1)
        self.assertEqual(
            requested_urls,
            ["https://captions.example/first", "https://captions.example/second"],
        )

    def test_cleanly_parsed_empty_caption_tracks_raise_transcript_empty(self):
        info = {
            **valid_video_info(),
            "subtitles": {
                "en": [{"ext": "json3", "url": "https://captions.example/manual"}],
            },
            "automatic_captions": {
                "de": [{"ext": "json3", "url": "https://captions.example/automatic"}],
            },
        }

        def fake_run(command, capture_output, text, timeout, check):
            return completed_process(stdout=json.dumps(info), returncode=0)

        def fake_urlopen(request, timeout=0):
            return ResponseBytes(EMPTY_JSON3)

        service = YouTubeService(
            Settings(),
            urlopen=fake_urlopen,
            run=fake_run,
        )
        with self.assertRaises(YouTubeBridgeError) as raised:
            service.get_video_transcript(VIDEO_ID)
        self.assertEqual(raised.exception.code, "transcript_empty")
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("transcript_fetch_failed", str(raised.exception))


class PlaylistFallbackFailureTests(unittest.TestCase):
    def test_playlist_extraction_failure_is_classified_not_missing(self):
        def fake_run(command, capture_output, text, timeout, check):
            self.assertIn("--flat-playlist", command)
            return completed_process(
                stderr="ERROR: HTTP Error 429: Too Many Requests",
                returncode=1,
            )

        service = YouTubeService(Settings(), run=fake_run)
        with self.assertRaises(YouTubeBridgeError) as raised:
            service.get_playlist(PLAYLIST_ID)
        envelope = error_result(raised.exception)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "rate_limited")
        self.assertNotEqual(envelope["error"]["code"], "playlist_not_found")
        self.assertTrue(envelope["error"]["retryable"])


if __name__ == "__main__":
    unittest.main()
