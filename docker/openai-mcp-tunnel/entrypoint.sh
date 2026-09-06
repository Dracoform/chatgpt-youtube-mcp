#!/bin/sh
# Entrypoint for the openai-mcp-tunnel wrapper image.
#
# Adapts the project's environment contract to the official tunnel-client
# contract and execs `tunnel-client run`. Designed to run as UID/GID 10001
# (set by the Dockerfile USER directive); it performs no identity change
# and requires no root privileges.
#
# Secrets are never printed. Configuration errors exit 78 (EX_CONFIG).
#
# Environment contract (see docs/OPENAI_MCP_TUNNEL_IMAGE_RESEARCH.md):
#   CONTROL_PLANE_TUNNEL_ID   official name (required path)
#   OPENAI_TUNNEL_ID          legacy compatibility alias, mapped to the
#                             official name; conflicting values are fatal
#   CONTROL_PLANE_API_KEY     Runtime API key (never logged, never in argv)
#   MCP_SERVER_URL            internal Streamable HTTP MCP target
#   HTTP_PROXY/HTTPS_PROXY/NO_PROXY   passed through untouched
#   LOG_LEVEL (debug|info|warn), LOG_FORMAT (struct-text|json)
set -u

TUNNEL_CLIENT_BIN="${TUNNEL_CLIENT_BIN:-/usr/bin/tunnel-client}"

die() { # die EXIT MESSAGE — message must be pre-redacted by caller
    printf '%s\n' "$2" >&2
    exit "$1"
}
EX_CONFIG=78

# ------------------------------------------------------------- tunnel id
# Official name wins; legacy alias is accepted for compatibility.
# Both set with different values is fatal (avoid silently using the wrong
# tunnel). Neither set is fatal.
openai_tid="${OPENAI_TUNNEL_ID:-}"
control_tid="${CONTROL_PLANE_TUNNEL_ID:-}"
if [ -n "$openai_tid" ] && [ -n "$control_tid" ] && [ "$openai_tid" != "$control_tid" ]; then
    die $EX_CONFIG "config error: OPENAI_TUNNEL_ID and CONTROL_PLANE_TUNNEL_ID are both set but differ; set only CONTROL_PLANE_TUNNEL_ID"
fi
TUNNEL_ID="${control_tid:-$openai_tid}"
[ -n "$TUNNEL_ID" ] || die $EX_CONFIG "config error: CONTROL_PLANE_TUNNEL_ID is required (legacy alias OPENAI_TUNNEL_ID is accepted)"

# Official format: tunnel_ + 32 lowercase hex characters.
case "$TUNNEL_ID" in
    tunnel_[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) die $EX_CONFIG "config error: tunnel id must match ^tunnel_[0-9a-f]{32}$ (value not shown)" ;;
esac

# ---------------------------------------------------------------- api key
# Reject unset, empty, whitespace-only, or CR/LF-embedded values.
# The value is NOT trimmed or transformed: the exact bytes provided are the
# credential the client will use. Nothing about the value is ever printed.
api_key="${CONTROL_PLANE_API_KEY:-}"
[ -n "$api_key" ] || die $EX_CONFIG "config error: CONTROL_PLANE_API_KEY is required and must not be empty"
case "$api_key" in
    *[!\ \	]*) ;;           # contains at least one non-whitespace char
    *) die $EX_CONFIG "config error: CONTROL_PLANE_API_KEY must not be whitespace-only" ;;
esac
case "$api_key" in
    *'
'*|*''*) die $EX_CONFIG "config error: CONTROL_PLANE_API_KEY must not contain line breaks" ;;
esac

# -------------------------------------------------------- mcp server url
[ -n "${MCP_SERVER_URL:-}" ] || die $EX_CONFIG "config error: MCP_SERVER_URL is required"
case "$MCP_SERVER_URL" in
    http://*|https://*) ;;
    *) die $EX_CONFIG "config error: MCP_SERVER_URL must be an absolute http(s) URL (value not shown)" ;;
esac

# --------------------------------------------------------------- logging
LOG_LEVEL="${LOG_LEVEL:-info}"
LOG_FORMAT="${LOG_FORMAT:-json}"
case "$LOG_LEVEL" in
    debug|info|warn) : ;;
    *) die $EX_CONFIG "config error: LOG_LEVEL must be one of debug,info,warn" ;;
esac
case "$LOG_FORMAT" in
    struct-text|json) : ;;
    *) die $EX_CONFIG "config error: LOG_FORMAT must be one of struct-text,json" ;;
esac

# ---------------------------------------------- normalize official names
# The client reads CONTROL_PLANE_TUNNEL_ID; make sure it is set even when
# only the legacy alias was provided.
export CONTROL_PLANE_TUNNEL_ID="$TUNNEL_ID"
export CONTROL_PLANE_API_KEY="$api_key"
export HEALTH_LISTEN_ADDR="${HEALTH_LISTEN_ADDR:-127.0.0.1:8080}"
# HTTP_PROXY / HTTPS_PROXY / NO_PROXY pass through untouched: the client
# applies standard env semantics when no explicit proxy flag is set.

# ------------------------------------------------------------------ exec
# Direct exec: this process becomes the client (PID 1), receives SIGTERM
# directly, and no identity change is attempted (container runs as the
# non-root user configured in the Dockerfile).
exec "$TUNNEL_CLIENT_BIN" run \
    --log.level="$LOG_LEVEL" --log.format="$LOG_FORMAT"
