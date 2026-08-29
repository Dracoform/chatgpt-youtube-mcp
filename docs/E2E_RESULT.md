# End-to-end test record

Date: 2026-08-29 UTC

Input video: `https://www.youtube.com/watch?v=-ulRSvQIObk`

Environment: Python 3.12, `mcp` 1.29.1, `yt-dlp` 2026.08.19, no YouTube API key. The test therefore intentionally exercised the unofficial no-key path.

## Result

- URL/ID recognition: pass (`-ulRSvQIObk`)
- Live metadata retrieval: pass
- Title: `stop running code on your computer`
- Channel: `Syntax`
- Publication date reported by YouTube extraction: 2026-08-28
- Duration: 799 seconds
- Description and 19 chapters: retrieved
- Caption discovery: pass; automatic English original and translated tracks were advertised
- Transcript download: pass on retry; 439 timed segments were retrieved
- Transcript language: automatic English captions
- Output bounding: pass; the smoke test requested 4,000 characters and received `truncated:true`

The first caption fetch timed out and was not hidden: the tool returned a structured retryable failure. The implementation was then changed to try caption candidates in a deterministic order and use a 60-second caption timeout. The full rerun succeeded. The CA behavior of the isolated test proxy was handled by making `yt-dlp` respect the operator-supplied system/custom CA bundle; certificate verification remains enabled.

Unit suite: 10 tests passed after adding both Portainer stack generators, private-file verification, and the shared stack-contract checks. Run the final commands below to reproduce the current state:

```bash
uv run python -m unittest discover -s tests -v
uv run python scripts/live_smoke.py 'https://www.youtube.com/watch?v=-ulRSvQIObk'
```
