import io
import json
import subprocess
import unittest

from youtube_mcp.core import (
    Settings,
    YouTubeBridgeError,
    YouTubeService,
    parse_playlist_reference,
)
from youtube_mcp.server import mcp


PLAYLIST_ID = "PLP1Zv1tsQgEhmO_osGoRLreKh1OuXt0zE"
VIDEO_ID = "rZ_9TQo0N6g"
OTHER_VIDEO_ID = "dQw4w9WgXcQ"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ---- reference parsing ------------------------------------------------------


class ParsePlaylistReferenceTests(unittest.TestCase):
    def test_bare_playlist_ids(self):
        for playlist_id in (
            "PLP1Zv1tsQgEhmO_osGoRLreKh1OuXt0zE",
            "UU_x5XG1OV2P6uZZ5FSM9Ttw",  # uploads playlist
            "RDMMabc1234567",  # mix
            "OLAK5uy_abcdefghijklmnopqr",  # album
        ):
            with self.subTest(playlist_id=playlist_id):
                parsed = parse_playlist_reference(playlist_id)
                self.assertEqual(parsed["kind"], "id")
                self.assertEqual(parsed["playlist_id"], playlist_id)
                self.assertIsNone(parsed["video_id"])
                self.assertIsNone(parsed["url_index"])

    def test_playlist_url(self):
        parsed = parse_playlist_reference(f"https://www.youtube.com/playlist?list={PLAYLIST_ID}")
        self.assertEqual(parsed["playlist_id"], PLAYLIST_ID)
        self.assertIsNone(parsed["video_id"])
        self.assertIsNone(parsed["url_index"])

    def test_watch_url_with_list_and_index(self):
        url = f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}&index=7"
        parsed = parse_playlist_reference(url)
        self.assertEqual(parsed["kind"], "url")
        self.assertEqual(parsed["playlist_id"], PLAYLIST_ID)
        self.assertEqual(parsed["video_id"], VIDEO_ID)
        self.assertEqual(parsed["url_index"], 7)

    def test_watch_url_without_index(self):
        parsed = parse_playlist_reference(f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}")
        self.assertIsNone(parsed["url_index"])

    def test_youtu_be_short_link_with_list(self):
        parsed = parse_playlist_reference(f"https://youtu.be/{VIDEO_ID}?list={PLAYLIST_ID}")
        self.assertEqual(parsed["playlist_id"], PLAYLIST_ID)
        self.assertEqual(parsed["video_id"], VIDEO_ID)

    def test_shorts_url_with_list(self):
        parsed = parse_playlist_reference(f"https://www.youtube.com/shorts/{VIDEO_ID}?list={PLAYLIST_ID}")
        self.assertEqual(parsed["video_id"], VIDEO_ID)
        self.assertEqual(parsed["playlist_id"], PLAYLIST_ID)

    def test_bare_video_url_without_list_is_rejected(self):
        # No list= context: playlist membership must NOT be inferred.
        with self.assertRaisesRegex(YouTubeBridgeError, "cannot be inferred from a bare video URL"):
            parse_playlist_reference(f"https://www.youtube.com/watch?v={VIDEO_ID}")

    def test_non_youtube_host_is_rejected(self):
        with self.assertRaisesRegex(YouTubeBridgeError, "Expected a YouTube playlist URL"):
            parse_playlist_reference(f"https://example.org/playlist?list={PLAYLIST_ID}")

    def test_unrecognized_list_value_is_rejected(self):
        with self.assertRaisesRegex(YouTubeBridgeError, "not a recognized playlist ID"):
            parse_playlist_reference("https://www.youtube.com/playlist?list=not-a-playlist")

    def test_invalid_or_missing_index_is_context_only(self):
        parsed = parse_playlist_reference(f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}&index=abc")
        self.assertIsNone(parsed["url_index"])
        parsed = parse_playlist_reference(f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}&index=0")
        self.assertIsNone(parsed["url_index"])


# ---- official Data API path -------------------------------------------------


def api_playlist_items_page(items, next_page_token=None):
    payload = {"items": items}
    if next_page_token:
        payload["nextPageToken"] = next_page_token
    return payload


def api_playlist_item(snippet_position, video_id, published_at="2026-01-01T00:00:00Z"):
    return {
        "snippet": {
            "position": snippet_position,
            "title": f"Video {snippet_position}",
            "publishedAt": "2026-03-01T00:00:00Z",  # when it was ADDED to the playlist
            "videoOwnerChannelTitle": "Owner Channel",
            "videoOwnerChannelId": "UCOWNER0000000000000000A",
            "videoPublishedAt": published_at,
            "resourceId": {"videoId": video_id},
        },
        "contentDetails": {"videoId": video_id, "videoPublishedAt": published_at},
    }


def api_playlist_metadata(item_count):
    return {
        "items": [{
            "id": PLAYLIST_ID,
            "snippet": {
                "title": "My Course",
                "description": "A course playlist",
                "channelId": "UCCHANNEL0000000000000000B",
                "channelTitle": "Playlist Owner",
                "publishedAt": "2026-02-01T00:00:00Z",
            },
            "contentDetails": {"itemCount": item_count},
        }],
    }


class FakeApi:
    """Routes _api_get style URL fetches by path."""

    def __init__(self, playlists_payload, item_pages):
        self.playlists_payload = playlists_payload
        self.item_pages = item_pages  # list of payloads, served in order
        self.requests = []

    def __call__(self, request, timeout=0):
        url = request.full_url
        self.requests.append(url)
        if "playlists?" in url:
            return Response(json.dumps(self.playlists_payload).encode())
        return Response(json.dumps(self.item_pages[len(self.requests) - 2]).encode())


class GetPlaylistApiTests(unittest.TestCase):
    def test_official_playlist_metadata_and_ordered_entries(self):
        items = [api_playlist_item(i, f"vid{i:09d}0") for i in range(3)]
        fake = FakeApi(api_playlist_metadata(3), [api_playlist_items_page(items)])
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        result = service.get_playlist(PLAYLIST_ID)
        self.assertTrue(result["ok"])
        self.assertTrue(result["provenance"]["official"])
        playlist = result["playlist"]
        self.assertEqual(playlist["title"], "My Course")
        self.assertEqual(playlist["item_count"], 3)
        self.assertEqual(playlist["channel_title"], "Playlist Owner")
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual([item["position"] for item in result["items"]], [1, 2, 3])
        self.assertEqual(result["items"][0]["video_id"], "vid0000000000")
        self.assertEqual(result["items"][0]["channel_title"], "Owner Channel")
        self.assertEqual(result["items"][0]["video_published_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(result["items"][0]["playlist_added_at"], "2026-03-01T00:00:00Z")
        self.assertFalse(result["pagination"]["has_more"])
        self.assertEqual(result["pagination"]["total_items"], 3)

    def test_watch_url_with_list_context_preserves_video_and_url_index(self):
        fake = FakeApi(api_playlist_metadata(1), [api_playlist_items_page([api_playlist_item(0, VIDEO_ID)])])
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        url = f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}&index=7"
        result = service.get_playlist(url)
        self.assertEqual(result["playlist_reference"]["video_id"], VIDEO_ID)
        self.assertEqual(result["playlist_reference"]["url_index_context"], 7)

    def test_missing_playlist_raises(self):
        fake = FakeApi({"items": []}, [])
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        with self.assertRaisesRegex(YouTubeBridgeError, "playlist_not_found|no matching public playlist|no public playlist"):
            service.get_playlist(PLAYLIST_ID)

    def test_private_and_deleted_items_keep_positions(self):
        items = [
            api_playlist_item(0, "vid000000001"),
            {"snippet": {"position": 1, "title": "Private video", "publishedAt": "2026-03-01T00:00:00Z"}, "contentDetails": {}},
            api_playlist_item(2, "vid000000003"),
        ]
        fake = FakeApi(api_playlist_metadata(3), [api_playlist_items_page(items)])
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        result = service.get_playlist(PLAYLIST_ID)
        self.assertEqual([item["position"] for item in result["items"]], [1, 2, 3])
        self.assertIsNone(result["items"][1]["video_id"])
        self.assertEqual(result["items"][1]["availability_note"], "Private video")
        self.assertNotIn("url", result["items"][1])

    def test_pagination_across_multiple_api_pages(self):
        # 3 pages of 50 items each = 120 items; the service must follow
        # nextPageToken and assign contiguous 1-based positions.
        page_size = 50
        pages = []
        for page_index in range(3):
            count = page_size if page_index < 2 else 20
            items = [api_playlist_item(i, f"vid{page_index:03d}{i:07d}") for i in range(count)]
            token = f"token{page_index}" if page_index < 2 else None
            pages.append(api_playlist_items_page(items, token))
        fake = FakeApi(api_playlist_metadata(120), pages)
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        result = service.get_playlist(PLAYLIST_ID)
        self.assertEqual(len(result["items"]), 120)
        positions = [item["position"] for item in result["items"]]
        self.assertEqual(positions, list(range(1, 121)))
        self.assertEqual(result["items"][0]["video_id"], "vid0000000000")
        self.assertEqual(result["items"][49]["video_id"], "vid0000000049")
        self.assertEqual(result["items"][50]["video_id"], "vid0010000000")
        # page requests stayed within the documented maxResults bound
        for url in fake.requests:
            if "playlistItems?" in url:
                self.assertIn("maxResults=50", url)

    def test_enumeration_is_bounded_at_500_items(self):
        # One page would normally contain only 50; the service must stop at
        # its own 500-item bound even if YouTube kept returning pages.
        class EndlessPages(FakeApi):
            def __call__(self, request, timeout=0):
                url = request.full_url
                self.requests.append(url)
                if "playlists?" in url:
                    return Response(json.dumps(api_playlist_metadata(10000)).encode())
                items = [api_playlist_item(i, f"vid{i:09d}x") for i in range(50)]
                return Response(json.dumps(api_playlist_items_page(items, "always-more")).encode())

        service = YouTubeService(Settings(api_key="secret"), urlopen=EndlessPages(api_playlist_metadata(10000), []))
        result = service.get_playlist(PLAYLIST_ID)
        self.assertEqual(len(result["items"]), 500)
        self.assertEqual(result["items"][-1]["position"], 500)

    def test_find_playlist_position_ignores_url_index(self):
        # URL claims index=7, but enumeration says the video is at position 2.
        items = [api_playlist_item(0, OTHER_VIDEO_ID), api_playlist_item(1, VIDEO_ID)]
        fake = FakeApi(api_playlist_metadata(2), [api_playlist_items_page(items)])
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        url = f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}&index=7"
        result = service.find_playlist_position(url)
        self.assertEqual(result["position"], 2)
        self.assertEqual(result["url_index_context"], 7)

    def test_find_playlist_position_requires_list_context(self):
        service = YouTubeService(Settings(api_key="secret"))
        with self.assertRaisesRegex(YouTubeBridgeError, "cannot be inferred from a bare video URL"):
            service.find_playlist_position(f"https://www.youtube.com/watch?v={VIDEO_ID}")

    def test_find_playlist_position_video_not_in_playlist(self):
        items = [api_playlist_item(0, OTHER_VIDEO_ID)]
        fake = FakeApi(api_playlist_metadata(1), [api_playlist_items_page(items)])
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        url = f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}"
        with self.assertRaisesRegex(YouTubeBridgeError, "not found in playlist"):
            service.find_playlist_position(url)

    def test_long_descriptions_are_bounded(self):
        payload = api_playlist_metadata(1)
        payload["items"][0]["snippet"]["description"] = "x" * 5000
        fake = FakeApi(payload, [api_playlist_items_page([api_playlist_item(0, VIDEO_ID)])])
        service = YouTubeService(Settings(api_key="secret"), urlopen=fake)
        result = service.get_playlist(PLAYLIST_ID)
        self.assertLessEqual(len(result["playlist"]["description"]), 1000)


# ---- yt-dlp fallback path ---------------------------------------------------


def ytdlp_flat_playlist_payload():
    # Field set mirrors a real yt-dlp --flat-playlist extraction (verified
    # live shape: no per-video publish dates, no playlist added_at, no notes).
    def entry(index):
        return {
            "_type": "url",
            "id": f"vid{index:09d}0",
            "title": f"Episode {index}",
            "url": f"https://www.youtube.com/watch?v=vid{index:09d}0",
            "channel": "Some Channel",
            "channel_id": "UCSOMECHANNEL0000000000000C",
            "duration": 300.0 + index,
        }

    return {
        "_type": "playlist",
        "id": PLAYLIST_ID,
        "title": "Fallback Course",
        "description": "A playlist enumerated without an API key",
        "channel": "Playlist Owner",
        "channel_id": "UCCHANNEL0000000000000000B",
        "playlist_count": 2,
        "webpage_url": f"https://www.youtube.com/playlist?list={PLAYLIST_ID}",
        "entries": [entry(1), entry(2)],
    }


class GetPlaylistFallbackTests(unittest.TestCase):
    def _service(self, payload=None, enable_ytdlp=True):
        payload = payload if payload is not None else ytdlp_flat_playlist_payload()

        def fake_run(command, capture_output, text, timeout, check):
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        return YouTubeService(Settings(enable_ytdlp=enable_ytdlp), run=fake_run)

    def test_fallback_metadata_and_entries(self):
        service = self._service()
        result = service.get_playlist(PLAYLIST_ID)
        self.assertTrue(result["ok"])
        self.assertFalse(result["provenance"]["official"])
        self.assertIn("not configured", result["provenance"]["note"])
        playlist = result["playlist"]
        self.assertEqual(playlist["title"], "Fallback Course")
        self.assertEqual(playlist["item_count"], 2)
        # yt-dlp does not expose the playlist creation date; honestly omitted.
        self.assertIsNone(playlist["published_at"])
        items = result["items"]
        self.assertEqual([item["position"] for item in items], [1, 2])
        self.assertEqual(items[0]["video_id"], "vid0000000010")
        self.assertEqual(items[0]["title"], "Episode 1")
        self.assertEqual(items[0]["channel_title"], "Some Channel")
        self.assertEqual(items[0]["duration_seconds"], 301.0)
        # Fields the flat fallback cannot know are omitted, not invented.
        self.assertNotIn("video_published_at", items[0])
        self.assertNotIn("playlist_added_at", items[0])
        self.assertNotIn("note", items[0])

    def test_fallback_accepts_watch_url_with_list(self):
        service = self._service()
        url = f"https://www.youtube.com/watch?v={VIDEO_ID}&list={PLAYLIST_ID}&index=7"
        result = service.get_playlist(url)
        self.assertEqual(result["playlist_reference"]["video_id"], VIDEO_ID)
        self.assertEqual(result["playlist_reference"]["url_index_context"], 7)
        self.assertEqual(result["items"][0]["position"], 1)

    def test_fallback_ytdlp_disabled_by_policy(self):
        service = self._service(enable_ytdlp=False)
        with self.assertRaisesRegex(YouTubeBridgeError, "disabled by policy"):
            service.get_playlist(PLAYLIST_ID)

    def test_fallback_missing_playlist_raises(self):
        service = self._service(payload={"_type": "playlist", "id": PLAYLIST_ID, "entries": [], "playlist_count": 0})
        with self.assertRaisesRegex(YouTubeBridgeError, "playlist_not_found|no matching public playlist|no public playlist"):
            service.get_playlist(PLAYLIST_ID)

    def test_fallback_ytdlp_failure_is_reported(self):
        def fake_run(command, capture_output, text, timeout, check):
            return subprocess.CompletedProcess(command, 1, "", "ERROR: This playlist is not available")

        service = YouTubeService(Settings(), run=fake_run)
        with self.assertRaisesRegex(YouTubeBridgeError, "This playlist is not available"):
            service.get_playlist(PLAYLIST_ID)

    def test_fallback_enumeration_is_bounded_at_500_items(self):
        # Regression: the yt-dlp fallback path must respect the same 500-item
        # hard bound as the official API path.
        entries = [{"id": f"vid{i:09d}0", "title": f"V{i}"} for i in range(501)]
        payload = {"_type": "playlist", "id": PLAYLIST_ID, "title": "P", "entries": entries, "playlist_count": 501}
        service = self._service(payload=payload)
        result = service.get_playlist(PLAYLIST_ID)
        self.assertEqual(len(result["items"]), 500)
        self.assertEqual(result["items"][-1]["position"], 500)

    def test_watch_url_trailing_slash_keeps_video_id(self):
        # Regression: /watch/?v=... (trailing slash) must not drop the video ID.
        parsed = parse_playlist_reference(f"https://www.youtube.com/watch/?v={VIDEO_ID}&list={PLAYLIST_ID}&index=3")
        self.assertEqual(parsed["video_id"], VIDEO_ID)
        self.assertEqual(parsed["url_index"], 3)

    def test_api_key_never_appears_in_error_envelope(self):
        # Security: an HTTPError from Google embeds the full request URL,
        # which contains key=...; the error envelope must never leak it.
        from urllib.error import HTTPError

        def boom(request, timeout=0):
            raise HTTPError(
                f"https://www.googleapis.com/youtube/v3/playlists?part=snippet&key=SECRETKEY123",
                403, "Forbidden", {}, None,
            )

        from youtube_mcp.core import error_result
        service = YouTubeService(Settings(api_key="SECRETKEY123"), urlopen=boom)
        try:
            service.get_playlist(PLAYLIST_ID)
        except YouTubeBridgeError as exc:
            envelope = json.dumps(error_result(exc))
            self.assertNotIn("SECRETKEY123", envelope)

    def test_fallback_command_targets_playlist_url_flat(self):
        commands = []

        def fake_run(command, capture_output, text, timeout, check):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(ytdlp_flat_playlist_payload()), "")

        service = YouTubeService(Settings(), run=fake_run)
        service.get_playlist(PLAYLIST_ID)
        command = commands[0]
        self.assertIn("--flat-playlist", command)
        self.assertIn(f"https://www.youtube.com/playlist?list={PLAYLIST_ID}", command)
        self.assertIn("--dump-single-json", command)


# ---- schema ----------------------------------------------------------------


class PlaylistToolSchemaTests(unittest.TestCase):
    def test_fastmcp_tool_schemas(self):
        tools = {tool.name for tool in mcp._tool_manager.list_tools()}
        self.assertIn("get_playlist", tools)
        self.assertIn("find_playlist_position", tools)
        for name in ("get_playlist", "find_playlist_position"):
            tool = next(t for t in mcp._tool_manager.list_tools() if t.name == name)
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertTrue(tool.annotations.idempotentHint)
        get_playlist = next(t for t in mcp._tool_manager.list_tools() if t.name == "get_playlist")
        self.assertEqual(get_playlist.parameters["properties"]["playlist"]["type"], "string")
        self.assertIn("playlist", get_playlist.parameters["required"])
        find_position = next(t for t in mcp._tool_manager.list_tools() if t.name == "find_playlist_position")
        self.assertIn("watch_url_with_list", find_position.parameters["required"])


if __name__ == "__main__":
    unittest.main()
