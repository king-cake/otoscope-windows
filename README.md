# Otoscope for Windows

A Windows desktop app for **Soulear (i4season) Wi-Fi otoscopes**, e.g. the Hopefox Find T. It's a companion to the Android app [Otoscope](https://github.com/rbeilvert/otoscope) and speaks the same reverse-engineered protocol. No vendor software needed. The UI is in German.

## Features

- **Kamera verbinden**: lists nearby `Soulear-XXXXX` access points, joins the one you pick, and switches the laptop back to its previous Wi-Fi when you disconnect or close the app.
- Live image in a circle, **auto-rotated** from the camera's accelerometer. Auto-rotation can be switched off.
- **Ganzes Bild** (key K): the whole frame, corners included, with no circle and no rotation. Photos and videos then save it the same way. The Find T sends 480×480 JPEGs (the packet header claims 640×480), so the circle only costs the corners.
- **Licht** (ring light) on/off.
- **Horizontal / vertikal spiegeln**, independently. Only the live view is mirrored, never what gets saved.
- **Drehen um 90°** left/right (arrow keys). Added to the auto-rotation in the circle view; in whole-image mode it's the only rotation. Photos and videos follow it.
- **Foto** (JPEG) and **Video** (H.264 MP4) to `Bilder\Otoscope` and `Videos\Otoscope`. Captures are orientation-corrected and masked to the circle on black, like the Android app.
- **Beschriftung**: a caption (e.g. "Linkes Ohr") burnt into the bottom-left corner of every capture.
- **Vollbild** via F11 or a double-click; Esc leaves it.
- Model, battery and frame-rate readout.

Keyboard shortcuts: **Space** photo, **R** start/stop video, **L** light, **K** whole image / circle, **← / →** turn 90°, **F11** fullscreen, **Esc** leave fullscreen.

## Using it

1. Switch on the otoscope and start `Otoscope.exe`. No installation needed.
2. Pick your `Soulear-…` network and click **Verbinden**.
3. **Firewall, first time only.** The camera answers commands, but Windows silently drops its video: it arrives unsolicited on a UDP port, and Windows doesn't prompt for UDP. After a few seconds without video, the app shows a banner with **„Firewall-Freigabe einrichten"**. One admin confirmation adds a narrow inbound rule named *Otoscope (Kamera-Video)*: this .exe only, UDP only, local subnet only. The rule is tied to the file's path, so after moving the .exe, just click the button again.

While connected, the laptop has no internet: the camera's Wi-Fi is a closed network.

If SmartScreen warns about an unknown app, choose "Weitere Informationen", then "Trotzdem ausführen". The .exe isn't code-signed.

On Windows 11 24H2 and later, the network list stays empty unless desktop apps may use location: *Einstellungen › Datenschutz und Sicherheit › Standort › Desktop-Apps den Zugriff erlauben*. The app offers a button for that page.

Log file: `%LOCALAPPDATA%\OtoscopeWin\otoscope.log`.

## Building

Requires Python 3.13 (other 3.11+ versions should work too).

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

The script creates `.venv`, installs the dependencies, runs the tests and writes `dist\Otoscope.exe`: one self-contained file.

Running from source: `.venv\Scripts\python -m otoscope_win`. Tests: `.venv\Scripts\python -m pytest`.

## Design

| Module | Role |
|---|---|
| `protocol.py` | Control-datagram codec (`0xFFEEFFEE` header, device info, open video, LED) and the accelerometer decode. A port of `I4seasonProtocol.kt`. |
| `assembler.py` | Reassembles the 16- or 28-byte-header UDP JPEG chunks into frames. A port of `FrameAssembler.kt`. |
| `session.py` | Camera connection: a control thread (device info, keepalive via re-sent "open video", battery poll, LED) and a video thread. UI-agnostic, reports through callbacks. |
| `wifi.py` | Windows Native Wifi API via ctypes (`wlanapi.dll`). Scans, joins through a temporary open profile in manual mode, restores the previous connection. Language-independent, unlike parsing `netsh`. |
| `compose.py` | Draws the square crop, rotation, circle mask and caption. Shared by the live view and the captures. |
| `recorder.py` | H.264 MP4 through PyAV's bundled libx264. Frames carry their real capture time because the camera's frame rate drifts. |
| `firewall.py` | Detects and, after one UAC prompt, adds the inbound UDP allow rule for the video stream (elevated PowerShell, `-EncodedCommand`, so no quoting pitfalls). |
| `storage.py` | Folders and `otoscope-YYYYMMDD-HHMMSS-mmm` file names, as on Android. |
| `ui/` | PySide6 (Qt) pages, fullscreen window, and the Qt bridge that decodes JPEGs off the GUI thread. |

The protocol reference is [docs/i4season-protocol.md](docs/i4season-protocol.md), reverse-engineered from the Soulear app. [tools/i4season_probe.py](tools/i4season_probe.py) is the standard-library command-line client the protocol was first verified with: `python tools/i4season_probe.py` streams for 15 s and saves JPEGs. The Android implementation is rbeilvert/otoscope#39.

## License

GPL-3.0-or-later, like the Android app [Otoscope](https://github.com/rbeilvert/otoscope) this is derived from. Not affiliated with the camera vendors or the Soulear app publisher.

Tests run offscreen and don't touch the real Wi-Fi. A fake camera on localhost covers the full flow: device info, streaming, rotation, LED, photo, H.264 video, fullscreen and disconnect.
