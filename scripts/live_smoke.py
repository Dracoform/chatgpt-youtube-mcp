#!/usr/bin/env python3
import json
import sys

from youtube_mcp.core import Settings, YouTubeService, error_result


def main() -> int:
    reference = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=-ulRSvQIObk"
    service = YouTubeService(Settings.from_env())
    result = {"reference": reference}
    for name, call in {
        "metadata": lambda: service.get_video(reference),
        "captions": lambda: service.list_caption_tracks(reference),
        "transcript": lambda: service.get_video_transcript(reference, languages=["en", "de"], max_chars=4000),
    }.items():
        try:
            result[name] = call()
        except Exception as exc:
            result[name] = error_result(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("metadata", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

