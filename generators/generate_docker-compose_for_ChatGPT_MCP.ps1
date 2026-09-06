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

$youtubeApiKey = Read-SecretText -Prompt "[OPTIONAL] YouTube Data API Key (example: AIza...; Enter = leer)"
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
$runtimeApiKey = Read-SecretText -Prompt "[MANDATORY] OpenAI Runtime API Key (example: sk-...; Eingabe verborgen)" -Required
$httpProxy = Read-SecretText -Prompt "[OPTIONAL] Outbound-Proxy fur den Tunnel, HTTPS_PROXY (example: http://proxy:3128; Enter = keiner)"

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
$lines.Add("      - youtube-mcp")
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
Write-Host "Der Tunnel-Container published keinen Host-Port; die Health-Endpunkte des"
Write-Host "Tunnel-Clients bleiben innerhalb des Containers (Loopback)."
Write-Host "In Portainer: Stacks -> Add stack -> Web editor -> Inhalt einfugen -> Deploy the stack"
