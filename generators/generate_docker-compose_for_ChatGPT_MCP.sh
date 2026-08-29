#!/usr/bin/env bash
set -Eeuo pipefail

PROGRAM_NAME="generate_docker-compose_for_ChatGPT_MCP.sh"
DEFAULT_OUTPUT="portainer-youtube-mcp-stack.yml"
DEFAULT_LANGUAGES="de,en"
DEFAULT_MAX_CHARS="60000"
DEFAULT_MCP_TAG="0.1.0"
DEFAULT_TUNNEL_TAG="0.1.0"

usage() {
  printf '%s\n' \
    "Gefuehrter Generator fuer einen Portainer-Stack mit YouTube MCP und OpenAI-Tunnel." \
    "" \
    "Verwendung:" \
    "  ./$PROGRAM_NAME [--output DATEI]" \
    "" \
    "Das Skript installiert und startet nichts. Es erzeugt nur eine YAML-Datei."
}

die() {
  printf 'Fehler: %s\n' "$1" >&2
  exit 1
}

prompt() {
  local __result_var="$1" prompt_text="$2" default_value="${3-}" answer
  if [[ -n "$default_value" ]]; then
    read -r -p "$prompt_text [$default_value]: " answer
    answer="${answer:-$default_value}"
  else
    read -r -p "$prompt_text: " answer
  fi
  printf -v "$__result_var" '%s' "$answer"
}

prompt_required() {
  local __result_var="$1" prompt_text="$2" answer=""
  while [[ -z "$answer" ]]; do
    read -r -p "$prompt_text: " answer
    [[ -n "$answer" ]] || printf 'Eine Eingabe ist erforderlich.\n' >&2
  done
  printf -v "$__result_var" '%s' "$answer"
}

prompt_secret() {
  local __result_var="$1" prompt_text="$2" required="${3:-false}" answer=""
  while true; do
    read -r -s -p "$prompt_text: " answer
    printf '\n' >&2
    if [[ "$required" != "true" || -n "$answer" ]]; then
      break
    fi
    printf 'Eine Eingabe ist erforderlich.\n' >&2
  done
  printf -v "$__result_var" '%s' "$answer"
}

prompt_yes_no() {
  local __result_var="$1" prompt_text="$2" default_value="${3:-yes}" answer suffix
  [[ "$default_value" == "yes" ]] && suffix="J/n" || suffix="j/N"
  while true; do
    read -r -p "$prompt_text [$suffix]: " answer
    answer="${answer:-$default_value}"
    case "${answer,,}" in
      j|ja|y|yes) printf -v "$__result_var" '%s' "true"; return ;;
      n|nein|no) printf -v "$__result_var" '%s' "false"; return ;;
      *) printf 'Bitte mit ja oder nein antworten.\n' >&2 ;;
    esac
  done
}

yaml_quote() {
  local value="$1"
  value=${value//\'/\'\'}
  printf "'%s'" "$value"
}

output_path="$DEFAULT_OUTPUT"
while (($#)); do
  case "$1" in
    --output)
      (($# >= 2)) || die "Nach --output fehlt ein Dateiname."
      output_path="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) die "Unbekannte Option: $1" ;;
  esac
done

printf '%s\n' \
  "" \
  "ChatGPT YouTube MCP - Portainer-Stack-Generator" \
  "================================================" \
  "" \
  "Dieses Skript erzeugt nur den YAML-Code fuer Portainer." \
  "Es installiert weder Docker noch Container." \
  "" \
  "Kennzeichnung:" \
  "  [MANDATORY]             Eingabe ist erforderlich." \
  "  [OPTIONAL]              Enter uebernimmt den Standardwert oder laesst das Feld leer." \
  "  [CONDITIONAL]           Nur erforderlich, wenn die genannte Funktion aktiv ist." \
  ""

prompt_required registry_namespace "[MANDATORY - TEMPORARY] Registry-Namespace (example: ghcr.io/my-github-name)"
prompt mcp_tag "[OPTIONAL] Version des YouTube-MCP-Images (example: 0.1.0)" "$DEFAULT_MCP_TAG"
prompt tunnel_tag "[OPTIONAL] Version des Tunnel-Images (example: 0.1.0)" "$DEFAULT_TUNNEL_TAG"
mcp_image="${registry_namespace%/}/youtube-current-data-mcp:$mcp_tag"
tunnel_image="${registry_namespace%/}/openai-mcp-tunnel:$tunnel_tag"

prompt_secret youtube_api_key "[OPTIONAL] YouTube Data API Key (example: AIza...; Enter = leer)" false
prompt_yes_no enable_ytdlp "[OPTIONAL] Inoffiziellen Transcript-Abruf ueber yt-dlp aktivieren?" yes
if [[ "$enable_ytdlp" == "true" ]]; then
  printf '%s\n' \
    "Hinweis: Der Transcript-Abruf nutzt inoffizielle YouTube-Endpunkte und" \
    "kann durch Rate-Limits oder Aenderungen bei YouTube beeintraechtigt werden."
  prompt_required consent "[CONDITIONAL - MANDATORY] Consent: zum Bestaetigen bitte JA eingeben (example: JA)"
  [[ "${consent^^}" == "JA" ]] || die "Einrichtung abgebrochen: Consent nicht bestaetigt."
fi

prompt languages "[OPTIONAL] Bevorzugte Transcript-Sprachen, kommasepariert (example: de,en)" "$DEFAULT_LANGUAGES"
[[ "$languages" =~ ^[A-Za-z0-9_-]+([,][A-Za-z0-9_-]+)*$ ]] || die "Ungueltige Sprachenliste. Beispiel: de,en"

prompt max_chars "[OPTIONAL] Maximale Transcript-Zeichen (example: 60000)" "$DEFAULT_MAX_CHARS"
[[ "$max_chars" =~ ^[0-9]+$ ]] || die "Transcript-Limit muss eine Zahl sein."
((max_chars >= 1000 && max_chars <= 500000)) || die "Transcript-Limit muss zwischen 1000 und 500000 liegen."

prompt_required tunnel_id "[MANDATORY] OpenAI Tunnel-ID (example: tunnel_0123456789abcdef)"
[[ "$tunnel_id" =~ ^tunnel_[A-Za-z0-9_-]+$ ]] || die "Die Tunnel-ID muss mit tunnel_ beginnen."
prompt_secret runtime_api_key "[MANDATORY] OpenAI Runtime API Key (example: sk-...; Eingabe verborgen)" true

if [[ -e "$output_path" ]]; then
  prompt_yes_no overwrite "[CONDITIONAL] Datei $output_path existiert. Ueberschreiben?" no
  [[ "$overwrite" == "true" ]] || die "Keine Datei veraendert."
fi

tmp_path="${output_path}.tmp.$$"
trap 'rm -f "$tmp_path"' EXIT

{
  printf '%s\n' \
    "# Von generate_docker-compose_for_ChatGPT_MCP.sh erzeugt. Enthaelt Secrets; Zugriff entsprechend beschraenken." \
    "services:" \
    "  youtube-mcp:" \
    "    image: $(yaml_quote "$mcp_image")" \
    "    restart: unless-stopped" \
    "    environment:" \
    "      MCP_TRANSPORT: 'streamable-http'" \
    "      MCP_HOST: '0.0.0.0'" \
    "      MCP_PORT: '8765'" \
    "      YOUTUBE_API_KEY: $(yaml_quote "$youtube_api_key")" \
    "      YOUTUBE_ENABLE_YTDLP: $(yaml_quote "$enable_ytdlp")" \
    "      YOUTUBE_TRANSCRIPT_MAX_CHARS: $(yaml_quote "$max_chars")" \
    "      YOUTUBE_DEFAULT_LANGUAGES: $(yaml_quote "$languages")" \
    "    expose:" \
    "      - '8765'" \
    "    read_only: true" \
    "    tmpfs:" \
    "      - /tmp:size=64m" \
    "    security_opt:" \
    "      - no-new-privileges:true" \
    "    networks:" \
    "      - youtube-mcp-internal" \
    "" \
    "  openai-tunnel:" \
    "    image: $(yaml_quote "$tunnel_image")" \
    "    restart: unless-stopped" \
    "    environment:" \
    "      OPENAI_TUNNEL_ID: $(yaml_quote "$tunnel_id")" \
    "      CONTROL_PLANE_API_KEY: $(yaml_quote "$runtime_api_key")" \
    "      MCP_SERVER_URL: 'http://youtube-mcp:8765/mcp'" \
    "    depends_on:" \
    "      - youtube-mcp" \
    "    volumes:" \
    "      - tunnel-config:/config" \
    "    security_opt:" \
    "      - no-new-privileges:true" \
    "    networks:" \
    "      - youtube-mcp-internal" \
    "" \
    "networks:" \
    "  youtube-mcp-internal:" \
    "    driver: bridge" \
    "" \
    "volumes:" \
    "  tunnel-config:"
} >"$tmp_path"

chmod 600 "$tmp_path"
mv -f "$tmp_path" "$output_path"
trap - EXIT

printf '%s\n' \
  "" \
  "Stack erfolgreich erzeugt: $output_path" \
  "" \
  "Enthaltene Images:" \
  "  MCP:    $mcp_image" \
  "  Tunnel: $tunnel_image" \
  "" \
  "Die Datei enthaelt Secrets und wurde mit restriktiven Dateirechten geschrieben." \
  "In Portainer: Stacks -> Add stack -> Web editor -> Inhalt einfuegen -> Deploy the stack"
