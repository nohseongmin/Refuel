# ⛽ Refuel

> **A fuel gauge for AI coding agents** — see how many tokens you've burned, when your limit refuels, and get told the moment it does.

[![Release](https://img.shields.io/github/v/release/nohseongmin/Refuel?label=release&color=46e08a)](https://github.com/nohseongmin/Refuel/releases/latest)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-5a8dee)](https://github.com/nohseongmin/Refuel/releases/latest)
[![Android](https://img.shields.io/badge/Android-7%2B-46e08a)](https://github.com/nohseongmin/Refuel/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-f5c451)](LICENSE)

You hit the limit, you wait — and then you forget exactly when it comes back. Refuel remembers for you and tells you the moment it's free.

Every day you use it also gets planted on a **contribution-graph style calendar**, so you can see your streak at a glance.

---

## ✨ Features

| | |
|---|---|
| 🌱 **Grass & streaks** | 16 weeks of daily usage, GitHub-style. Days shade from dark to bright in 5 steps depending on how much you used. Shows your current and best streak (PC + phone) |
| ⏳ **Refuel countdown** | Live countdown to the 5-hour rolling window reset, with the exact reset time |
| 📅 **Weekly limit tracking** | If the 5-hour window is free but the weekly limit is the bottleneck, the card switches to a weekly countdown automatically |
| 🔔 **Alerts that matter** | Only two: 5-hour reset and weekly reset. Your phone rings them **even when the PC is off** |
| 🤖 **Agent auto-discovery** | Finds your agent logs by itself — no paths to configure (Claude Code supported, Codex experimental) |
| 📊 **Usage dashboard** | Current window (input / output / cache split) · today · this week · last 7 days |
| 🎯 **Limit estimation** | Learns your limit from your own past usage — nothing to enter |
| 🎨 **One-color theming** | Pick an accent and *everything* follows — alerts, warnings, grass, bars. Your phone picks up the same color automatically |
| 🖥️ **Lives in the tray** | Close to tray, hover for the countdown, right-click to quit. Single instance — launching again just brings the window back |

---

## 📥 Install

### Windows (the collector)

1. Download **`Refuel.exe`** from the [latest release](https://github.com/nohseongmin/Refuel/releases/latest)
2. Double-click it. That's it — no installer, no admin rights.

> Windows SmartScreen may warn you because the binary isn't code-signed (signing certificates cost money). Click **More info → Run anyway**, or [build it yourself](#-build-from-source).

### Android (optional, for phone alerts)

1. Download **`Refuel.apk`** from the same release
2. Allow "install from unknown sources" when prompted
3. Open the app → **Scan QR to connect**
4. On the PC: **⚙ → Pair with QR**, then scan it

### iPhone / anything else

Open <https://nohseongmin.github.io/Refuel/> in Safari and **Add to Home Screen**. The dashboard works as a web app; background push is limited on iOS (see [MANUAL.md](docs/MANUAL.md)).

---

## 🚀 Quick start

```
1. Run Refuel.exe          → it finds your agent logs automatically
2. Use your AI agent       → usage appears within ~20 seconds
3. (optional) ⚙ → Phone sync → Pair with QR → scan on your phone
```

There is nothing else to configure. No API keys, no account, no login.

Full walkthrough: **[INSTALL.md](docs/INSTALL.md)** · Everything else: **[MANUAL.md](docs/MANUAL.md)**

---

## 🔒 Privacy

- **By default Refuel makes no network calls.** Everything stays on your PC.
- It only **reads** your local log files, and only counts **tokens and timestamps** — never your code or prompts.
- Stored in `~/.refuel/` (`config.json`, `history.db`, `refuel.log`).
- **Phone sync is opt-in and off by default.** When you turn it on:
  - What leaves your PC: token counts, timestamps, agent names. Nothing else.
  - The status payload is **end-to-end encrypted (AES-GCM)** — the relay (ntfy.sh) only ever sees ciphertext, and the GCM tag blocks forged status injection.
  - The channel is a **166-bit random secret topic**. The encryption key is passed **only in the QR fragment (`#`)**, which browsers never send to any server.
  - One click regenerates the topic and key if you think it leaked.
- The Android app additionally asks for **camera** (QR scanning only) and **exact alarms** (so reset alerts fire on time).

Full text: [DISCLAIMER.md](DISCLAIMER.md) · [Privacy policy](https://nohseongmin.github.io/Refuel/privacy.html)

---

## 🛠 Build from source

**Windows app:**

```bash
pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Refuel ^
  --collect-all pystray --collect-all PIL --collect-all winotify --collect-all qrcode run.py
```

Or just run it directly:

```bash
python run.py
```

**Android app** (needs JDK 17 + Android SDK):

```bash
cd android-app
npm install
powershell -ExecutionPolicy Bypass -File build-release.ps1
```

Signing paths come from `JAVA_HOME` / `ANDROID_HOME` / `REFUEL_KEYSTORE`, so no machine-specific paths are baked into the repo.

---

## 🧱 How it works

```
~/.claude/projects/**/*.jsonl        ← agent logs (read-only)
        │
        ▼
  refuel/core.py     parse → 5-hour window blocks → limit estimate → grass
        │
        ├── refuel/app.py    Tk tray app (Windows)
        │
        └── refuel/sync.py   AES-GCM encrypt → ntfy relay (opt-in)
                                    │
                                    ▼
                            docs/index.html    phone dashboard (PWA / Capacitor)
```

The 5-hour window is anchored to **the moment you sent the message**, not when the reply finished — otherwise a slow first response pushes the whole reset estimate late.

---

## ❓ FAQ

**Does it show my exact plan limit?**
No, and nothing can — Anthropic doesn't publish a token quota. Refuel estimates your ceiling from the largest completed window it has actually seen on your account, which is why the percentage becomes meaningful only after you've bumped into the limit once.

**Do alerts arrive when my PC is off?**
Yes. Your phone schedules the alert locally the last time it synced, so the PC doesn't need to be awake. Turn on **exact alarms** in the app's diagnostics, and set the battery mode to unrestricted — aggressive power saving on some devices kills scheduled alerts.

**Does it work with agents other than Claude Code?**
Codex support is experimental. Adding another agent is a small entry in the `AGENTS` registry in `refuel/core.py` — PRs welcome.

**Is it on the Play Store?**
No. Install the APK from the releases page.

---

## 📄 License

MIT — see [LICENSE](LICENSE).

Refuel is an unofficial tool and is not affiliated with Anthropic, OpenAI, Cursor, or any other company.
