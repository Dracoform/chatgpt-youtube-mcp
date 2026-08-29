# Geführte Portainer-Stack-Generatoren

Die Generatoren installieren nichts und benötigen auf dem ausführenden Rechner kein Docker. Sie fragen die Konfiguration interaktiv ab und erzeugen eine fertige YAML-Datei zum Einfügen in den Portainer Web Editor.

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
| Registry-Namespace | **Vorübergehend verpflichtend** | keiner | `ghcr.io/my-github-name` | Präfix für beide Container-Images. Nur nötig, solange das Projekt keine fest veröffentlichten Images vorgibt. |
| YouTube-MCP-Image-Version | Optional | `0.1.0` | `0.1.0` | Tag des MCP-Images. |
| Tunnel-Image-Version | Optional | `0.1.0` | `0.1.0` | Tag des Tunnel-Images. |
| YouTube Data API Key | Optional | leer | `AIza...` | Aktiviert offizielle Metadaten-, Channel- und Suchabfragen. Die Eingabe wird verdeckt. |
| yt-dlp-Transcript-Abruf | Optional | `ja` | `ja` | Aktiviert den inoffiziellen Transcript-Fallback. |
| Consent für yt-dlp | Bedingt verpflichtend | keiner | `JA` | Wird nur abgefragt, wenn yt-dlp aktiviert ist. |
| Transcript-Sprachen | Optional | `de,en` | `de,en,fr` | Bevorzugte Untertitelsprachen in Prioritätsreihenfolge. |
| Maximale Transcript-Zeichen | Optional | `60000` | `100000` | Begrenzt die an ChatGPT gelieferte Textmenge; erlaubt sind 1.000 bis 500.000. |
| OpenAI Tunnel-ID | **Verpflichtend** | keiner | `tunnel_0123456789abcdef` | Identifiziert den zuvor in ChatGPT angelegten Tunnel. |
| OpenAI Runtime API Key | **Verpflichtend** | keiner | `sk-...` | Authentifiziert den Tunnel-Client. Die Eingabe wird verdeckt. |
| Bestehende Datei überschreiben | Bedingt | `nein` | `ja` | Erscheint nur, wenn der Ausgabepfad bereits existiert. |

### Warum wird der Registry-Namespace noch abgefragt?

Im aktuellen Prototyp sind die beiden Images noch nicht unter einem festen offiziellen Namen veröffentlicht. Deshalb muss der Generator wissen, aus welchem Registry-Namespace Portainer sie laden soll, zum Beispiel `ghcr.io/my-github-name`.

Für eine fertige Endnutzer-Version ist dieses Feld **nicht mehr nötig**: Sobald feste, geprüfte Image-Namen veröffentlicht sind, werden sie direkt im Generator hinterlegt. Sinnvoll bleibt dann höchstens ein optionaler Experten-Parameter zum Überschreiben der Standard-Images.

## Voraussetzungen

Vor dem Deployment müssen die abgefragten Images tatsächlich in der angegebenen Registry vorhanden sein:

- `<namespace>/youtube-current-data-mcp:<version>`
- `<namespace>/openai-mcp-tunnel:<version>`

Der Tunnel-Container muss diesen Vertrag erfüllen:

| Variable | Bedeutung |
| --- | --- |
| `OPENAI_TUNNEL_ID` | vorhandene OpenAI-Tunnel-ID |
| `CONTROL_PLANE_API_KEY` | Runtime-Key für `tunnel-client` |
| `MCP_SERVER_URL` | interne Streamable-HTTP-Adresse des MCP-Servers |

Das zugehörige Tunnel-Image ist noch nicht Teil von v0.1. Vor einer wirklich einsetzbaren Veröffentlichung müssen dessen Build, Lizenz zur Weiterverteilung des offiziellen `tunnel-client`, Versionspinning und Prüfsummen verifiziert werden. Die Generatoren erfinden deshalb keinen öffentlichen Image-Namen, sondern fragen den Registry-Namespace ab.

## Portainer

1. Portainer öffnen.
2. `Stacks` -> `Add stack`.
3. Einen Stack-Namen vergeben.
4. `Web editor` wählen.
5. Inhalt der erzeugten YAML-Datei einfügen.
6. `Deploy the stack` auswählen.

Der Stack veröffentlicht keinen Host-Port. Der Tunnel erreicht den MCP-Server über das interne Compose-Netzwerk. Beide Container behalten ausgehenden Internetzugriff für OpenAI beziehungsweise YouTube.
