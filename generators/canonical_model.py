"""Reference renderer for the canonical multi-client capability model (Stage 4).

This module is the SINGLE SOURCE OF TRUTH for what Compose YAML a given
canonical semantic input must produce. All three real generators
(Bash, PowerShell, Web) must produce byte-identical, normalized-equal YAML from
the same canonical JSON input. The three-way equivalence test feeds identical
canonical JSON to bash / pwsh / node(wrapper) / this reference and compares
normalized YAML.

The canonical input is a JSON object with this shape (see
docs/MULTI_CLIENT_DEPLOYMENT_GUIDE.md §"Canonical input model"):

{
  "mcp":         {"tag": "latest", "youtube_api_key": "", "enable_ytdlp": true,
                  "languages": "de,en", "max_chars": "60000"},
  "access":      ["local"|"tunnel"|"static"|"oauth", ...],   # enabled access methods
  "tunnel":      {"tag": "0.1.0", "tunnel_id": "...", "runtime_api_key": "...",
                  "http_proxy": ""},
  "edge": {
    "enabled": true,                                  # derived: static or oauth in access
    "auth_modes": ["static"|"oauth", ...],            # = access ∩ {static, oauth}
    "tls": "edge"|"ingress",
    "public_port": "8443",
    "static_token_prefix": "ytsk_",
    "static_tokens": ["ytsk_..."],
    "oauth": {"issuer": "", "resource": "", "audience": "",
              "required_scope": "", "jwks_url": "",
              "authorization_servers": "", "scopes_supported": ""}
  }
}

Validation rules (rejected combinations raise ValueError):
  - "access" must be non-empty and only contain recognized methods.
  - edge enabled iff static or oauth present in access. If edge.enabled but
    auth_modes empty -> reject (public edge needs at least one auth mode).
  - "oauth" mode requires edge.oauth.issuer and edge.oauth.resource (fail closed).
  - tls == "edge" requires exactly (cert + key) both non-empty; cert xor key ->
    reject. tls == "ingress" must have both empty.
  - static+oauth simultaneously: every static token MUST begin with
    static_token_prefix (else ambiguous with the OAuth namespace) -> reject.
  - static tokens only are permitted with any prefix (static-only keeps legacy
    backward compatibility).
  - never weaken security: a public edge never falls back to no-auth; the core
    is never published on a public (non-loopback) interface.
"""

from __future__ import annotations

import json
from typing import Any

MCP_IMAGE_BASE = "ghcr.io/dracoform/chatgpt-youtube-mcp"
TUNNEL_IMAGE_BASE = "ghcr.io/dracoform/openai-mcp-tunnel"

ACCESS_METHODS = ("local", "tunnel", "static", "oauth")

# Existing / unreachable: default image tags.
MCP_TAG_DEFAULT = "latest"
TUNNEL_TAG_DEFAULT = "0.1.0"


def _default_model() -> dict[str, Any]:
    return {
        "mcp": {"tag": MCP_TAG_DEFAULT, "youtube_api_key": "",
                "enable_ytdlp": True, "languages": "de,en", "max_chars": "60000"},
        "access": ["tunnel"],
        "tunnel": {"tag": TUNNEL_TAG_DEFAULT, "tunnel_id": "",
                   "runtime_api_key": "", "http_proxy": ""},
        "edge": {
            "enabled": False,
            "auth_modes": [],
            "tls": "ingress",
            "public_port": "8443",
            "cert_file": "",
            "key_file": "",
            "static_token_prefix": "ytsk_",
            "static_tokens": [],
            "oauth": {"issuer": "", "resource": "", "audience": "",
                      "required_scope": "", "jwks_url": "",
                      "authorization_servers": "", "scopes_supported": ""},
        },
    }


def normalize(model: dict[str, Any]) -> dict[str, Any]:
    """Fill unset/default fields and coerce types; does not mutate input."""
    mcp_default = _default_model()
    out = json.loads(json.dumps(model))
    mcp = dict(mcp_default["mcp"])
    mcp.update(out.get("mcp") or {})
    out["mcp"] = mcp

    access = out.get("access") or []
    out["access"] = list(access)

    tun = dict(mcp_default["tunnel"])
    tun.update(out.get("tunnel") or {})
    out["tunnel"] = tun

    edge = json.loads(json.dumps(mcp_default["edge"]))
    given = out.get("edge") or {}
    for k in ("tls", "public_port", "static_token_prefix", "cert_file", "key_file"):
        if k in given:
            edge[k] = given[k]
    edge["auth_modes"] = list(given.get("auth_modes") or [])
    edge["static_tokens"] = list(given.get("static_tokens") or [])
    oauth_default = dict(mcp_default["edge"]["oauth"])
    oauth_default.update(given.get("oauth") or {})
    edge["oauth"] = oauth_default
    out["edge"] = edge

    out["edge"]["enabled"] = ("static" in access) or ("oauth" in access)
    return out


def validate(model: dict[str, Any]) -> dict[str, Any]:
    """Validate a (normalized) canonical input; raises ValueError on rejection."""
    m = normalize(model)
    access = m["access"]
    unknown = [a for a in access if a not in ACCESS_METHODS]
    if unknown:
        raise ValueError(f"Unknown access method(s): {', '.join(unknown)}")
    if not access:
        raise ValueError("At least one access method must be selected.")

    static_on = "static" in access
    oauth_on = "oauth" in access
    edge = m["edge"]

    if edge["enabled"] != (static_on or oauth_on):
        raise ValueError("edge.enabled must match the presence of static/oauth access.")
    if edge["enabled"] and not edge["auth_modes"]:
        raise ValueError("A public edge requires at least one auth mode (static and/or oauth).")
    if edge["enabled"] and (static_on != ("static" in edge["auth_modes"]) or
                            oauth_on != ("oauth" in edge["auth_modes"])):
        raise ValueError("edge.auth_modes must equal access ∩ {static, oauth}.")

    # OAuth fail-closed
    if oauth_on:
        o = edge["oauth"]
        if not o.get("issuer") or not o.get("resource"):
            raise ValueError("OAuth access requires edge.oauth.issuer and edge.oauth.resource.")
    if not oauth_on:
        o = edge["oauth"]
        if o.get("issuer") or o.get("resource"):
            raise ValueError("OAuth issuer/resource set but OAuth is not an enabled access method.")

    # TLS: edge-terminated requires BOTH cert+key; ingress requires neither.
    tls = edge["tls"]
    cert = edge.get("cert_file") or ""
    key = edge.get("key_file") or ""
    if tls == "edge":
        if bool(cert) != bool(key):
            raise ValueError("TLS mode 'edge' requires BOTH edge.cert_file and edge.key_file.")
    elif tls == "ingress":
        if cert or key:
            raise ValueError("TLS mode 'ingress' must not set edge.cert_file/key_file (external TLS).")
    else:
        raise ValueError(f"edge.tls must be 'edge' or 'ingress'; got {tls!r}")

    # static+oauth namespace ambiguity: every static token must carry the prefix
    # when OAuth is also enabled, else it would be dispatched to the OAuth domain.
    if static_on and oauth_on:
        prefix = edge.get("static_token_prefix") or "ytsk_"
        for tok in edge["static_tokens"]:
            if not tok.startswith(prefix):
                raise ValueError(
                    f"Static token {json.dumps(tok)} does not begin with the reserved "
                    f"static namespace prefix {prefix!r}; with static+OAuth on one edge "
                    f"this would be ambiguous (rejected)."
                )

    # Never a public no-auth edge: if edge enabled, must have static_tokens when
    # static_on, and OAuth config when oauth_on (fail closed at generation).
    if edge["enabled"] and static_on and not edge["static_tokens"]:
        raise ValueError("Static access requires at least one static token.")
    return m


def _yaml_scalar(value: str | int | bool) -> str:
    """Portable scalar quoting: single-quote strings ('' escapes '), else literal."""
    if isinstance(value, bool):
        return "'true'" if value else "'false'"
    if isinstance(value, int):
        return str(value)
    s = str(value)
    return "'" + s.replace("'", "''") + "'"


def render(model_json: str | dict[str, Any]) -> str:
    """Render the canonical model to Compose YAML (the single source of truth).

    Accepts a canonical model dict, an inline JSON string, or a path to a JSON
    file (if the string names an existing file).
    """
    from pathlib import Path

    if not isinstance(model_json, dict):
        candidate = model_json
        try:
            raw: Any = json.loads(candidate)
        except json.JSONDecodeError:
            # Not inline JSON: try as a file path.
            if Path(candidate).is_file():
                raw = json.loads(Path(candidate).read_text(encoding="utf-8"))
            else:
                raise
    else:
        raw = model_json
    m = validate(raw)
    lines: list[str] = []
    header = ("# Generated by the chatgpt-youtube-mcp multi-client generator. "
              "Contains secrets; restrict access.")
    lines.append(header)
    lines.append("services:")
    mcp = m["mcp"]
    access = m["access"]
    lines.append("  youtube-mcp:")
    lines.append("    image: " + _yaml_scalar(f"{MCP_IMAGE_BASE}:{mcp['tag']}"))
    lines.append("    restart: unless-stopped")
    lines.append("    environment:")
    lines.append("      MCP_TRANSPORT: 'streamable-http'")
    lines.append("      MCP_HOST: '0.0.0.0'")
    lines.append("      MCP_PORT: '8765'")
    lines.append("      YOUTUBE_API_KEY: " + _yaml_scalar(mcp.get("youtube_api_key") or ""))
    lines.append("      YOUTUBE_ENABLE_YTDLP: " + _yaml_scalar(
        "true" if mcp.get("enable_ytdlp") else "false"))
    lines.append("      YOUTUBE_TRANSCRIPT_MAX_CHARS: " + _yaml_scalar(mcp.get("max_chars") or "60000"))
    lines.append("      YOUTUBE_DEFAULT_LANGUAGES: " + _yaml_scalar(mcp.get("languages") or "de,en"))
    # Local access: the core publishes a LOOPBACK host port so host clients can
    # reach it. It is NEVER published to a public interface.
    if "local" in access:
        lines.append("    ports:")
        lines.append("      - '127.0.0.1:8765:8765'")
    lines.append("    expose:")
    lines.append("      - '8765'")
    lines.append("    read_only: true")
    lines.append("    tmpfs:")
    lines.append("      - /tmp:size=64m")
    lines.append("    cap_drop:")
    lines.append("      - ALL")
    lines.append("    security_opt:")
    lines.append("      - no-new-privileges:true")
    lines.append("    networks:")
    lines.append("      - youtube-mcp-internal")
    lines.append("")

    tunnel_on = "tunnel" in access
    if tunnel_on:
        tun = m["tunnel"]
        lines.append("  openai-tunnel:")
        lines.append("    image: " + _yaml_scalar(f"{TUNNEL_IMAGE_BASE}:{tun['tag']}"))
        lines.append("    restart: unless-stopped")
        lines.append("    environment:")
        lines.append("      CONTROL_PLANE_TUNNEL_ID: " + _yaml_scalar(tun.get("tunnel_id") or ""))
        lines.append("      CONTROL_PLANE_API_KEY: " + _yaml_scalar(tun.get("runtime_api_key") or ""))
        lines.append("      MCP_SERVER_URL: 'http://youtube-mcp:8765/mcp'")
        proxy = tun.get("http_proxy") or ""
        if proxy:
            lines.append("      HTTPS_PROXY: " + _yaml_scalar(proxy))
            lines.append("      NO_PROXY: 'youtube-mcp,localhost,127.0.0.1'")
        lines.append("    depends_on:")
        lines.append("      youtube-mcp:")
        lines.append("        condition: service_healthy")
        lines.append("    read_only: true")
        lines.append("    tmpfs:")
        lines.append("      - /tmp:size=16m")
        lines.append("    cap_drop:")
        lines.append("      - ALL")
        lines.append("    security_opt:")
        lines.append("      - no-new-privileges:true")
        lines.append("    stop_grace_period: 30s")
        lines.append("    networks:")
        lines.append("      - youtube-mcp-internal")
        lines.append("")

    edge = m["edge"]
    if edge["enabled"]:
        edge_svc = "  youtube-mcp-edge:"
        lines.append(edge_svc)
        lines.append("    image: " + _yaml_scalar(f"{MCP_IMAGE_BASE}:{mcp['tag']}"))
        lines.append("    restart: unless-stopped")
        lines.append("    entrypoint: ['youtube-mcp-edge']")
        lines.append("    depends_on:")
        lines.append("      youtube-mcp:")
        lines.append("        condition: service_healthy")
        lines.append("    environment:")
        lines.append("      EDGE_MCP_UPSTREAM_URL: 'http://youtube-mcp:8765/mcp'")
        lines.append("      EDGE_HOST: '0.0.0.0'")
        lines.append("      EDGE_PORT: '8766'")
        static_on_e = "static" in edge["auth_modes"]
        oauth_on_e = "oauth" in edge["auth_modes"]
        lines.append("      EDGE_STATIC_AUTH_ENABLED: " + _yaml_scalar(static_on_e))
        if static_on_e:
            lines.append("      EDGE_STATIC_TOKENS: " +
                         _yaml_scalar(",".join(edge["static_tokens"])))
            lines.append("      EDGE_STATIC_TOKEN_PREFIX: " +
                         _yaml_scalar(edge.get("static_token_prefix") or "ytsk_"))
        lines.append("      EDGE_OAUTH_ENABLED: " + _yaml_scalar(oauth_on_e))
        if oauth_on_e:
            o = edge["oauth"]
            lines.append("      EDGE_OAUTH_ISSUER: " + _yaml_scalar(o.get("issuer") or ""))
            lines.append("      EDGE_OAUTH_RESOURCE_IDENTIFIER: " +
                         _yaml_scalar(o.get("resource") or ""))
            if o.get("audience"):
                lines.append("      EDGE_OAUTH_AUDIENCE: " + _yaml_scalar(o["audience"]))
            if o.get("required_scope"):
                lines.append("      EDGE_OAUTH_REQUIRED_SCOPE: " +
                             _yaml_scalar(o["required_scope"]))
            if o.get("jwks_url"):
                lines.append("      EDGE_OAUTH_JWKS_URL: " + _yaml_scalar(o["jwks_url"]))
            if o.get("authorization_servers"):
                lines.append("      EDGE_OAUTH_AUTHORIZATION_SERVERS: " +
                             _yaml_scalar(o["authorization_servers"]))
            if o.get("scopes_supported"):
                lines.append("      EDGE_OAUTH_SCOPES_SUPPORTED: " +
                             _yaml_scalar(o["scopes_supported"]))
        lines.append("      EDGE_MAX_REQUEST_BYTES: '2097152'")
        lines.append("      EDGE_UPSTREAM_TIMEOUT_SECONDS: '120'")
        tls = edge["tls"]
        if tls == "edge":
            lines.append("      EDGE_TLS_CERT_FILE: '" + (edge.get("cert_file") or "") + "'")
            lines.append("      EDGE_TLS_KEY_FILE: '" + (edge.get("key_file") or "") + "'")
            # Mount an operator-provided cert dir read-only so the edge can
            # terminate TLS. The host dir ./edge-certs must contain the PEM
            # cert chain (tls.crt) and private key (tls.key) referenced above.
            lines.append("    volumes:")
            lines.append("      - './edge-certs:/certs:ro'")
        lines.append("    read_only: true")
        lines.append("    tmpfs:")
        lines.append("      - /tmp:size=16m")
        lines.append("    cap_drop:")
        lines.append("      - ALL")
        lines.append("    security_opt:")
        lines.append("      - no-new-privileges:true")
        if tls == "edge":
            # Edge-terminated TLS: publish the public port, loopback-gated by
            # default (operator widens to expose publicly).
            lines.append("    ports:")
            lines.append("      - " + _yaml_scalar(
                "127.0.0.1:" + str(edge.get("public_port") or "8443") + ":8766"))
        lines.append("    networks:")
        lines.append("      - youtube-mcp-internal")
        lines.append("")

    lines.append("networks:")
    lines.append("  youtube-mcp-internal:")
    lines.append("    driver: bridge")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Shared canonical-model CLI (used by Bash/PowerShell generators in
    non-interactive `--input <json>` mode).

    Usage:
      python -m generators.canonical_model <canonical.json> [--out out.yml]
    Prints the rendered Compose YAML to stdout (or writes to --out).
    Returns 0 on success, 2 on usage error; raises ValueError on validation
    rejection (callers fail closed).
    """
    import argparse

    ap = argparse.ArgumentParser(prog="canonical_model")
    ap.add_argument("input", help="canonical JSON input file")
    ap.add_argument("--out", help="optional output YAML path")
    args = ap.parse_args(argv)
    text = render(args.input)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.exit(main(sys.argv[1:]))
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        sys.exit(1)