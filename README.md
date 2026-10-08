# Refuel

A Windows usage monitor for AI coding agents, with an optional phone dashboard and reset alerts. It reads local agent logs and estimates when usage windows reset.

[Download](https://github.com/nohseongmin/Refuel/releases/latest).

## Features

| Feature | Behavior |
|---|---|
| Usage history | Sixteen weeks of daily usage, with five intensity levels and current or best streaks. |
| Reset countdown | A five-hour window countdown and exact estimated reset time. |
| Weekly limits | Switches to the weekly countdown when that is the active constraint. |
| Phone alerts | Local alerts for five-hour and weekly resets, even after the PC turns off. |
| Log discovery | Finds Claude Code logs automatically; Codex support is experimental. |
| Dashboard | Input, output, and cache counts for the current window, today, week, and last seven days. |
| Limit estimate | Learns a ceiling from recorded usage. |
| Theme | Shared accent color across the PC and phone interfaces. |
| Tray | Close to tray, countdown tooltip, and a single running instance. |

## Installation

### Windows

Download `Refuel.exe` and run it. No installer or administrator rights are needed. The binary is unsigned and may trigger SmartScreen. You can also [build from source](#building).

### Android

Download `Refuel.apk` from the release, allow installation from that source, and open the app. On the PC, open Settings and choose Pair with QR; scan the code on the phone.

### iPhone and other devices

Open [the phone dashboard](https://nohseongmin.github.io/Refuel/) in Safari and add it to the Home Screen. Background alerts on iOS are limited; see [MANUAL.md](docs/MANUAL.md).

Usage normally appears within about 20 seconds of activity. The local monitor needs no API keys, account, or login. Phone sync is optional.

See [INSTALL.md](docs/INSTALL.md) for setup and [MANUAL.md](docs/MANUAL.md) for the full guide.

## Privacy

By default, Refuel makes no network calls. It reads local log files for token counts and timestamps, without collecting code or prompts. Configuration, history, and logs live in `~/.refuel/`.

Opt-in phone sync sends token counts, timestamps, and agent names. Status payloads use AES-GCM encryption, so the ntfy.sh relay receives ciphertext. The channel uses a 166-bit random secret topic, and the encryption key is transferred in the QR URL fragment. The topic and key can be regenerated.

The Android app requests camera access for QR scanning and exact alarms for reset notifications.

See [DISCLAIMER.md](DISCLAIMER.md) and [the privacy policy](https://nohseongmin.github.io/Refuel/privacy.html).

## Building

Windows, from Command Prompt:

```bat
pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Refuel ^
  --collect-all pystray --collect-all PIL --collect-all winotify --collect-all qrcode run.py
```

To run the source directly:

```bash
python run.py
```

Android requires JDK 17 and the Android SDK:

```powershell
cd android-app
npm ci
powershell -ExecutionPolicy Bypass -File build-release.ps1
```

Signing uses `JAVA_HOME`, `ANDROID_HOME`, `REFUEL_KEYSTORE`, and `REFUEL_KEYSTORE_SECRETS`. Passwords are passed through a temporary environment variable. Keep signing files outside Git.

Release PRs build both platforms and check that the APK contains the current dashboard. Matching `vX.Y.Z` tags publish `Refuel.exe` and `SHA256SUMS.txt`. Android CI produces an unsigned artifact; sign it locally and attach `Refuel.apk`, `Refuel.aab`, and checksums to the release.

The tested Capacitor CLI is pinned to 6.2.1. Its transitive `tar` build dependency has high/critical advisories, while runtime dependencies pass `npm audit --omit=dev`. An Android build-system migration is deferred. Use the committed lockfile and platform templates, and do not process untrusted archives with that dependency.

## Implementation

```text
Agent JSONL logs       Read-only input
refuel/core.py         Parse logs, group windows, estimate limits, build history
refuel/app.py          Windows tray interface
refuel/sync.py         Encrypt and relay optional phone sync
docs/index.html       Phone dashboard, PWA, and Capacitor interface
```

The five-hour window is anchored to when a message was sent, so a slow response does not shift the estimate.

The ceiling is inferred from the largest completed usage window observed on the account; it is not an exact plan quota. The estimate becomes more useful after a limit has been reached.

Phones schedule alerts locally using their last sync. On Android, exact alarms and unrestricted battery mode may be needed. The app is distributed through GitHub releases rather than the Play Store.

## License

[MIT](LICENSE). Refuel is an unofficial tool and is not affiliated with Anthropic, OpenAI, Cursor, or other companies.
