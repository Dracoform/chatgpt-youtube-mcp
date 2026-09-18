[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputPath = "portainer-youtube-mcp-stack.yml",
    [Parameter()]
    [string]$InputPath = ""
)

# Capability-oriented generator for the multi-client YouTube MCP (Stage 4).
# Mirrors generators/generate_docker-compose_for_ChatGPT_MCP.sh and produces the
# same Compose YAML (cross-generator equivalence). Rendering is delegated to
# generators/canonical_model.py (the single source of truth), so this generator
# and the Bash generator produce byte-identical output for the same canonical
# input.
#
# ACCESS METHODS (independently selectable, composable):
#   local  -> core on host loopback 127.0.0.1:8765 (generic MCP clients)
#   tunnel -> OpenAI Secure MCP Tunnel DIRECT to the core (ChatGPT/OpenAI)
#   static -> public edge with static Bearer auth (DeepSeek Harness, LibreChat static)
#   oauth  -> public edge with OAuth access-token validation (Claude)
#   static+oauth -> ONE edge handles both
#
# The MCP core is ALWAYS present and NEVER published on a public interface.
#
#   -InputPath <canonical.json>  non-interactive: render the given canonical input
#   (no -InputPath)              interactive capability flow

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$CanonicalModule = "generators.canonical_model"

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
    # Read one hidden line, returning plaintext. Empty input allowed (caller
    # decides). Value never printed, never passed via command-line args.
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Get-SecretMask {
    param([AllowEmptyString()] [string]$Value)
    $length = $Value.Length
    if ($length -le 4) { return ("X" * $length) }
    return ("X" * ($length - 4)) + $Value.Substring($length - 4)
}

function Test-SecretValid {
    param([AllowEmptyString()] [string]$Value)
    if ($Value -ne $Value.Trim()) { return $false }
    foreach ($ch in $Value.ToCharArray()) {
        if ([char]::IsControl($ch)) { return $false }
    }
    return $true
}

function Read-SecretConfirmed {
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

function Select-AccessMethods {
    # Multi-select from a numbered list; returns selected names.
    Write-Host "Zugriffsarten:"
    Write-Host "  1) local   - MCP-Kern auf Host-Loopback (lokale/vertraute Clients)"
    Write-Host "  2) tunnel  - OpenAI Secure MCP Tunnel direkt zum Kern (ChatGPT/OpenAI)"
    Write-Host "  3) static  - oeffentlicher Edge mit statischem Bearer-Token"
    Write-Host "  4) oauth   - oeffentlicher Edge mit OAuth-Token-Validierung (Claude)"
    $options = @("local", "tunnel", "static", "oauth")
    while ($true) {
        $sel = Read-Host "Wahl (Nummern, durch Leerzeichen getrennt; leer = keine)"
        $result = @()
        foreach ($tok in ($sel -split "\s+")) {
            if ($tok -match '^[1-4]$') {
                $result += $options[[int]$tok - 1]
            }
        }
        if ($result.Count -gt 0) { return @($result) }
        Write-Warning "Mindestens eine Zugriffsart muss gewaehlt werden."
    }
}

function Invoke-CanonicalRenderer {
    param(
        [Parameter(Mandatory)] [string]$CanonicalJsonPath,
        [Parameter(Mandatory)] [string]$OutPath
    )
    # Delegate to the shared canonical renderer (single source of truth).
    Push-Location $RepoDir
    try {
        & python3 -m $CanonicalModule $CanonicalJsonPath --out $OutPath
        if ($LASTEXITCODE -ne 0) { throw "canonical_model render failed (exit $LASTEXITCODE)" }
    }
    finally {
        Pop-Location
    }
}

# ==================== non-interactive (--input) ====================
if (-not [string]::IsNullOrWhiteSpace($InputPath)) {
    if (-not (Test-Path -LiteralPath $InputPath)) {
        throw "Input file not found: $InputPath"
    }
    $absInput = (Resolve-Path -LiteralPath $InputPath).Path
    $absOut = [IO.Path]::GetFullPath($OutputPath)
    Invoke-CanonicalRenderer -CanonicalJsonPath $absInput -OutPath $absOut
    Write-Host ""
    Write-Host "Stack erfolgreich erzeugt (aus kanonischem Input): $absOut"
    exit 0
}

# ==================== interactive capability flow ====================
Write-Host ""
Write-Host "ChatGPT YouTube MCP - Multi-Client Portainer-Stack-Generator"
Write-Host "============================================================"
Write-Host ""
Write-Host "Dieses Skript erzeugt nur den YAML-Code. Es installiert/startet nichts."
Write-Host "Der MCP-Kern ist immer vorhanden und wird NIE oeffentlich publiziert."
Write-Host ""

# 1) common MCP/YouTube settings
$mcpTag = Read-Value -Prompt "[OPTIONAL] Version des YouTube-MCP-Images (example: latest)" -Default "latest"
$languages = Read-Value -Prompt "[OPTIONAL] Bevorzugte Transcript-Sprachen, kommasepariert (example: de,en)" -Default "de,en"
if ($languages -notmatch '^[A-Za-z0-9_-]+(,[A-Za-z0-9_-]+)*$') {
    throw "Ungueltige Sprachenliste."
}
$maxCharsText = Read-Value -Prompt "[OPTIONAL] Maximale Transcript-Zeichen (example: 60000)" -Default "60000"
$maxChars = 0
if (-not [int]::TryParse($maxCharsText, [ref]$maxChars) -or $maxChars -lt 1000 -or $maxChars -gt 500000) {
    throw "Transcript-Limit muss zwischen 1000 und 500000 liegen."
}

# optional YouTube API key (masked confirm; empty allowed with confirmation)
$continueNoKey = $false
$youtubeApiKey = ""
while ($true) {
    $youtubeApiKey = Read-SecretConfirmed -Prompt "[OPTIONAL] YouTube Data API Key (example: AIza...; Enter = leer)" -Label "YouTube API key"
    if ([string]::IsNullOrWhiteSpace($youtubeApiKey)) {
        Write-Host "No YouTube API key was entered."
        $continueNoKey = Read-YesNo -Prompt "Continue without a YouTube API key?" -Default $false
        if ($continueNoKey) { break }
        continue
    }
    break
}

# yt-dlp (consent required only when enabled)
$enableYtDlp = Read-YesNo -Prompt "[OPTIONAL] Inoffiziellen Transcript-Abruf ueber yt-dlp aktivieren?" -Default $true
$consent = ""
if ($enableYtDlp) {
    Write-Host "Hinweis: Der Transcript-Abruf nutzt inoffizielle YouTube-Endpunkte und kann durch Rate-Limits oder Aenderungen bei YouTube beeintraechtigt werden."
    while ($consent.ToUpperInvariant() -ne "JA") {
        $consent = Read-Value -Prompt "[CONDITIONAL - MANDATORY] Consent (example: JA)" -Required
        if ($consent.ToUpperInvariant() -ne "JA") { Write-Warning "Bitte JA eingeben." }
    }
}

# 2) capability selection
$AccessArray = @(Select-AccessMethods)

# 3a) tunnel (only asked when tunnel selected) - tunnel-only stays short & does
# NOT ask OAuth questions.
$tunnelTag = "0.1.0"; $tunnelId = ""; $runtimeApiKey = ""; $httpProxy = ""
if ($AccessArray -contains "tunnel") {
    $tunnelTag = Read-Value -Prompt "[OPTIONAL] Version des Tunnel-Images (example: 0.1.0)" -Default "0.1.0"
    $tunnelId = Read-Value -Prompt "[MANDATORY] OpenAI Tunnel-ID (example: tunnel_0123456789abcdef)" -Required
    if ($tunnelId -notmatch '^tunnel_[A-Za-z0-9_-]+$') {
        throw "Die Tunnel-ID muss mit tunnel_ beginnen."
    }
    while ($true) {
        $runtimeApiKey = Read-SecretConfirmed -Prompt "[MANDATORY] OpenAI Runtime API Key (example: sk-...; Eingabe verborgen)" -Required -Label "OpenAI Runtime API key"
        if (-not [string]::IsNullOrWhiteSpace($runtimeApiKey)) { break }
    }
    $httpProxy = Read-SecretConfirmed -Prompt "[OPTIONAL] Outbound-Proxy fuer den Tunnel, HTTPS_PROXY (example: http://proxy:3128; Enter = keiner)" -Label "HTTPS proxy value"
}

# 3b) public edge (static / oauth / both)
$hasStatic = $AccessArray -contains "static"
$hasOauth = $AccessArray -contains "oauth"
$edgeEnabled = $hasStatic -or $hasOauth

$edgeTls = "ingress"; $edgeCert = ""; $edgeKey = ""; $edgePublicPort = "8443"
$staticPrefix = "ytsk_"; $staticTokens = @()
$oauthIssuer = ""; $oauthResource = ""; $oauthAudience = ""; $oauthScope = ""; $oauthJwks = ""

if ($edgeEnabled) {
    # TLS: OAuth (Claude) requires HTTPS in front of the resource.
    $tlsDefault = if ($hasOauth) { "edge" } else { "ingress" }
    $edgeTls = (Read-Value -Prompt "[OPTIONAL] TLS-Terminierung: edge (Zertifikat im Container) oder ingress (externer Proxy)" -Default $tlsDefault).ToLowerInvariant()
    if ($edgeTls -eq "edge") {
        $edgePublicPort = Read-Value -Prompt "[OPTIONAL] Oeffentlicher HTTPS Port (host)" -Default "8443"
        $edgeCert = Read-Value -Prompt "[MANDATORY] Pfad zum TLS-Zertifikat im Container (example: /certs/tls.crt)" -Required
        $edgeKey = Read-Value -Prompt "[MANDATORY] Pfad zum TLS-Schluessel im Container (example: /certs/tls.key)" -Required
        if ([string]::IsNullOrWhiteSpace($edgeCert) -or [string]::IsNullOrWhiteSpace($edgeKey)) {
            throw "Bei TLS 'edge' muessen Zertifikat UND Schluessel gesetzt sein."
        }
    } elseif ($edgeTls -eq "ingress") {
        $edgeCert = ""; $edgeKey = ""
    } else {
        throw "edge_tls muss 'edge' oder 'ingress' sein."
    }

    if ($hasStatic) {
        $staticPrefix = (Read-Value -Prompt "[OPTIONAL] Reserviertes Static-Token-Praefix" -Default "ytsk_").Trim()
        # at least one static token
        while ($true) {
            $tok = Read-SecretConfirmed -Prompt "[MANDATORY] Static Bearer Token (eines; weitere spaeter)" -Label "Static Bearer token"
            if (-not [string]::IsNullOrWhiteSpace($tok)) { break }
        }
        $staticTokens = @($tok)
        $more = Read-YesNo -Prompt "Weitere static Tokens fuer Rotation hinzufuegen?" -Default $false
        while ($more) {
            $tok = Read-SecretConfirmed -Prompt "[OPTIONAL] weiteres static Token (Enter = fertig)" -Label "Static Bearer token"
            if (-not [string]::IsNullOrWhiteSpace($tok)) { $staticTokens += $tok }
            $more = Read-YesNo -Prompt "Noch ein Token hinzufuegen?" -Default $false
        }
        if ($hasOauth) {
            foreach ($t in $staticTokens) {
                if (-not $t.StartsWith($staticPrefix)) {
                    throw "Static token '$t' beginnt nicht mit dem reservierten Praefix '$staticPrefix'; bei static+OAuth auf einem Edge unzulaessig."
                }
            }
        }
    }

    if ($hasOauth) {
        $oauthIssuer = Read-Value -Prompt "[MANDATORY] OAuth Issuer URL (example: https://as.example.org/realms/master)" -Required
        $oauthResource = Read-Value -Prompt "[MANDATORY] OAuth Resource/Protected-URL (example: https://mcp.example.org/mcp)" -Required
        if ([string]::IsNullOrWhiteSpace($oauthIssuer) -or [string]::IsNullOrWhiteSpace($oauthResource)) {
            throw "Fuer OAuth sind Issuer UND Resource erforderlich."
        }
        $oauthAudience = Read-Value -Prompt "[OPTIONAL] Erwartete Audience (example: account)" -Default ""
        $oauthScope = Read-Value -Prompt "[OPTIONAL] Benoetigter Scope (example: youtube-mcp)" -Default ""
        $oauthJwks = Read-Value -Prompt "[OPTIONAL] JWKS-URL-Override (leer = via Discovery)" -Default ""
    }
}

# 4) assemble canonical JSON and delegate render
$model = [ordered]@{
    mcp = [ordered]@{
        tag = $mcpTag
        youtube_api_key = $youtubeApiKey
        enable_ytdlp = $enableYtDlp
        languages = $languages
        max_chars = $maxCharsText
    }
    access = @($AccessArray)
    tunnel = if ($AccessArray -contains "tunnel") {
        [ordered]@{ tag = $tunnelTag; tunnel_id = $tunnelId; runtime_api_key = $runtimeApiKey; http_proxy = $httpProxy }
    } else { $null }
    edge = [ordered]@{
        enabled = $edgeEnabled
        auth_modes = @((if ($hasStatic) { "static" }), (if ($hasOauth) { "oauth" })) | Where-Object { $_ }
        tls = $edgeTls
        public_port = $edgePublicPort
        cert_file = $edgeCert
        key_file = $edgeKey
        static_token_prefix = $staticPrefix
        static_tokens = @($staticTokens)
        oauth = [ordered]@{
            issuer = $oauthIssuer
            resource = $oauthResource
            audience = $oauthAudience
            required_scope = $oauthScope
            jwks_url = $oauthJwks
            authorization_servers = ""
            scopes_supported = ""
        }
    }
}

$canonicalJson = $model | ConvertTo-Json -Depth 10
$tmpJson = [IO.Path]::GetTempFileName()
[IO.File]::WriteAllText($tmpJson, $canonicalJson, [Text.UTF8Encoding]::new($false))
try {
    $absOut = [IO.Path]::GetFullPath($OutputPath)
    Invoke-CanonicalRenderer -CanonicalJsonPath $tmpJson -OutPath $absOut
}
finally {
    [IO.File]::Delete($tmpJson)
}

Write-Host ""
Write-Host "Stack erfolgreich erzeugt: $([IO.Path]::GetFullPath($OutputPath))"
Write-Warning "Die Datei enthaelt die eingegebenen Secrets im Klartext."
Write-Host "Der MCP-Kern ist immer enthalten und wird NIE oeffentlich publiziert."
