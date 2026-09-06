#!/usr/bin/env bash
set -Eeuo pipefail

PROGRAM_NAME="generate_docker-compose_for_ChatGPT_MCP.sh"
DEFAULT_OUTPUT="portainer-youtube-mcp-stack.yml"
DEFAULT_LANGUAGES="de,en"
DEFAULT_MAX_CHARS="60000"
DEFAULT_MCP_TAG="latest"
DEFAULT_TUNNEL_TAG="0.1.0"
MCP_IMAGE_BASE="ghcr.io/dracoform/chatgpt-youtube-mcp"
TUNNEL_IMAGE_BASE="ghcr.io/dracoform/openai-mcp-tunnel"

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
    read -r -p "$prompt_text [$default_value]: " answer || true
    answer="${answer:-$default_value}"
  else
    read -r -p "$prompt_text: " answer || true
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

# Print a verifiable masked representation of a secret: every character
# masked except the final four; values of four characters or fewer are
# masked completely. Never prints the secret itself.
mask_secret() {
  local value="$1" length="${#1}" i shown="" masked=""
  if ((length <= 4)); then
    for ((i = 0; i < length; i++)); do masked+='X'; done
    printf '%s' "$masked"
  else
    shown="${value: -4}"
    for ((i = 0; i < length - 4; i++)); do masked+='X'; done
    printf '%s%s' "$masked" "$shown"
  fi
}

# A secret is invalid if it contains CR, LF, NUL, other control characters,
# or leading/trailing whitespace. Whitespace inside the value is allowed.
secret_has_control_chars_or_edge_whitespace() {
  local value="$1"
  [[ "$value" != "${value#"${value%%[!\ \	]*}"}" || "$value" != "${value%"${value##*[!\ \	]}"}" ]] && return 0
  # reject C0/C1 control characters anywhere in the value
  [[ "$value" =~ [[:cntrl:]] ]] && return 0
  return 1
}

# Read a secret repeatedly until it passes validation and the user confirms
# the displayed masked representation. The value is never echoed and never
# passed through command-line arguments of any child process.
prompt_secret_confirmed() {
  local __result_var="$1" prompt_text="$2" required="${3:-false}" \
        label="$4" format_warn="${5:-}" answer
  while true; do
    answer=""
    while true; do
      # IFS= preserves leading/trailing whitespace so rule "no silent
      # trimming" is enforceable; without it `read` strips edge whitespace.
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
        printf 'Warnung: Die Eingabe enthaelt Steuerzeichen oder fuehrende/nachfolgende Leerzeichen.\n' >&2
        printf 'Bitte erneut eingeben.\n' >&2
        continue
      fi
      break
    done
    if [[ -z "$answer" ]]; then
      # empty answer on an optional secret: caller decides what happens next
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
  if [[ -n "$format_warn" && ! "$answer" =~ $format_warn ]]; then
    printf 'Warnung: %s\n' "$label" >&2
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

prompt mcp_tag "[OPTIONAL] Version des YouTube-MCP-Images (example: latest)" "$DEFAULT_MCP_TAG"
mcp_image="${MCP_IMAGE_BASE}:${mcp_tag}"

prompt tunnel_tag "[OPTIONAL] Version des Tunnel-Images (example: 0.1.0)" "$DEFAULT_TUNNEL_TAG"
tunnel_image="${TUNNEL_IMAGE_BASE}:${tunnel_tag}"

# Optionaler YouTube API Key: Bestaetigung mit maskierter Anzeige.
# Ein Google-API-Key hat aktuell die Form AIza + 35 Zeichen ([A-Za-z0-9_-]).
# Abweichende Formate erzeugen eine Warnung; ein explizites Ueberschreiben
# bleibt moeglich, damit kuenftige Formate nicht dauerhaft blockiert werden.
GOOGLE_KEY_PATTERN='^AIza[A-Za-z0-9_-]{35}$'
while true; do
  prompt_secret_confirmed youtube_api_key \
    "[OPTIONAL] YouTube Data API Key (example: AIza...; Enter = leer)" false \
    "YouTube API key"
  if [[ -z "$youtube_api_key" ]]; then
    printf '%s\n' "No YouTube API key was entered."
    prompt_yes_no continue_no_key "Continue without a YouTube API key?" no
    [[ "$continue_no_key" == "true" ]] && break
    continue
  fi
  if [[ ! "$youtube_api_key" =~ $GOOGLE_KEY_PATTERN ]]; then
    printf 'Warnung: Der eingegebene Wert entspricht nicht dem erwarteten Format eines Google API keys (AIza + 35 Zeichen).\n' >&2
    printf 'Moegliche Ursache: doppelt eingefuegter Schluessel (pruefen Sie die angezeigte Laenge).\n' >&2
    prompt_yes_no keep_format "Den Wert trotz der Warnung uebernehmen?" no
    [[ "$keep_format" == "true" ]] && break
    continue
  fi
  break
done
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

# Pflicht-Geheimnis: Runtime API Key (CONTROL_PLANE_API_KEY).
# Kein starres Laengen-/Prefix-Format (OpenAI-Key-Formate koennen sich aendern);
# nur ein bekannter Praefix 'sk-' verhindert die Warnung. Abweichende Werte
# erfordern eine explizite Bestaetigung statt stiller Akzeptanz.
while true; do
  prompt_secret_confirmed runtime_api_key \
    "[MANDATORY] OpenAI Runtime API Key (example: sk-...; Eingabe verborgen)" true \
    "OpenAI Runtime API key"
  if [[ ! "$runtime_api_key" =~ ^sk- ]]; then
    printf 'Warnung: Der Wert beginnt nicht mit einem bekannten OpenAI-Key-Praefix (sk-).\n' >&2
    prompt_yes_no keep_prefix "Den Wert trotz der Warnung uebernehmen?" no
    [[ "$keep_prefix" == "true" ]] && break
    continue
  fi
  break
done

prompt_secret_confirmed http_proxy \
  "[OPTIONAL] Outbound-Proxy fuer den Tunnel, HTTPS_PROXY (example: http://proxy:3128; Enter = keiner)" false \
  "HTTPS proxy value"

if [[ -e "$output_path" ]]; then
  prompt_yes_no overwrite "[CONDITIONAL] Datei $output_path existiert. Ueberschreiben?" no
  [[ "$overwrite" == "true" ]] || die "Keine Datei veraendert."
fi

tmp_path="${output_path}.tmp.$$"
trap 'rm -f "$tmp_path"' EXIT

# Tunnel-Umgebung: nur gesetzte Proxy-Variablen landen in der YAML.
proxy_lines=""
if [[ -n "$http_proxy" ]]; then
  proxy_lines=$(printf '%s\n' \
    "      HTTPS_PROXY: $(yaml_quote "$http_proxy")" \
    "      NO_PROXY: 'youtube-mcp,localhost,127.0.0.1'")
fi

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
    "    cap_drop:" \
    "      - ALL" \
    "    security_opt:" \
    "      - no-new-privileges:true" \
    "    networks:" \
    "      - youtube-mcp-internal" \
    "" \
    "  openai-tunnel:" \
    "    image: $(yaml_quote "$tunnel_image")" \
    "    restart: unless-stopped" \
    "    environment:" \
    "      CONTROL_PLANE_TUNNEL_ID: $(yaml_quote "$tunnel_id")" \
    "      CONTROL_PLANE_API_KEY: $(yaml_quote "$runtime_api_key")" \
    "      MCP_SERVER_URL: 'http://youtube-mcp:8765/mcp'"

  if [[ -n "$proxy_lines" ]]; then
    printf '%s\n' "$proxy_lines"
  fi

  printf '%s\n' \
    "    depends_on:" \
    "      - youtube-mcp" \
    "    read_only: true" \
    "    tmpfs:" \
    "      - /tmp:size=16m" \
    "    cap_drop:" \
    "      - ALL" \
    "    security_opt:" \
    "      - no-new-privileges:true" \
    "    stop_grace_period: 30s" \
    "    networks:" \
    "      - youtube-mcp-internal" \
    "" \
    "networks:" \
    "  youtube-mcp-internal:" \
    "    driver: bridge"
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
  "Der Tunnel-Container published keinen Host-Port; die Health-Endpunkte des" \
  "Tunnel-Clients bleiben innerhalb des Containers (Loopback)." \
  "In Portainer: Stacks -> Add stack -> Web editor -> Inhalt einfuegen -> Deploy the stack"
