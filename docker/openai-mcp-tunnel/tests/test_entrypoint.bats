#!/usr/bin/env bats
# No-credential tests for the openai-mcp-tunnel entrypoint.
# A stub executable records the argv it would receive and the relevant
# environment; no network access, no OpenAI contact, no real secrets.

setup() {
    TEST_DIR="$(mktemp -d)"
    STUB="$TEST_DIR/stub-client"
    cat > "$STUB" <<'EOF'
#!/bin/sh
{
  printf 'ARGV:'
  # print each argument delimited so secret-in-argv is detectable exactly
  for arg in "$@"; do printf ' <%s>' "$arg"; done
  printf '\n'
  printf 'UID:%s\n' "$(id -u)"
  env | grep -E '^(CONTROL_PLANE_TUNNEL_ID|CONTROL_PLANE_API_KEY|MCP_SERVER_URL|HTTP_PROXY|HTTPS_PROXY|NO_PROXY|HEALTH_LISTEN_ADDR)=' | sort
} > "$RECORD_FILE"
exit 0
EOF
    chmod +x "$STUB"
    export RECORD_FILE="$TEST_DIR/record"
    export TUNNEL_CLIENT_BIN="$STUB"
    export ENTRYPOINT="$BATS_TEST_DIRNAME/../entrypoint.sh"
    export VALID_ID="tunnel_0123456789abcdef0123456789abcdef"
    export OTHER_ID="tunnel_ffffffffffffffffffffffffffffffff"
}

teardown() { rm -rf "$TEST_DIR"; }

run_ep() {
    run env "$@" "$ENTRYPOINT"
}

assert_rejected() { # assert_rejected <status> <output> [needle]
    [ "$1" -eq 78 ]
    [ -n "$3" ] && [[ "$2" == *"$3"* ]]
}

# ---------------------------------------------------------------- tunnel id

@test "missing tunnel id -> exit 78" {
    run_ep CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp
    assert_rejected "$status" "$output" "CONTROL_PLANE_TUNNEL_ID"
}

@test "malformed tunnel id -> exit 78, value not echoed" {
    run_ep OPENAI_TUNNEL_ID=tunnel_SHORT CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp
    assert_rejected "$status" "$output" "must match"
    [[ "$output" != *"tunnel_SHORT"* ]]
}

@test "official CONTROL_PLANE_TUNNEL_ID accepted" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp
    [ "$status" -eq 0 ]
    grep -q "^CONTROL_PLANE_TUNNEL_ID=$VALID_ID$" "$RECORD_FILE"
}

@test "legacy alias OPENAI_TUNNEL_ID accepted and mapped" {
    run_ep OPENAI_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp
    [ "$status" -eq 0 ]
    grep -q "^CONTROL_PLANE_TUNNEL_ID=$VALID_ID$" "$RECORD_FILE"
}

@test "both names with same value accepted" {
    run_ep OPENAI_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_TUNNEL_ID="$VALID_ID" \
        CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp
    [ "$status" -eq 0 ]
    grep -q "^CONTROL_PLANE_TUNNEL_ID=$VALID_ID$" "$RECORD_FILE"
}

@test "both names with different values -> exit 78" {
    run_ep OPENAI_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_TUNNEL_ID="$OTHER_ID" \
        CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp
    assert_rejected "$status" "$output" "differ"
}

# ----------------------------------------------------------------- api key

@test "missing api key -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" MCP_SERVER_URL=http://x/mcp
    assert_rejected "$status" "$output" "CONTROL_PLANE_API_KEY"
}

@test "empty api key -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY= MCP_SERVER_URL=http://x/mcp
    assert_rejected "$status" "$output" "CONTROL_PLANE_API_KEY"
}

@test "whitespace-only api key -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY="   " MCP_SERVER_URL=http://x/mcp
    assert_rejected "$status" "$output" "whitespace-only"
}

@test "api key containing newline -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=$'sk-a\nb' MCP_SERVER_URL=http://x/mcp
    assert_rejected "$status" "$output" "line breaks"
    [[ "$output" != *"sk-a"* ]]
}

# ----------------------------------------------------------- mcp server url

@test "missing MCP_SERVER_URL -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test
    assert_rejected "$status" "$output" "MCP_SERVER_URL"
}

@test "non-http MCP_SERVER_URL -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=ftp://x/mcp
    assert_rejected "$status" "$output" "http(s)"
}

# ----------------------------------------------------------------- logging

@test "invalid LOG_LEVEL -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test \
        MCP_SERVER_URL=http://x/mcp LOG_LEVEL=verbose
    assert_rejected "$status" "$output" "LOG_LEVEL"
}

@test "invalid LOG_FORMAT -> exit 78" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test \
        MCP_SERVER_URL=http://x/mcp LOG_FORMAT=xml
    assert_rejected "$status" "$output" "LOG_FORMAT"
}

# ------------------------------------------------------------------ proxy

@test "proxy variables pass through unchanged" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test \
        MCP_SERVER_URL=http://x/mcp \
        HTTP_PROXY=http://proxy:8080 HTTPS_PROXY=http://proxy:3128 \
        NO_PROXY=youtube-mcp,localhost,127.0.0.1
    [ "$status" -eq 0 ]
    grep -q '^HTTP_PROXY=http://proxy:8080$' "$RECORD_FILE"
    grep -q '^HTTPS_PROXY=http://proxy:3128$' "$RECORD_FILE"
    grep -q '^NO_PROXY=youtube-mcp,localhost,127.0.0.1$' "$RECORD_FILE"
}

# ----------------------------------------------------------------- secrets

@test "api key never appears in stdout/stderr" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=SECRETKEY123 \
        MCP_SERVER_URL=http://x/mcp
    [ "$status" -eq 0 ]
    [[ "$output" != *"SECRETKEY123"* ]]
}

@test "api key never appears in process arguments" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=SECRETKEY123 \
        MCP_SERVER_URL=http://x/mcp
    [ "$status" -eq 0 ]
    local argv
    argv="$(grep '^ARGV:' "$RECORD_FILE")"
    [[ "$argv" != *"SECRETKEY123"* ]]
    grep -q '^CONTROL_PLANE_API_KEY=SECRETKEY123$' "$RECORD_FILE"   # env is the channel
}

# ------------------------------------------------------------ exec surface

@test "final exec arguments are exactly run + log flags" {
    run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test \
        MCP_SERVER_URL=http://x/mcp
    [ "$status" -eq 0 ]
    [ "$(grep '^ARGV:' "$RECORD_FILE")" = "ARGV: <run> <--log.level=info> <--log.format=json>" ]
}

@test "runs as the configured non-root user" {
    # Simulate non-root: the stub records its own UID; when the harness runs
    # as root (CI), verify via setpriv; otherwise the UID must be non-zero.
    if [ "$(id -u)" -eq 0 ]; then
        run setpriv --reuid=10001 --regid=10001 --clear-groups \
            env RECORD_FILE="$RECORD_FILE" TUNNEL_CLIENT_BIN="$STUB" \
            CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test \
            MCP_SERVER_URL=http://x/mcp "$ENTRYPOINT"
        [ "$status" -eq 0 ]
        grep -q '^UID:10001$' "$RECORD_FILE"
    else
        run_ep CONTROL_PLANE_TUNNEL_ID="$VALID_ID" CONTROL_PLANE_API_KEY=sk-test MCP_SERVER_URL=http://x/mcp
        [ "$status" -eq 0 ]
        local uid
        uid="$(grep '^UID:' "$RECORD_FILE" | cut -d: -f2)"
        [ "$uid" -eq "$(id -u)" ]
        [ "$uid" -ne 0 ]
    fi
}
