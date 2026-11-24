# SLP Label Studio

Etiketten mit dem **SII/Seiko Smart Label Printer 100** (SLP100) drucken: Treiber, Weboberfläche
und MCP-Server in einem Docker-Container.

*Docker container with a user-space USB driver, a web label editor and an MCP server for the
discontinued SII Smart Label Printer 100. No vendor driver or CUPS required.*

- **Treiber im Container:** spricht den Drucker direkt über USB an, ohne CUPS und ohne Herstellertreiber
- **Weboberfläche:** Text, über 700 Icons mit Suche, 23 Rahmenstile, Bilder, QR-Codes, Vorlagen, Live-Vorschau Punkt für Punkt
- **MCP-Server:** KI-Assistenten wie Claude können Etiketten entwerfen, ansehen und drucken

> Geprüft mit einem SLP100 an einer Debian-13-VM unter Proxmox (amd64). Andere Modelle der SLP-Reihe
> werden nicht unterstützt, arm64 ist gebaut, aber nicht mit Drucker getestet.

## Voraussetzungen

- Linux-Host (amd64 oder arm64) mit Docker und Docker Compose
- SLP100 am USB-Anschluss (`lsusb` zeigt `0619:0103`)

Unter Proxmox den Drucker zuerst an die VM durchreichen: `qm set <VMID> -usb0 host=0619:0103`

## Start

Es reicht die Compose-Datei, der Quellcode wird nicht gebraucht:

```sh
mkdir slp-label-studio && cd slp-label-studio
curl -fLO https://raw.githubusercontent.com/soma-web/SLP100-Label-Printer/main/compose.yaml
echo 'SLP_PASSWORD=hier-ein-passwort' > .env
docker compose up -d
```

Danach `http://<host>:8100` öffnen. Benutzername ist `admin`, sofern nicht anders gesetzt.

Läuft auf dem Host das Kernelmodul `usblp`, löst der Container den Drucker davon, sobald er zum
ersten Mal darauf zugreift. `/dev/usb/lp0` verschwindet dann bis zum nächsten Anstecken. Den Drucker
deshalb nicht zusätzlich in CUPS einrichten.

## Einstellungen

Alle Variablen stehen in [.env.example](.env.example).

| Variable | Bedeutung |
|---|---|
| `SLP_PASSWORD` | Passwort der Weboberfläche. Leer = keine Anmeldung |
| `SLP_USERNAME` | Benutzername, Standard `admin` |
| `SLP_PUBLISH` | Port auf dem Host, Standard `8100` |
| `SLP_ALLOWED_HOSTS` | Eigene Domainnamen, wenn kein Passwort gesetzt ist |
| `SLP_MCP` | MCP beim ersten Start aktivieren (`1`) |
| `SLP_MCP_TOKEN` | Festes MCP-Token, sonst wird eins erzeugt |
| `SLP_BACKEND` | `auto`, `usbfs`, `usblp` oder `mock` (Simulator ohne Drucker) |
| `LP_GID` | Gruppe des USB-Druckers auf dem Host, Standard `7` |

Vorlagen, Icons und Einstellungen liegen im Volume `data`.

Aktualisieren: `docker compose pull && docker compose up -d`

## MCP-Server

In der Weboberfläche unter **Einstellungen → MCP-Server** aktivieren. Dort steht der fertige Befehl:

```sh
claude mcp add --transport http etiketten http://<host>:8100/mcp --header "Authorization: Bearer <token>"
```

Werkzeuge: Druckerstatus, Vorlagen und Icons auflisten, Vorschau, Drucken, Vorlage speichern.

## Sicherheit

Die Verbindung ist unverschlüsseltes HTTP. Nicht direkt ins Internet stellen, sondern nur im eigenen
Netz oder hinter einem HTTPS-Reverse-Proxy betreiben. Ohne `SLP_PASSWORD` kann jeder im Netz drucken,
Vorlagen ändern und das MCP-Token sehen.

Der Container läuft ohne Root-Rechte und ohne `privileged`, bekommt aber `/dev/bus/usb` eingebunden.
Schreiben kann er nur auf USB-Geräte, die der Gruppe `LP_GID` gehören oder für alle freigegeben sind.

## Probleme

| Meldung | Lösung |
|---|---|
| Drucker nicht gefunden | Eingeschaltet? Zeigt `lsusb` auf dem Host `0619:0103`? |
| Kein Zugriff auf den Drucker | `ls -ln /dev/bus/usb/*/*` ansehen und `LP_GID` anpassen |
| USB-Verbindung abgebrochen | Verlängerungskabel weglassen, anderen Anschluss und das Netzteil prüfen. Bei uns brach der Druck mit Verlängerung beim Druckstart ab und lief direkt angesteckt durch |
| Druck läuft auf das nächste Etikett über oder ein leeres Etikett kommt mit | Unter **Einstellungen → Etikettenlänge kalibrieren** das Lineal drucken und die Werte eintragen |
| Der Drucker meldet eine Störung | Klappe öffnen, Etikettenstau beseitigen |
| Wird von einem anderen Programm benutzt | Drucker in CUPS o. Ä. entfernen |

## Entwicklung

```sh
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/python dev_mock.py     # http://127.0.0.1:8111 mit simuliertem Drucker
```

Iconset ändern: `tools/icons.txt` bearbeiten und `tools/build_icons.py` ausführen.

Image selbst bauen: `docker compose -f compose.yaml -f compose.build.yaml up -d --build`

## Lizenz

Apache License 2.0. Die Druckbefehle stammen aus [LPrint](https://github.com/michaelrsweet/lprint),
das Iconset aus [Material Design Icons](https://pictogrammers.com/library/mdi/),
beide Apache License 2.0, siehe [NOTICE](NOTICE). Das Projekt gehört nicht zu Seiko Instruments.
