# Obtaining a YouTube Data API key

This guide explains how to create a Google API key for the YouTube Data API
v3 and how to enter it into the Compose/Portainer generator of this project.
It follows the current official Google documentation:

- Google Cloud: API key authentication — <https://docs.cloud.google.com/docs/authentication/api-keys>
- YouTube Data API: Getting started — <https://developers.google.com/youtube/v3/getting-started>

## When you need a key

The key is **required** for every tool that calls the YouTube Data API v3
(video/channel metadata, search, recent uploads via the API) and **optional**
for the unofficial `yt-dlp` transcript fallback, which works without a key.
If you enable the transcript fallback but skip the API key, only the
no-key features will work.

## Creating the key in Google Cloud Console

1. Select or create a **Google Cloud project**
   (<https://console.cloud.google.com/>). A project is required before any
   API can be enabled.
2. Open **APIs & Services → Library**.
3. Search for **YouTube Data API v3**.
4. Open it and click **Enable**.
5. Only after enabling it, open **APIs & Services → Credentials**.
6. Click **Create credentials → API key**.
7. Give the key a descriptive name, for example `ChatGPT YouTube MCP`.
8. Under **API restrictions**, restrict the key to **YouTube Data API v3**
   only. Note that the restriction list contains only APIs that are already
   enabled in the selected project — if YouTube Data API v3 does not appear,
   go back to step 2 and enable it first.
9. Under **Application restrictions**:
   - **None** is the right choice for the initial Docker/Portainer setup.
     The container's outbound IP is typically not stable, and per-request
     referrer restrictions cannot be applied to server-side API calls.
   - **IP address** restriction is appropriate only once the Docker host has
     a stable public outbound IP address; otherwise you will lock yourself
     out.
10. Do **not** configure service-account authentication for this use case:
    the standard YouTube Data API key flow does not use service accounts.
11. Create the key and copy the `AIza...` value securely.

The resulting key looks like `AIza` followed by 35 characters.

## Entering the key into the generator

The Compose/Portainer generators prompt for the key with hidden input and
show a masked confirmation (length-preserving, only the last four characters
visible) before accepting it. The key is written into the generated stack
file as an environment variable — **the generated file contains the key in
plain text and must never be committed to Git.** Restrict its file
permissions; the Bash generator already writes it with mode `0600`.

## Quota

YouTube Data API usage counts against the project's daily quota budget.
The exact default allocation and per-call costs are defined by Google and
can change, so check the quota page of your project in the Google Cloud
Console rather than relying on hardcoded numbers here. Metadata and search
calls consume different quota amounts; the `yt-dlp` fallback does not use
API quota but is unofficial and can be rate-limited by YouTube.

## Troubleshooting

- **YouTube Data API v3 does not appear in the API-restriction list:**
  the API has not been enabled yet in that project (step 2–4), or a
  different Google Cloud project is selected than the one where you enabled
  it.
- **403 `quotaExceeded` responses:** the project's daily API quota is used
  up; wait for the reset or reduce call volume.
- **403 errors mentioning the API not enabled for the project:** enable
  YouTube Data API v3 for exactly the project that owns the key.

## Supplementary visual reference

A third-party walkthrough with screenshots is available from Themeum; use
it only as a visual supplement — the official Google pages above are
authoritative and more current:

- <https://docs.themeum.com/tutor-lms/tutorials/get-youtube-api-key/>
