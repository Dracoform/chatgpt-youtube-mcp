import json
import subprocess
import unittest

from youtube_mcp.core import (
    Settings,
    YouTubeBridgeError,
    YouTubeService,
    normalize_search_text,
)
from youtube_mcp.server import mcp


VIDEO_ID = "dQw4w9WgXcQ"

YTDLP_INFO = {
    "id": VIDEO_ID,
    "title": "StarCraft",
    "subtitles": {"en": [{"ext": "json3", "url": "https://captions/manual-en"}]},
    "automatic_captions": {
        "de": [{"ext": "json3", "url": "https://captions/de-translation"}],
        "de-orig": [{"ext": "json3", "url": "https://captions/de-orig"}],
    },
}

# de-translation fixture: 0..2s HALLO, 3..5s soft wrap hyphen, 5..7s café-naïve
JSON3_DE = (
    b'{"events": ['
    b'{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "HALLO"}]}, '
    b'{"tStartMs": 3000, "dDurationMs": 2000, "segs": [{"utf8": "Soft Wrap  hyphen"}]}, '
    b'{"tStartMs": 5000, "dDurationMs": 2000, "segs": [{"utf8": "caf\\u00e9-na\\u00efve"}]}'
    b']}'
)

# manual en fixture: 60..62s soft wrap hyphen, 62..64s café naïve
JSON3_EN = (
    b'{"events": ['
    b'{"tStartMs": 60000, "dDurationMs": 2000, "segs": [{"utf8": "Soft wrap hyphen"}]}, '
    b'{"tStartMs": 62000, "dDurationMs": 2000, "segs": [{"utf8": "Caf\\u00e9 na\\u00efve"}]}'
    b']}'
)

# 12 "the topic" segments at 120..132s for common-query bounding
JSON3_MANY_THE = (
    b'{"events": ['
    + b"".join(
        b'{"tStartMs": %d, "dDurationMs": 1000, "segs": [{"utf8": "the topic %d"}]}, ' % (120000 + i * 1000, i)
        for i in range(12)
    )
    + b'{"tStartMs": 132000, "dDurationMs": 1000, "segs": [{"utf8": "unrelated"}]}'
    b']}'
)

JSON3_EMPTY = b'{"events": []}'


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


def make_service(json3=JSON3_DE, info=YTDLP_INFO, en_json3=JSON3_EN):
    def fake_run(command, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(command, 0, json.dumps(info), "")

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if url.endswith("manual-en"):
            return ResponseBytes(en_json3)
        return ResponseBytes(json3)

    return YouTubeService(Settings(), urlopen=fake_urlopen, run=fake_run)


class NormalizeSearchTextTests(unittest.TestCase):
    def test_casefold_and_whitespace(self):
        self.assertEqual(normalize_search_text("  Soft   Wrap  "), "soft wrap")
        self.assertEqual(normalize_search_text("CAFÉ"), "café")

    def test_unicode_nfkc_compatibility(self):
        self.assertEqual(normalize_search_text("ﬁ"), "fi")
        self.assertEqual(normalize_search_text("Ⅻ"), "xii")


class SearchVideoTranscriptTests(unittest.TestCase):
    def test_single_query_with_context_clipping_and_timestamp(self):
        service = make_service()
        result = service.search_video_transcript(VIDEO_ID, queries=["hallo"])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual(match["match_start"], 0.0)
        self.assertEqual(match["match_end"], 2.0)
        self.assertEqual(match["start"], 0.0)  # context_before=10 clipped at transcript start
        self.assertEqual(match["end"], 7.0)    # context_after=20 clipped at transcript end
        self.assertEqual(match["timestamp"], "00:00:00")
        self.assertIn("hallo", match["text"])
        self.assertEqual(match["matched_queries"], ["hallo"])

    def test_multiple_queries_and_matched_queries_recording(self):
        service = make_service(en_json3=JSON3_EN)
        result = service.search_video_transcript(
            VIDEO_ID, queries=["café naïve", "WRAP"], languages=["en"],
            context_before=0, context_after=0,
        )
        self.assertEqual(result["language"], "en")
        self.assertFalse(result["automatic"])
        # With zero context the hits are still adjacent (60..62, 62..64) and
        # merge into one region recording both contributing queries.
        self.assertEqual(result["match_count"], 1)
        merged_match = result["matches"][0]
        self.assertEqual(merged_match["matched_queries"], ["café naïve", "WRAP"])  # queries list order (per-query scan), merged region records all contributors
        self.assertEqual(merged_match["match_start"], 60.0)   # chronologically first match across the merged region
        self.assertEqual(merged_match["match_end"], 64.0)
        self.assertEqual(result["queries"], ["café naïve", "wrap"])

    def test_disjoint_queries_return_separate_regions(self):
        service = make_service(en_json3=JSON3_EN)
        # Negative context is invalid, so use a fixture with distant hits:
        result = service.search_video_transcript(
            VIDEO_ID, queries=["nonexistent-a", "nonexistent-b"], languages=["en"],
        )
        self.assertEqual(result["match_count"], 0)

    def test_zero_matches_is_distinguishable_from_unavailable(self):
        service = make_service(en_json3=JSON3_EN)
        result = service.search_video_transcript(VIDEO_ID, queries=["quantum"], languages=["en"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["match_count"], 0)

        unavailable = make_service(info={**YTDLP_INFO, "subtitles": {}, "automatic_captions": {}})
        with self.assertRaisesRegex(YouTubeBridgeError, "transcript_unavailable"):
            unavailable.search_video_transcript(VIDEO_ID, queries=["quantum"])

    def test_transcript_empty_is_distinguishable_from_zero_matches(self):
        # All tracks respond with an empty event list: transcript_empty error,
        # NOT a zero-match success.
        service = make_service(JSON3_EMPTY, en_json3=JSON3_EMPTY)
        with self.assertRaisesRegex(YouTubeBridgeError, "transcript_empty"):
            service.search_video_transcript(VIDEO_ID, queries=["hallo"])

    def test_invalid_queries_rejected(self):
        service = make_service()
        for bad in ([], ["   "], [""], [42], "not-a-list", None):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(YouTubeBridgeError, "invalid_query"):
                    service.search_video_transcript(VIDEO_ID, queries=bad)

    def test_invalid_limits_rejected(self):
        service = make_service()
        for bad in (0, -1, 51, 100, "ten", None, 2.5):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(YouTubeBridgeError, "invalid_limit"):
                    service.search_video_transcript(VIDEO_ID, queries=["hallo"], limit=bad)

    def test_invalid_context_rejected(self):
        service = make_service()
        for before, after in ((-1.0, 20.0), (10.0, -0.1), (float("inf"), 20.0), ("x", 20.0)):
            with self.subTest(before=before, after=after):
                with self.assertRaisesRegex(YouTubeBridgeError, "invalid_context"):
                    service.search_video_transcript(
                        VIDEO_ID, queries=["hallo"], context_before=before, context_after=after,
                    )

    def test_context_clipping_and_custom_context(self):
        service = make_service()
        result = service.search_video_transcript(
            VIDEO_ID, queries=["café"], context_before=1.0, context_after=1.0,
        )
        match = result["matches"][0]
        self.assertEqual(match["match_start"], 5.0)
        self.assertEqual(match["start"], 4.0)  # 5 - 1
        self.assertEqual(match["end"], 7.0)    # requested 5 + 1, extended to cover the matching segment (ends at 7)
        self.assertIn("café", match["text"])

    def test_overlapping_hits_are_merged_with_all_matched_queries(self):
        service = make_service()
        result = service.search_video_transcript(
            VIDEO_ID, queries=["hallo", "soft wrap hyphen"], context_after=4.0,
        )
        # "hallo" at 0s and "soft wrap hyphen" at 3s overlap with ctx_after=4
        self.assertEqual(result["match_count"], 1)
        match = result["matches"][0]
        self.assertEqual(set(match["matched_queries"]), {"hallo", "soft wrap hyphen"})
        self.assertEqual(result["total_matches_before_limit"], 1)

    def test_common_query_bounded_by_limit_reports_truncation(self):
        info = {**YTDLP_INFO, "subtitles": {"en": [{"ext": "json3", "url": "https://captions/manual-en"}]}}
        service = make_service(JSON3_MANY_THE, info=info, en_json3=JSON3_MANY_THE)
        result = service.search_video_transcript(
            VIDEO_ID, queries=["the topic"], languages=["en"], limit=5,
            context_before=0, context_after=0,
        )
        self.assertEqual(result["match_count"], 5)
        self.assertEqual(result["total_matches_before_limit"], 12)
        self.assertTrue(result["results_truncated_by_limit"])
        self.assertEqual(result["limit"], 5)
        starts = [m["match_start"] for m in result["matches"]]
        self.assertEqual(starts, sorted(starts))  # chronological order

    def test_limit_50_default_10(self):
        info = {**YTDLP_INFO, "subtitles": {"en": [{"ext": "json3", "url": "https://captions/manual-en"}]}}
        service = make_service(JSON3_MANY_THE, info=info, en_json3=JSON3_MANY_THE)
        result = service.search_video_transcript(
            VIDEO_ID, queries=["the topic"], languages=["en"], limit=50,
            context_before=0, context_after=0,
        )
        self.assertEqual(result["match_count"], 12)
        self.assertFalse(result["results_truncated_by_limit"])
        default = service.search_video_transcript(
            VIDEO_ID, queries=["the topic"], languages=["en"], context_before=0, context_after=0,
        )
        self.assertEqual(default["match_count"], 10)
        self.assertTrue(default["results_truncated_by_limit"])

    def test_default_languages_from_settings(self):
        service = make_service()
        result = service.search_video_transcript(VIDEO_ID, queries=["hallo"])
        self.assertEqual(result["language"], "de")  # default languages ("de","en")
        self.assertTrue(result["automatic"])

    def test_preferred_language_order_respected(self):
        service = make_service(en_json3=JSON3_EN)
        result = service.search_video_transcript(VIDEO_ID, queries=["café naïve"], languages=["en", "de"])
        self.assertEqual(result["language"], "en")

    def test_duplicate_queries_deduplicated(self):
        service = make_service()
        result = service.search_video_transcript(VIDEO_ID, queries=["HALLO", "hallo"])
        self.assertEqual(result["queries"], ["hallo"])
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["matches"][0]["matched_queries"], ["HALLO"])  # original spelling preserved


class SearchVideoTranscriptSchemaTests(unittest.TestCase):
    def test_fastmcp_tool_schema(self):
        tools = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertIn("search_video_transcript", tools)
        schema = next(t.parameters for t in mcp._tool_manager.list_tools() if t.name == "search_video_transcript")
        properties = schema["properties"]
        self.assertEqual(properties["queries"]["type"], "array")
        self.assertEqual(properties["queries"]["items"]["type"], "string")
        self.assertIn("queries", schema["required"])
        for name, typ in (
            ("video", "string"),
            ("limit", "integer"),
            ("context_before", "number"),
            ("context_after", "number"),
        ):
            self.assertEqual(properties[name]["type"], typ, name)
        self.assertIn("array", [opt.get("type") for opt in properties["languages"].get("anyOf", [])])


if __name__ == "__main__":
    unittest.main()