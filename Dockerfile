FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Dracoform/chatgpt-youtube-mcp" \
      org.opencontainers.image.description="Read-only MCP server for current YouTube data" \
      org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765
EXPOSE 8765
USER 65532:65532
ENTRYPOINT ["youtube-current-data-mcp"]
