# syntax=docker/dockerfile:1
# Thin wrapper around the official OpenAI Secure MCP Tunnel client image.
# Upstream: https://github.com/openai/tunnel-client (Apache-2.0)
#
# NOTE ON LICENSING: this derived image distributes or references upstream
# image layers. Apache-2.0 permits this subject to its conditions (keep the
# license and attribution notices). The upstream image is not modified in a
# way that strips any license or notice files.

# The upstream multi-arch index digest is injected by CI (build-arg) and
# defaults to the value verified in versions.env / the research document.
ARG BASE_IMAGE=ghcr.io/openai/tunnel-client@sha256:41d7c85dab37797a3eaa17c41b94a7206dd0bc186fd361034c9ec3863596ff6c

FROM ${BASE_IMAGE}

# Unprivileged runtime identity. The entrypoint validates the environment
# and directly execs the official client; it does not need root, and this
# image does not install an identity-change helper (a non-root process
# could not use one anyway).
# UNRESOLVED (tracked in docs): whether the upstream client needs any
# capability or writable path beyond /tmp under non-root must be confirmed
# by the CI container smoke test before publication.
USER 10001:10001

COPY --chmod=0755 entrypoint.sh /entrypoint.sh

# Health listener stays loopback-only inside the container; the healthcheck
# runs in the same container, so no port is published to the host.
# /healthz is the liveness endpoint (process health). UNRESOLVED until the
# CI smoke test runs: exact status semantics of /healthz vs /readyz; the
# healthcheck intentionally checks liveness only, NOT end-to-end readiness.
ENV HEALTH_LISTEN_ADDR=127.0.0.1:8080 \
    LOG_LEVEL=info \
    LOG_FORMAT=json
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["/entrypoint.sh"]

LABEL org.opencontainers.image.title="openai-mcp-tunnel" \
      org.opencontainers.image.description="Secure MCP Tunnel client wrapper for the chatgpt-youtube-mcp project; wraps ghcr.io/openai/tunnel-client (Apache-2.0)" \
      org.opencontainers.image.url="https://github.com/Dracoform/chatgpt-youtube-mcp" \
      org.opencontainers.image.source="https://github.com/Dracoform/chatgpt-youtube-mcp" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="ghcr.io/openai/tunnel-client" \
      org.opencontainers.image.base.digest="sha256:41d7c85dab37797a3eaa17c41b94a7206dd0bc186fd361034c9ec3863596ff6c"
