[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputPath = "portainer-youtube-mcp-stack.yml"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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
    param(
        [Parameter(Mandatory)] [string]$Prompt,
        [switch]$Required
    )
    while ($true) {
        $secure = Read-Host $Prompt -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        if (-not $Required -or -not [string]::IsNullOrWhiteSpace($value)) { return $value }
        Write-Warning "Eine Eingabe ist erforderlich."
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
Write-Host "Dieses Skript erzeugt nur den YAML-Code für Portainer."
Write-Host "Es installiert weder Docker noch Container."
Write-Host ""
Write-Host "Kennzeichnung:"
Write-Host "  [MANDATORY]             Eingabe ist erforderlich."
Write-Host "  [OPTIONAL]              Enter übernimmt den Standardwert oder lässt das Feld leer."
Write-Host "  [CONDITIONAL]           Nur erforderlich, wenn die genannte Funktion aktiv ist."
Write-Host ""

$registryNamespace = Read-Value -Prompt "[MANDATORY - TEMPORARY] Registry-Namespace (example: ghcr.io/my-github-name)" -Required
$registryNamespace = $registryNamespace.TrimEnd("/")
$mcpTag = Read-Value -Prompt "[OPTIONAL] Version des YouTube-MCP-Images (example: 0.1.0)" -Default "0.1.0"
$tunnelTag = Read-Value -Prompt "[OPTIONAL] Version des Tunnel-Images (example: 0.1.0)" -Default "0.1.0"
$mcpImage = "${registryNamespace}/youtube-current-data-mcp:${mcpTag}"
$tunnelImage = "${registryNamespace}/openai-mcp-tunnel:${tunnelTag}"

$youtubeApiKey = Read-SecretText -Prompt "[OPTIONAL] YouTube Data API Key (example: AIza...; Enter = leer)"
$enableYtDlp = Read-YesNo -Prompt "[OPTIONAL] Inoffiziellen Transcript-Abruf über yt-dlp aktivieren?" -Default $true
if ($enableYtDlp) {
    Write-Host "Hinweis: Der Transcript-Abruf nutzt inoffizielle YouTube-Endpunkte und"
    Write-Host "kann durch Rate-Limits oder Änderungen bei YouTube beeinträchtigt werden."
    $consent = Read-Value -Prompt "[CONDITIONAL - MANDATORY] Consent: zum Bestätigen bitte JA eingeben (example: JA)" -Required
    if ($consent.ToUpperInvariant() -ne "JA") {
        throw "Einrichtung abgebrochen: Consent nicht bestätigt."
    }
}

$languages = Read-Value -Prompt "[OPTIONAL] Bevorzugte Transcript-Sprachen, kommasepariert (example: de,en)" -Default "de,en"
if ($languages -notmatch '^[A-Za-z0-9_-]+(,[A-Za-z0-9_-]+)*$') {
    throw "Ungültige Sprachenliste. Beispiel: de,en"
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
$runtimeApiKey = Read-SecretText -Prompt "[MANDATORY] OpenAI Runtime API Key (example: sk-...; Eingabe verborgen)" -Required

if (Test-Path -LiteralPath $OutputPath) {
    if (-not (Read-YesNo -Prompt "[CONDITIONAL] Datei $OutputPath existiert. Überschreiben?" -Default $false)) {
        throw "Keine Datei verändert."
    }
}

$ytDlpText = if ($enableYtDlp) { "true" } else { "false" }
$lines = @(
    "# Von generate_docker-compose_for_ChatGPT_MCP.ps1 erzeugt. Enthält Secrets; Zugriff entsprechend beschränken."
    "services:"
    "  youtube-mcp:"
    "    image: $(ConvertTo-YamlSingleQuoted $mcpImage)"
    "    restart: unless-stopped"
    "    environment:"
    "      MCP_TRANSPORT: 'streamable-http'"
    "      MCP_HOST: '0.0.0.0'"
    "      MCP_PORT: '8765'"
    "      YOUTUBE_API_KEY: $(ConvertTo-YamlSingleQuoted $youtubeApiKey)"
    "      YOUTUBE_ENABLE_YTDLP: $(ConvertTo-YamlSingleQuoted $ytDlpText)"
    "      YOUTUBE_TRANSCRIPT_MAX_CHARS: $(ConvertTo-YamlSingleQuoted $maxCharsText)"
    "      YOUTUBE_DEFAULT_LANGUAGES: $(ConvertTo-YamlSingleQuoted $languages)"
    "    expose:"
    "      - '8765'"
    "    read_only: true"
    "    tmpfs:"
    "      - /tmp:size=64m"
    "    security_opt:"
    "      - no-new-privileges:true"
    "    networks:"
    "      - youtube-mcp-internal"
    ""
    "  openai-tunnel:"
    "    image: $(ConvertTo-YamlSingleQuoted $tunnelImage)"
    "    restart: unless-stopped"
    "    environment:"
    "      OPENAI_TUNNEL_ID: $(ConvertTo-YamlSingleQuoted $tunnelId)"
    "      CONTROL_PLANE_API_KEY: $(ConvertTo-YamlSingleQuoted $runtimeApiKey)"
    "      MCP_SERVER_URL: 'http://youtube-mcp:8765/mcp'"
    "    depends_on:"
    "      - youtube-mcp"
    "    volumes:"
    "      - tunnel-config:/config"
    "    security_opt:"
    "      - no-new-privileges:true"
    "    networks:"
    "      - youtube-mcp-internal"
    ""
    "networks:"
    "  youtube-mcp-internal:"
    "    driver: bridge"
    ""
    "volumes:"
    "  tunnel-config:"
)

$absoluteOutputPath = [IO.Path]::GetFullPath($OutputPath)
$parent = Split-Path -Parent $absoluteOutputPath
if (-not (Test-Path -LiteralPath $parent)) {
    [void](New-Item -ItemType Directory -Path $parent -Force)
}
$utf8NoBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($absoluteOutputPath, ($lines -join [Environment]::NewLine) + [Environment]::NewLine, $utf8NoBom)

Write-Host ""
Write-Host "Stack erfolgreich erzeugt: $absoluteOutputPath"
Write-Host ""
Write-Host "Enthaltene Images:"
Write-Host "  MCP:    $mcpImage"
Write-Host "  Tunnel: $tunnelImage"
Write-Host ""
Write-Warning "Die Datei enthält die eingegebenen Secrets im Klartext."
Write-Host "In Portainer: Stacks -> Add stack -> Web editor -> Inhalt einfügen -> Deploy the stack"
