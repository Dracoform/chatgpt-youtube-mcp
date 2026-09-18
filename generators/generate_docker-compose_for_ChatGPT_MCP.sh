#!/usr/bin/env bash
set -Eeuo pipefail

# Usage:
#   ./generate_docker-compose_for_ChatGPT_MCP.sh [--output FILE] [--input canonical.json]
#
# Capability-oriented generator for the multi-client YouTube MCP. The operator
# selects the ACCESS METHODS they want; the Compose stack is composed
# accordingly. The generated YAML is identical regardless of which generator
# (Bash / PowerShell / Web) produced it - cross-generator equivalence is
# enforced by tests/test_generator_equivalence.py against
# generators/canonical_model.py (the single source of truth).
#
# ACCESS METHODS (independently selectable, composable):
#   local  -> core reachable on host loopback 127.0.0.1:8765 (generic MCP clients)
#   tunnel -> OpenAI Secure MCP Tunnel, connected DIRECTLY to the core (ChatGPT/OpenAI)
#   static -> public edge with static Bearer auth (DeepSeek Harness, LibreChat static)
#   oauth  -> public edge with OAuth access-token validation (Claude)
#   static+oauth -> ONE edge handles both (single public /mcp endpoint)
#
# The MCP core is ALWAYS present and is NEVER published on a public interface.
# In non-interactive `--input <canonical.json>` mode the generator renders the
# canonical input through the shared canonical_model.py and must match it
# byte-for-byte.

PROGRAM_NAME="generate_docker-compose_for_ChatGPT_MCP.sh"
DEFAULT_OUTPUT="portainer-youtube-mcp-stack.yml"

usage() {
  cat <<EOF
Gefuehrter Generator fuer einen multi-client Portainer-Stack mit YouTube MCP.

Zugriffsarten (unabhaengig komponierbar):
  local   - MCP-Kern auf Host-Loopback (lokale/vertraute Clients)
  tunnel  - OpenAI Secure MCP Tunnel direkt zum Kern (ChatGPT/OpenAI)
  static  - oeffentlicher Edge mit statischem Bearer-Token
  oauth   - oeffentlicher Edge mit OAuth-Token-Validierung (Claude)
  static+oauth - EIN Edge behandelt beides

Verwendung:
  ./$PROGRAM_NAME [--output DATEI] [--input canonical.json]

  --output DATEI   Ausgabedatei (default: $DEFAULT_OUTPUT)
  --input DATEI    Kanonisches Eingabe-JSON (nicht-interaktiv; fuer Tests/Automation)

Das Skript installiert und startet nichts. Es erzeugt nur eine YAML-Datei.
EOF
}

die() {
  printf 'Fehler: %s\n' "$1" >&2
  exit 1
}

prompt() {
  local __result_var="$1" prompt_text="$2" default_value="${3-}" answer
  if [[ -n "$default_value" ]]; then
    read -r -p "$prompt_text [$default_value]: " answer || true
    answer="${answer:-$default_value}"
  else
    read -r -p "$prompt_text: " answer || true
  fi
  printf -v "$__result_var" '%s' "$answer"
}

prompt_yes_no() {
  local __result_var="$1" prompt_text="$2" default_value="${3:-yes}" answer suffix
  [[ "$default_value" == "yes" ]] && suffix="J/n" || suffix="j/N"
  while true; do
    read -r -p "$prompt_text [$suffix]: " answer || true
    answer="${answer:-$default_value}"
    case "${answer,,}" in
      j|ja|y|yes) printf -v "$__result_var" '%s' "true"; return ;;
      n|nein|no) printf -v "$__result_var" '%s' "false"; return ;;
      *) printf 'Bitte mit ja oder nein antworten.\n' >&2 ;;
    esac
  done
}

# Select zero or more options (numbers, space-separated). Sets the result to
# space-separated names.
prompt_select_multi() {
  local __result_var="$1" prompt_text="$2"
  shift 2
  local options=("$@") selections="" idx answer
  printf '%s\n' "$prompt_text" >&2
  for ((i=0; i<${#options[@]}; i++)); do
    printf '  %d) %s\n' $((i+1)) "${options[$i]}" >&2
  done
  printf 'Wahl (Nummern, durch Leerzeichen getrennt): ' >&2
  IFS= read -r answer || true
  for word in $answer; do
    idx="${word//[^0-9]/}"
    if [[ -n "$idx" ]] && ((idx >= 1 && idx <= ${#options[@]})); then
      selections+=" ${options[$((idx-1))]}"
    fi
  done
  printf -v "$__result_var" '%s' "$selections"
}

# ---- secret handling (kept consistent with prior stages) -------------------

mask_secret() {
  local value="$1" length="${#1}" i shown="" masked=""
  if ((length <= 4)); then
    for ((i=0; i<length; i++)); do masked+='X'; done
    printf '%s' "$masked"
  else
    shown="${value: -4}"
    for ((i=0; i<length-4; i++)); do masked+='X'; done
    printf '%s%s' "$masked" "$shown"
  fi
}

secret_has_control_chars_or_edge_whitespace() {
  local value="$1"
  # leading/trailing whitespace
  [[ "$value" != "${value#"${value%%[![:space:]]*}"}" ]] && return 0
  [[ "$value" != "${value%"${value##*[![:space:]]}"}" ]] && return 0
  # C0/C1 control characters anywhere
  [[ "$value" =~ [[:cntrl:]] ]] && return 0
  return 1
}

prompt_secret_confirmed() {
  local __result_var="$1" prompt_text="$2" required="${3:-false}" label="$4" answer
  while true; do
    answer=""
    while true; do
      IFS= read -r -s -p "$prompt_text: " answer || true
      printf '\n' >&2
      if [[ -z "$answer" && "$required" != "true" ]]; then
        break
      fi
      if [[ -z "$answer" ]]; then
        printf 'Eine Eingabe ist erforderlich.\n' >&2
        continue
      fi
      if secret_has_control_chars_or_edge_whitespace "$answer"; then
        printf 'Warnung: Die Eingabe enthaelt Steuerzeichen oder fuehrende/nachfolgende Leerzeichen. Bitte erneut eingeben.\n' >&2
        continue
      fi
      break
    done
    if [[ -z "$answer" ]]; then
      printf -v "$__result_var" '%s' ""
      return 0
    fi
    printf '%s empfangen:\n%s\nLaenge: %d Zeichen\n' "$label" "$(mask_secret "$answer")" "${#answer}" >&2
    local confirm
    read -r -p "Diesen Wert uebernehmen? [Y/n]: " confirm || true
    confirm="${confirm:-y}"
    case "${confirm,,}" in
      y|j|ja|yes) break ;;
      *) printf 'Eingabe wird wiederholt.\n' >&2 ;;
    esac
  done
  printf -v "$__result_var" '%s' "$answer"
}

# ---- rendering: delegate to the shared canonical renderer -------------------

CANONICAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_render_canonical() {
  local canonical_json="$1" output_path="$2"
  local tmp
  tmp="$(mktemp --suffix=.json)"
  printf '%s\n' "$canonical_json" > "$tmp"
  ( cd "$CANONICAL_DIR" && python3 -m generators.canonical_model "$tmp" --out "$output_path" )
  local rc=$?
  rm -f "$tmp"
  chmod 600 "$output_path"
  return $rc
}

# ---- canonical JSON building ------------------------------------------------

json_str() { # shell-quote a value as a JSON string ('' escapes in YAML not needed here; we need proper JSON)
  local s="$1"
  python3 - "$s" <<'PY'
import json,sys
print(json.dumps(sys.argv[1]))
PY
}

_build_canonical() {
  # Uses globals: mcp_tag languages max_chars youtube_api_key enable_ytdlp;
  # ACCESS_ARRAY; tunnel_tag tunnel_id runtime_api_key http_proxy; has_...
  # edge_tls edge_cert edge_key edge_public_port static_prefix static_tokens
  # oauth_issuer oauth_resource oauth_audience oauth_scope oauth_jwks.
  local access_json="# placeholder"
  # access list
  local acc=""
  local m
  for m in "${ACCESS_ARRAY[@]}"; do acc+="$(json_str "$m"),"; done
  acc="${acc%,}"

  local has_static=false has_oauth=false has_tunnel=false
  for m in "${ACCESS_ARRAY[@]}"; do
    [[ "$m" == "static" ]] && has_static=true
    [[ "$m" == "oauth" ]] && has_oauth=true
    [[ "$m" == "tunnel" ]] && has_tunnel=true
  done

  local auth_modes=""
  if $has_static; then auth_modes+="\"static\","; fi
  if $has_oauth; then auth_modes+="\"oauth\","; fi
  auth_modes="${auth_modes%,}"

  local tun_json="null"
  if $has_tunnel; then
    tun_json="{\"tag\": $(json_str "$tunnel_tag"), \"tunnel_id\": $(json_str "$tunnel_id"), \"runtime_api_key\": $(json_str "$runtime_api_key"), \"http_proxy\": $(json_str "$http_proxy")}"
  fi

  local edge_enabled=false
  if $has_static || $has_oauth; then edge_enabled=true; fi

  # static_tokens: comma-separated string -> list
  local tokens_json
  if [[ -n "$static_tokens" ]]; then
    tokens_json="[$( (IFS=','; for t in $static_tokens; do echo "$t"; done) | while read -r t; do json_str "$t"; echo ","; done | tr -d '\n' | sed 's/,$//' )]"
  else
    tokens_json="[]"
  fi

  local oauth_json="{\"issuer\": $(json_str "$oauth_issuer"), \"resource\": $(json_str "$oauth_resource"), \"audience\": $(json_str "$oauth_audience"), \"required_scope\": $(json_str "$oauth_scope"), \"jwks_url\": $(json_str "$oauth_jwks"), \"authorization_servers\": $(json_str ""), \"scopes_supported\": $(json_str "")}"

  local edge_json="{\"enabled\": $edge_enabled, \"auth_modes\": [$auth_modes], \"tls\": $(json_str "$edge_tls"), \"public_port\": $(json_str "$edge_public_port"), \"cert_file\": $(json_str "$edge_cert"), \"key_file\": $(json_str "$edge_key"), \"static_token_prefix\": $(json_str "$static_prefix"), \"static_tokens\": $tokens_json, \"oauth\": $oauth_json}"

  local mcp_json="{\"tag\": $(json_str "$mcp_tag"), \"youtube_api_key\": $(json_str "$youtube_api_key"), \"enable_ytdlp\": $(json_str "$enable_ytdlp"), \"languages\": $(json_str "$languages"), \"max_chars\": $(json_str "$max_chars")}"

  printf '{"mcp": %s, "access": [%s], "tunnel": %s, "edge": %s}' \
    "$mcp_json" "$acc" "$tun_json" "$edge_json"
}

# ---- interactive flow --------------------------------------------------------

output_path="$DEFAULT_OUTPUT"
input_path=""

if [[ "$#" -gt 0 ]]; then
  while (($#)); do
    case "$1" in
      --output) (($# >= 2)) || die "Nach --output fehlt ein Dateiname."; output_path="$2"; shift 2 ;;
      --input) (($# >= 2)) || die "Nach --input fehlt ein Dateiname."; input_path="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unbekannte Option: $1" ;;
    esac
  done
fi

if [[ -n "$input_path" ]]; then
  # Non-interactive canonical input -> delegate to the shared renderer.
  _render_canonical "$(python3 -c "import sys; print(open(sys.argv[1]).read())" "$input_path")" \
    "$output_path" \
    || die "Rendering fehlgeschlagen (ungueltiger kanonischer Input?)."
  printf '%s\n' "Stack erfolgreich erzeugt (aus kanonischem Input): $output_path"
  exit 0
fi

# ---- interactive (capability-oriented) ---------------------------------------

printf '%s\n' \
  "" \
  "ChatGPT YouTube MCP - Multi-Client Portainer-Stack-Generator" \
  "============================================================" \
  "" \
  "Dieses Skript erzeugt nur den YAML-Code. Es installiert/startet nichts." \
  "Der MCP-Kern ist immer vorhanden und wird NIE oeffentlich publiziert."

# 1) common MCP/YouTube settings
prompt mcp_tag "[OPTIONAL] Version des YouTube-MCP-Images (example: latest)" "latest"

languages="de,en"; prompt languages "[OPTIONAL] Bevorzugte Transcript-Sprachen, kommasepariert" "de,en"
[[ "$languages" =~ ^[A-Za-z0-9_-]+([,][A-Za-z0-9_-]+)*$ ]] || die "Ungueltige Sprachenliste. Beispiel: de,en"

max_chars="60000"; prompt max_chars "[OPTIONAL] Maximale Transcript-Zeichen" "60000"
[[ "$max_chars" =~ ^[0-9]+$ ]] || die "Transcript-Limit muss eine Zahl sein."
((max_chars >= 1000 && max_chars <= 500000)) || die "Transcript-Limit muss zwischen 1000 und 500000 liegen."

# optional YouTube API key (masked confirm; empty allowed with confirmation)
youtube_api_key=""
while true; do
  prompt_secret_confirmed youtube_api_key \
    "[OPTIONAL] YouTube Data API Key (example: AIza...; Enter = leer)" false "YouTube API key"
  continue_no_key="false"
  if [[ -z "$youtube_api_key" ]]; then
    printf '%s\n' "No YouTube API key was entered."
    prompt_yes_no continue_no_key "Continue without a YouTube API key?" no
    [[ "$continue_no_key" == "true" ]] && break
    continue
  fi
  break
done

# yt-dlp
enable_ytdlp="true"; prompt_yes_no enable_ytdlp "[OPTIONAL] Inoffiziellen Transcript-Abruf ueber yt-dlp aktivieren?" yes
if [[ "$enable_ytdlp" == "true" ]]; then
  consent=""
  printf '%s\n' "Hinweis: Der Transcript-Abruf nutzt inoffizielle YouTube-Endpunkte." >&2
  while [[ "${consent^^}" != "JA" ]]; do
    prompt consent "[CONDITIONAL - MANDATORY] Consent (example: JA)"
    [[ "${consent^^}" == "JA" ]] || printf 'Bitte JA eingeben.\n' >&2
  done
fi

# 2) select access methods (capability model)
printf '%s\n' "" "Zugriffsarten:" >&2
ACCESS_ARRAY=()
prompt_select_multi access_methods \
  "Welche Zugriffsarten aktivieren? (Mehrfachauswahl; leer = keine)" \
  "local" "tunnel" "static" "oauth"
ACCESS_ARRAY=($access_methods)
if [[ ${#ACCESS_ARRAY[@]} -eq 0 ]]; then
  die "Mindestens eine Zugriffsart muss gewaehlt werden."
fi

has_tunnel=false; has_static=false; has_oauth=false
for m in "${ACCESS_ARRAY[@]}"; do
  case "$m" in
    tunnel) has_tunnel=true ;;
    static) has_static=true ;;
    oauth) has_oauth=true ;;
  esac
done

# 3a) tunnel-only must stay short; only ask tunnel questions when tunnel chosen.
tunnel_tag="0.1.0"; tunnel_id=""; runtime_api_key=""; http_proxy=""
if $has_tunnel; then
  prompt tunnel_tag "[OPTIONAL] Version des Tunnel-Images (example: 0.1.0)" "0.1.0"
  prompt tunnel_id "[MANDATORY] OpenAI Tunnel-ID (example: tunnel_0123456789abcdef)" ""
  [[ "$tunnel_id" =~ ^tunnel_[A-Za-z0-9_-]+$ ]] || die "Die Tunnel-ID muss mit tunnel_ beginnen."
  while true; do
    prompt_secret_confirmed runtime_api_key \
      "[MANDATORY] OpenAI Runtime API Key (example: sk-...)" true "OpenAI Runtime API key"
    [[ -n "$runtime_api_key" ]] && break
  done
  prompt_secret_confirmed http_proxy \
    "[OPTIONAL] Outbound-Proxy fuer den Tunnel, HTTPS_PROXY (Enter = keiner)" false "HTTPS proxy value"
fi

# 3b) public edge (static / oauth / both)
edge_enabled=false; edge_tls="ingress"; edge_cert=""; edge_key=""; edge_public_port="8443"
static_prefix="ytsk_"; static_tokens=""
oauth_issuer=""; oauth_resource=""; oauth_audience=""; oauth_scope=""; oauth_jwks=""

if $has_static || $has_oauth; then
  edge_enabled=true
  # TLS: OAuth (Claude) requires HTTPS in front of the resource. Offer
  # edge-terminated (cert/key in container) or external ingress.
  tls_default="ingress"
  if $has_oauth; then tls_default="edge"; fi
  prompt edge_tls "[OPTIONAL] TLS-Terminierung: edge (Zertifikat im Container) oder ingress (externer Proxy)? [$tls_default]" "$tls_default"
  case "$edge_tls" in
    edge)
      prompt edge_public_port "[OPTIONAL] Oeffentlicher HTTPS Port (host)" "8443"
      prompt edge_cert "[MANDATORY] Pfad zum TLS-Zertifikat im Container (example: /certs/tls.crt)" ""
      prompt edge_key "[MANDATORY] Pfad zum TLS-Schluessel im Container (example: /certs/tls.key)" ""
      [[ -n "$edge_cert" && -n "$edge_key" ]] \
        || die "Bei TLS 'edge' muessen Zertifikat UND Schluessel gesetzt sein."
      ;;
    ingress)
      edge_cert=""; edge_key=""
      ;;
    *)
      die "edge_tls muss 'edge' oder 'ingress' sein."
      ;;
  esac

  if $has_static; then
    prompt static_prefix "[OPTIONAL] Reserviertes Static-Token-Praefix" "ytsk_"
    # collect at least one static token
    static_tokens=""
    tok=""
    while true; do
      prompt_secret_confirmed tok \
        "[MANDATORY] Static Bearer Token (eines; weitere spaeter)" false "Static Bearer token"
      [[ -n "$tok" ]] && break
    done
    static_tokens="$tok"
    prompt_yes_no more_tokens "Weitere static Tokens fuer Rotation hinzufuegen?" no
    while [[ "$more_tokens" == "true" ]]; do
      prompt_secret_confirmed tok "[OPTIONAL] weiteres static Token (Enter = fertig)" false "Static Bearer token"
      if [[ -n "$tok" ]]; then
        static_tokens="$static_tokens,$tok"
      fi
      prompt_yes_no more_tokens "Noch ein Token hinzufuegen?" no
    done
    # static+oauth namespace guard: every static token must carry the prefix
    if $has_oauth; then
      bad=""
      IFS=',' read -r -a _toks <<< "$static_tokens"
      for _t in "${_toks[@]}"; do
        [[ "$_t" == "$static_prefix"* ]] || { bad="$_t"; break; }
      done
      [[ -z "$bad" ]] || die "Static token '$bad' beginnt nicht mit dem reservierten Praefix '$static_prefix'; bei static+OAuth auf einem Edge unzulaessig."
    fi
  fi

  if $has_oauth; then
    prompt oauth_issuer "[MANDATORY] OAuth Issuer URL (example: https://as.example.org/realms/master)" ""
    prompt oauth_resource "[MANDATORY] OAuth Resource/Protected URL (example: https://mcp.example.org/mcp)" ""
    [[ -n "$oauth_issuer" && -n "$oauth_resource" ]] \
      || die "Fuer OAuth sind Issuer UND Resource erforderlich."
    prompt oauth_audience "[OPTIONAL] Erwartete Audience (example: account)" ""
    prompt oauth_scope "[OPTIONAL] Benoetigter Scope (example: youtube-mcp)" ""
    prompt oauth_jwks "[OPTIONAL] JWKS-URL-Override (leer = via Discovery)" ""
  fi
fi

# 4) validate + render
canonical="$(_build_canonical)"
_render_canonical "$canonical" "$output_path" || die "Konfiguration ungueltig (siehe Fehlermeldung)."

printf '%s\n' \
  "" \
  "Stack erfolgreich erzeugt: $output_path" \
  "" \
  "Der MCP-Kern ist immer enthalten und wird NIE oeffentlich publiziert." \
  "Aktivierte Zugriffsarten:${ACCESS_ARRAY[*]}" \
  "Die Datei enthaelt Secrets und wurde mit restriktiven Dateirechten geschrieben."
exit 0
