# Geführte Portainer-Stack-Generatoren

Die Generatoren installieren nichts und benötigen auf dem ausführenden Rechner kein Docker. Sie fragen die Konfiguration interaktiv ab und erzeugen eine fertige YAML-Datei zum Einfügen in den Portainer Web Editor.

Beide Generatoren (Bash und PowerShell) erzeugen semantisch äquivalente YAML-Dateien mit festen Image-Adressen.

## Bash

```bash
chmod +x generators/generate_docker-compose_for_ChatGPT_MCP.sh
./generators/generate_docker-compose_for_ChatGPT_MCP.sh
```

Optionaler Ausgabepfad:

```bash
./generators/generate_docker-compose_for_ChatGPT_MCP.sh --output mein-stack.yml
```

## PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./generators/generate_docker-compose_for_ChatGPT_MCP.ps1
```

Optionaler Ausgabepfad:

```powershell
./generators/generate_docker-compose_for_ChatGPT_MCP.ps1 -OutputPath mein-stack.yml
```

API-Keys werden bei der Eingabe verdeckt. Die fertige YAML-Datei enthält sie jedoch im Klartext, weil Portainer sie als Container-Umgebungsvariablen übergeben soll. Jeder Portainer-/Docker-Administrator kann solche Werte ohnehin aus der Containerkonfiguration lesen. Behandle die erzeugte Datei trotzdem als Secret.

## Eingabefelder

Jede interaktive Frage ist im Generator als `[MANDATORY]`, `[OPTIONAL]` oder `[CONDITIONAL]` markiert. Bei optionalen Feldern mit angezeigtem Standardwert genügt `Enter`.

| Feld | Status | Standardwert | Beispiel | Zweck |
| --- | --- | --- | --- | --- |
| Ausgabepfad | Optional | `portainer-youtube-mcp-stack.yml` | `mein-stack.yml` | Ziel der erzeugten Compose-Datei; wird als Kommandozeilenoption gesetzt. |
| YouTube-MCP-Image-Version | Optional | `latest` | `latest` | Tag des MCP-Images (`ghcr.io/dracoform/chatgpt-youtube-mcp`). |
| Tunnel-Image-Version | Optional | `0.1.0` | `0.1.0` | Tag des Tunnel-Images (`ghcr.io/dracoform/openai-mcp-tunnel`). |
| YouTube Data API Key | Optional | leer | `AIza...` | Aktiviert offizielle Metadaten-, Channel- und Suchabfragen. Die Eingabe wird verdeckt. |
| yt-dlp-Transcript-Abruf | Optional | `ja` | `ja` | Aktiviert den inoffiziellen Transcript-Fallback. |
| Consent für yt-dlp | Bedingt verpflichtend | keiner | `JA` | Wird nur abgefragt, wenn yt-dlp aktiviert ist. |
| Transcript-Sprachen | Optional | `de,en` | `de,en,fr` | Bevorzugte Untertitelsprachen in Prioritätsreihenfolge. |
| Maximale Transcript-Zeichen | Optional | `60000` | `100000` | Begrenzt die an ChatGPT gelieferte Textmenge; erlaubt sind 1.000 bis 500.000. |
| OpenAI Tunnel-ID | **Verpflichtend** | keiner | `tunnel_0123456789abcdef` | Identifiziert den zuvor in ChatGPT angelegten Tunnel. Landet in der offiziellen Variable `CONTROL_PLANE_TUNNEL_ID`. |
| OpenAI Runtime API Key | **Verpflichtend** | keiner | `sk-...` | Authentifiziert den Tunnel-Client. Die Eingabe wird verdeckt. |
| Outbound-Proxy (HTTPS_PROXY) | Optional | leer | `http://proxy:3128` | Nur wenn das Netzwerk einen ausgehenden Proxy erzwingt. Setzt zusätzlich `NO_PROXY=youtube-mcp,localhost,127.0.0.1`. |
| Bestehende Datei überschreiben | Bedingt | `nein` | `ja` | Erscheint nur, wenn der Ausgabepfad bereits existiert. |

## Feste Image-Adressen

Die Generatoren fragen keinen Registry-Namespace mehr ab. Sie tragen fest ein:

- `ghcr.io/dracoform/chatgpt-youtube-mcp:latest`
- `ghcr.io/dracoform/openai-mcp-tunnel:0.1.0`

Der Registry-Namespace eines GHCR-Images folgt dem GitHub-Konto, nicht dem Repository; beide Images werden aus diesem Projekt heraus gebaut.

## Voraussetzungen

Vor dem Deployment müssen beide Images tatsächlich in GHCR vorhanden sein:

- `ghcr.io/dracoform/chatgpt-youtube-mcp:latest` (veröffentlicht)
- `ghcr.io/dracoform/openai-mcp-tunnel:0.1.0` (**geplant, noch nicht veröffentlicht**; siehe `docs/OPENAI_SECURE_MCP_TUNNEL.md`)

Der Tunnel-Container erfüllt diesen Vertrag:

| Variable | Bedeutung |
| --- | --- |
| `CONTROL_PLANE_TUNNEL_ID` | Offizielle OpenAI-Variable für die Tunnel-ID (`tunnel_` + 32 Kleinbuchstaben-Hexzeichen) |
| `CONTROL_PLANE_API_KEY` | Runtime-Key für `tunnel-client` (nur per Umgebungsvariable, nie in Argumenten oder Logs) |
| `MCP_SERVER_URL` | interne Streamable-HTTP-Adresse des MCP-Servers (`http://youtube-mcp:8765/mcp`) |

Der Wrapper akzeptiert zusätzlich die alte Variable `OPENAI_TUNNEL_ID` als veralteten Kompatibilitäts-Alias und bildet sie auf `CONTROL_PLANE_TUNNEL_ID` ab. Neu erzeugte Stacks verwenden ausschließlich den offiziellen Namen. Der Container validiert alle Werte beim Start und beendet sich mit Exit-Code 78 und einer klaren Fehlermeldung, wenn etwas fehlt oder falsch formatiert ist.

## Security-Einstellungen (beide Services)

- `read_only: true` — nur `/tmp` ist beschreibbar (tmpfs)
- `cap_drop: [ALL]` und `no-new-privileges:true`
- Kein veröffentlichter Host-Port; die Health-Endpunkte des Tunnel-Clients (`/healthz`, `/readyz`) bleiben innerhalb des Containers auf Loopback
- Kein Konfigurations-Volume: der Tunnel-Client benötigt im reinen Umgebungsvariablen-Betrieb keinen persistenten Speicher; ein Volume würde nur unnötig Konfiguration und potenziell Secrets über Stack-Löschungen hinweg aufbewahren
- `stop_grace_period: 30s` für sauberes SIGTERM-Herunterfahren

## Portainer

1. Portainer öffnen.
2. `Stacks` -> `Add stack`.
3. Einen Stack-Namen vergeben.
4. `Web editor` wählen.
5. Inhalt der erzeugten YAML-Datei einfügen.
6. `Deploy the stack` auswählen.

Der Stack veröffentlicht keinen Host-Port. Der Tunnel erreicht den MCP-Server über das interne Compose-Netzwerk. Beide Container behalten ausgehenden Internetzugriff für OpenAI beziehungsweise YouTube.
