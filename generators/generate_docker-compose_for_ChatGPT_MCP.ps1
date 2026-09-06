[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputPath = "portainer-youtube-mcp-stack.yml"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DefaultMcpTag = "latest"
$DefaultTunnelTag = "0.1.0"
$McpImageBase = "ghcr.io/dracoform/chatgpt-youtube-mcp"
$TunnelImageBase = "ghcr.io/dracoform/openai-mcp-tunnel"

function Read-Value {
    param(
        [Parameter(Mandatory)] [string]$Prompt,
        [string]$Default = "",
        [switch]$Required
    )
    while ($true) {
        $suffix = if ($Default) { " [$Default]" } else { "" }
        $value = Read-Host "$Prompt$suffix"
        if ([string]::IsNullOrWhiteSpace($value)) { $value = $Default }
        if (-not $Required -or -not [string]::IsNullOrWhiteSpace($value)) { return $value }
        Write-Warning "Eine Eingabe ist erforderlich."
    }
}

function Read-SecretText {
    # Read one hidden line, returning the plaintext. Empty input is allowed
    # (callers decide); the value is never printed.
    while ($true) {
        $secure = Read-Host $Prompt -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

function Get-SecretMask {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    $length = $Value.Length
    if ($length -le 4) { return ("X" * $length) }
    return ("X" * ($length - 4)) + $Value.Substring($length - 4)
}

function Test-SecretValid {
    # Reject CR, LF, other control characters, and leading/trailing
    # whitespace. Inner whitespace is allowed. No silent trimming.
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    if ($Value -ne $Value.Trim()) { return $false }
    foreach ($ch in $Value.ToCharArray()) {
        if ([char]::IsControl($ch)) { return $false }
    }
    return $true
}

function Read-SecretConfirmed {
    # Read a hidden secret, show a length-preserving mask (final four chars
    # visible; fully masked when four characters or shorter), print the
    # length separately, and require explicit confirmation. Rejected or
    # invalid input repeats entry. The complete secret is never printed and
    # never passed through command-line arguments.
    param(
        [Parameter(Mandatory)] [string]$Prompt,
        [switch]$Required,
        [Parameter(Mandatory)] [string]$Label
    )
    while ($true) {
        $value = Read-SecretText -Prompt $Prompt
        if ([string]::IsNullOrWhiteSpace($value)) {
            if (-not $Required) { return "" }
            Write-Warning "Eine Eingabe ist erforderlich."
            continue
        }
        if (-not (Test-SecretValid -Value $value)) {
            Write-Warning "Die Eingabe enthaelt Steuerzeichen oder fuehrende/nachfolgende Leerzeichen. Bitte erneut eingeben."
            continue
        }
        Write-Host ""
        Write-Host "$Label empfangen:"
        Write-Host (Get-SecretMask -Value $value)
        Write-Host "Laenge: $($value.Length) Zeichen"
        $confirm = Read-Host "Diesen Wert uebernehmen? [Y/n]"
        if ([string]::IsNullOrWhiteSpace($confirm)) { $confirm = "y" }
        switch ($confirm.ToLowerInvariant()) {
            { $_ -in @("j", "ja", "y", "yes") } { return $value }
            default { Write-Host "Eingabe wird wiederholt." }
        }
    }
}

function Read-YesNo {
    param(
        [Parameter(Mandatory)] [string]$Prompt,
        [bool]$Default = $true
    )
    $suffix = if ($Default) { "J/n" } else { "j/N" }
    while ($true) {
        $value = Read-Host "$Prompt [$suffix]"
        if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
        switch ($value.ToLowerInvariant()) {
            { $_ -in @("j", "ja", "y", "yes") } { return $true }
            { $_ -in @("n", "nein", "no") } { return $false }
            default { Write-Warning "Bitte mit ja oder nein antworten." }
        }
    }
}

function ConvertTo-YamlSingleQuoted {
    param([AllowEmptyString()] [string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

Write-Host ""
Write-Host "ChatGPT YouTube MCP - Portainer-Stack-Generator"
Write-Host "================================================"
Write-Host ""
Write-Host "Dieses Skript erzeugt nur den YAML-Code fur Portainer."
Write-Host "Es installiert weder Docker noch Container."
Write-Host ""
Write-Host "Kennzeichnung:"
Write-Host "  [MANDATORY]             Eingabe ist erforderlich."
Write-Host "  [OPTIONAL]              Enter ubernimmt den Standardwert oder lasst das Feld leer."
Write-Host "  [CONDITIONAL]           Nur erforderlich, wenn die genannte Funktion aktiv ist."
Write-Host ""

$mcpTag = Read-Value -Prompt "[OPTIONAL] Version des YouTube-MCP-Images (example: latest)" -Default $DefaultMcpTag
$mcpImage = "${McpImageBase}:${mcpTag}"

$tunnelTag = Read-Value -Prompt "[OPTIONAL] Version des Tunnel-Images (example: 0.1.0)" -Default $DefaultTunnelTag
$tunnelImage = "${TunnelImageBase}:${tunnelTag}"

# Optionaler YouTube API Key: Bestaetigung mit maskierter Anzeige.
# Ein Google-API-key hat aktuell die Form AIza + 35 Zeichen ([A-Za-z0-9_-]).
# Abweichende Formate erzeugen eine Warnung; ein explizites Ueberschreiben
# bleibt moeglich, damit kuenftige Formate nicht dauerhaft blockiert werden.
while ($true) {
    $youtubeApiKey = Read-SecretConfirmed -Prompt "[OPTIONAL] YouTube Data API Key (example: AIza...; Enter = leer)" -Label "YouTube API key"
    if ([string]::IsNullOrWhiteSpace($youtubeApiKey)) {
        Write-Host "No YouTube API key was entered."
        $continueNoKey = Read-YesNo -Prompt "Continue without a YouTube API key?" -Default $false
        if ($continueNoKey) { break }
        continue
    }
    if ($youtubeApiKey -notmatch '^AIza[A-Za-z0-9_-]{35}$') {
        Write-Warning "Der eingegebene Wert entspricht nicht dem erwarteten Format eines Google API keys (AIza + 35 Zeichen). Moegliche Ursache: doppelt eingefuegter Schluessel (pruefen Sie die angezeigte Laenge)."
        $keepFormat = Read-YesNo -Prompt "Den Wert trotz der Warnung uebernehmen?" -Default $false
        if ($keepFormat) { break }
        continue
    }
    break
}
$enableYtDlp = Read-YesNo -Prompt "[OPTIONAL] Inoffiziellen Transcript-Abruf uber yt-dlp aktivieren?" -Default $true
if ($enableYtDlp) {
    Write-Host "Hinweis: Der Transcript-Abruf nutzt inoffizielle YouTube-Endpunkte und"
    Write-Host "kann durch Rate-Limits oder Aenderungen bei YouTube beeintrachtigt werden."
    $consent = Read-Value -Prompt "[CONDITIONAL - MANDATORY] Consent: zum Bestatigen bitte JA eingeben (example: JA)" -Required
    if ($consent.ToUpperInvariant() -ne "JA") {
        throw "Einrichtung abgebrochen: Consent nicht bestatigt."
    }
}

$languages = Read-Value -Prompt "[OPTIONAL] Bevorzugte Transcript-Sprachen, kommasepariert (example: de,en)" -Default "de,en"
if ($languages -notmatch '^[A-Za-z0-9_-]+(,[A-Za-z0-9_-]+)*$') {
    throw "Ungultige Sprachenliste. Beispiel: de,en"
}

$maxCharsText = Read-Value -Prompt "[OPTIONAL] Maximale Transcript-Zeichen (example: 60000)" -Default "60000"
$maxChars = 0
if (-not [int]::TryParse($maxCharsText, [ref]$maxChars) -or $maxChars -lt 1000 -or $maxChars -gt 500000) {
    throw "Transcript-Limit muss zwischen 1000 und 500000 liegen."
}

$tunnelId = Read-Value -Prompt "[MANDATORY] OpenAI Tunnel-ID (example: tunnel_0123456789abcdef)" -Required
if ($tunnelId -notmatch '^tunnel_[A-Za-z0-9_-]+$') {
    throw "Die Tunnel-ID muss mit tunnel_ beginnen."
}
# Pflicht-Geheimnis: Runtime API Key (CONTROL_PLANE_API_KEY).
# Kein starres Laengen-/Prefix-Format; nur der bekannte Praefix 'sk-'
# verhindert die Warnung. Abweichende Werte erfordern explizite Bestaetigung.
while ($true) {
    $runtimeApiKey = Read-SecretConfirmed -Prompt "[MANDATORY] OpenAI Runtime API Key (example: sk-...; Eingabe verborgen)" -Required -Label "OpenAI Runtime API key"
    if ($runtimeApiKey -notmatch '^sk-') {
        Write-Warning "Der Wert beginnt nicht mit einem bekannten OpenAI-Key-Praefix (sk-)."
        $keepPrefix = Read-YesNo -Prompt "Den Wert trotz der Warnung uebernehmen?" -Default $false
        if ($keepPrefix) { break }
        continue
    }
    break
}
$httpProxy = Read-SecretConfirmed -Prompt "[OPTIONAL] Outbound-Proxy fur den Tunnel, HTTPS_PROXY (example: http://proxy:3128; Enter = keiner)" -Label "HTTPS proxy value"

if (Test-Path -LiteralPath $OutputPath) {
    if (-not (Read-YesNo -Prompt "[CONDITIONAL] Datei $OutputPath existiert. Uberschreiben?" -Default $false)) {
        throw "Keine Datei verandert."
    }
}

$ytDlpText = if ($enableYtDlp) { "true" } else { "false" }
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("# Von generate_docker-compose_for_ChatGPT_MCP.ps1 erzeugt. Enthalt Secrets; Zugriff entsprechend beschranken.")
$lines.Add("services:")
$lines.Add("  youtube-mcp:")
$lines.Add("    image: $(ConvertTo-YamlSingleQuoted $mcpImage)")
$lines.Add("    restart: unless-stopped")
$lines.Add("    environment:")
$lines.Add("      MCP_TRANSPORT: 'streamable-http'")
$lines.Add("      MCP_HOST: '0.0.0.0'")
$lines.Add("      MCP_PORT: '8765'")
$lines.Add("      YOUTUBE_API_KEY: $(ConvertTo-YamlSingleQuoted $youtubeApiKey)")
$lines.Add("      YOUTUBE_ENABLE_YTDLP: $(ConvertTo-YamlSingleQuoted $ytDlpText)")
$lines.Add("      YOUTUBE_TRANSCRIPT_MAX_CHARS: $(ConvertTo-YamlSingleQuoted $maxCharsText)")
$lines.Add("      YOUTUBE_DEFAULT_LANGUAGES: $(ConvertTo-YamlSingleQuoted $languages)")
$lines.Add("    expose:")
$lines.Add("      - '8765'")
$lines.Add("    read_only: true")
$lines.Add("    tmpfs:")
$lines.Add("      - /tmp:size=64m")
$lines.Add("    cap_drop:")
$lines.Add("      - ALL")
$lines.Add("    security_opt:")
$lines.Add("      - no-new-privileges:true")
$lines.Add("    networks:")
$lines.Add("      - youtube-mcp-internal")
$lines.Add("")
$lines.Add("  openai-tunnel:")
$lines.Add("    image: $(ConvertTo-YamlSingleQuoted $tunnelImage)")
$lines.Add("    restart: unless-stopped")
$lines.Add("    environment:")
$lines.Add("      CONTROL_PLANE_TUNNEL_ID: $(ConvertTo-YamlSingleQuoted $tunnelId)")
$lines.Add("      CONTROL_PLANE_API_KEY: $(ConvertTo-YamlSingleQuoted $runtimeApiKey)")
$lines.Add("      MCP_SERVER_URL: 'http://youtube-mcp:8765/mcp'")
if (-not [string]::IsNullOrWhiteSpace($httpProxy)) {
    $lines.Add("      HTTPS_PROXY: $(ConvertTo-YamlSingleQuoted $httpProxy)")
    $lines.Add("      NO_PROXY: 'youtube-mcp,localhost,127.0.0.1'")
}
$lines.Add("    depends_on:")
$lines.Add("      youtube-mcp:")
$lines.Add("        condition: service_healthy")
$lines.Add("    read_only: true")
$lines.Add("    tmpfs:")
$lines.Add("      - /tmp:size=16m")
$lines.Add("    cap_drop:")
$lines.Add("      - ALL")
$lines.Add("    security_opt:")
$lines.Add("      - no-new-privileges:true")
$lines.Add("    stop_grace_period: 30s")
$lines.Add("    networks:")
$lines.Add("      - youtube-mcp-internal")
$lines.Add("")
$lines.Add("networks:")
$lines.Add("  youtube-mcp-internal:")
$lines.Add("    driver: bridge")

$absoluteOutputPath = [IO.Path]::GetFullPath($OutputPath)
$parent = Split-Path -Parent $absoluteOutputPath
if (-not (Test-Path -LiteralPath $parent)) {
    [void](New-Item -ItemType Directory -Path $parent -Force)
}
$utf8NoBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($absoluteOutputPath, ($lines -join "`n") + "`n", $utf8NoBom)

Write-Host ""
Write-Host "Stack erfolgreich erzeugt: $absoluteOutputPath"
Write-Host ""
Write-Host "Enthaltene Images:"
Write-Host "  MCP:    $mcpImage"
Write-Host "  Tunnel: $tunnelImage"
Write-Host ""
Write-Warning "Die Datei enthalt die eingegebenen Secrets im Klartext."
Write-Host "YouTube API key guide:"
Write-Host "  https://github.com/Dracoform/chatgpt-youtube-mcp/blob/main/docs/YOUTUBE_API_KEY.md"
Write-Host "Der Tunnel-Container published keinen Host-Port; die Health-Endpunkte des"
Write-Host "Tunnel-Clients bleiben innerhalb des Containers (Loopback)."
Write-Host "In Portainer: Stacks -> Add stack -> Web editor -> Inhalt einfugen -> Deploy the stack"
