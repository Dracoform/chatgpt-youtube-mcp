FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Dracoform/chatgpt-youtube-mcp" \
      org.opencontainers.image.description="Read-only MCP server for current YouTube data" \
      org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Writable state directory for the optional staged yt-dlp updater (Phase 2).
# Declared as a VOLUME and pre-created for UID 65532 so a named volume mounted
# here is writable by the non-root runtime; with auto-update disabled (the
# default) this is never written and stays quiescent.
RUN mkdir -p /app/state && chown 65532:65532 /app/state
VOLUME /app/state

ENV MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765
EXPOSE 8765

# Readiness check against the app's own /healthz endpoint (same uvicorn
# listener as /mcp): proves port 8765 accepts connections. Uses the Python
# standard library already in the image — no curl/wget added. Works under
# read_only, cap_drop ALL, and no-new-privileges (no writes, no caps).
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
  CMD ["python3", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).status == 200 else 1)"]

USER 65532:65532
ENTRYPOINT ["youtube-current-data-mcp"]
