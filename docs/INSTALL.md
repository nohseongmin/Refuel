# Installation guide

Two pieces: the **Windows collector** (required) and the **phone app** (optional, for alerts when your PC is off).

---

## 1. Windows

### Download and run

1. Go to the [latest release](https://github.com/nohseongmin/Refuel/releases/latest)
2. Download **`Refuel.exe`**
3. Double-click it

There is no installer and no admin prompt. The app appears in your system tray.

### "Windows protected your PC"

SmartScreen shows this for any binary without a code-signing certificate (they cost several hundred dollars a year, and this is a free project).

**Click `More info` → `Run anyway`.**

If you'd rather not trust a binary, [build it yourself](#building-from-source) — it's three commands.

### First launch

1. A terms dialog appears. Read it, tick the box, click **Agree & start**.
2. Refuel searches for agent logs automatically:
   - `%USERPROFILE%\.claude\projects\` (Claude Code)
   - `%USERPROFILE%\.codex\sessions\` (Codex, experimental)
   - Also honours the `CLAUDE_CONFIG_DIR` and `CODEX_HOME` environment variables
3. Use your AI agent for a bit. Usage shows up within about 20 seconds.

If nothing appears, see [Troubleshooting](#troubleshooting).

### Start with Windows (recommended)

**⚙ → Start with Windows** — Refuel launches quietly into the tray on boot, so your streak and history keep filling in without you thinking about it.

---

## 2. Android

The phone app exists for one reason: **alerts that arrive when your PC is asleep or off.**

### Install

1. Download **`Refuel.apk`** from the [latest release](https://github.com/nohseongmin/Refuel/releases/latest)
2. Open it on your phone. Android asks permission to install from this source — allow it.
3. Open Refuel.

> Not on the Play Store. Sideloading the APK is the supported route.

### Pair with your PC

1. **On the PC:** ⚙ → **Pair with QR** (this also switches Phone sync on)
2. **On the phone:** **📷 Scan QR to connect**
3. Point the camera at the PC screen

Done. The dashboard fills in within 30 seconds.

The QR encodes a random secret topic and an encryption key. The key travels in the URL fragment (`#`), which browsers never transmit to any server.

### Make alerts reliable (do this once)

Android will happily throttle scheduled alarms into oblivion. Two settings fix it:

1. **In the app:** ⚙ → **Alert diagnostics**
   - If *Exact alarms* says **off**, tap **Allow exact alarms** and grant it.
     Without this, Android 12+ silently downgrades your reset alert to an inexact alarm that can fire tens of minutes late.
2. **In Android settings:** Settings → Apps → Refuel → Battery → **Unrestricted**
   Samsung, Xiaomi, OPPO and others kill scheduled alerts under their own "optimisation".

### Verify in one minute (instead of waiting five hours)

⚙ → **Schedule an alert in 1 minute** → **close the app completely** → wait.

It is scheduled through exactly the same path as a real reset alert, so if it arrives, reset alerts will arrive too.

---

## 3. iPhone / other platforms

There is no native iOS build (Apple requires a paid developer account). The dashboard works as a web app:

1. Open <https://nohseongmin.github.io/Refuel/> in Safari
2. Share → **Add to Home Screen**
3. On the PC: ⚙ → Pair with QR, then open the link from the QR on your phone

You get the full dashboard, grass and streaks. Background push is restricted on iOS, so treat it as a viewer rather than an alarm.

---

## Building from source

### Windows app

Requires Python 3.10+.

```bash
git clone https://github.com/nohseongmin/Refuel.git
cd Refuel
pip install -r requirements.txt
python run.py
```

To produce the same single-file exe as the release:

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Refuel ^
  --collect-all pystray --collect-all PIL --collect-all winotify --collect-all qrcode run.py
```

Output: `dist\Refuel.exe`

### Android app

Requires JDK 17, Android SDK (build-tools 34+), Node 20+, and your own signing keystore.

```bash
cd android-app
npm install
powershell -ExecutionPolicy Bypass -File build-release.ps1
```

Paths are read from `JAVA_HOME`, `ANDROID_HOME`, `REFUEL_KEYSTORE` and `REFUEL_KEYSTORE_SECRETS`, with auto-detection as a fallback. The script bundles `docs/` into the app, runs `cap copy`, builds, signs, and then **verifies the packaged APK actually contains the latest code** before declaring success.

---

## Troubleshooting

### No agents detected

- Have you actually used the agent on this machine? Refuel reads logs; with no logs there is nothing to show.
- Check the folder exists: `%USERPROFILE%\.claude\projects`
- Using a custom location? Set `CLAUDE_CONFIG_DIR` and restart Refuel.
- Look at `%USERPROFILE%\.refuel\refuel.log` for parse errors.

### Windows notifications don't appear

- Windows Settings → System → Notifications — make sure they're on, and that **Focus assist / Do not disturb** is off.
- Test with ⚙ → **Test alert**.
- If the toast fails, Refuel falls back to a tray balloon; `refuel.log` records which channel was used.

### Phone shows "Waiting for data"

- Is Refuel running on the PC, and is **Phone sync** on in ⚙?
- Is the phone online? The dashboard polls the relay every 30 seconds.
- Re-pair: ⚙ → Scan again on the phone, ⚙ → Pair with QR on the PC.
- Check ⚙ → Diagnostic log on the phone; "undecryptable message" means the key no longer matches and you should re-pair.

### Phone alerts don't arrive

Work through these in order:

1. ⚙ → Alert diagnostics → is *Notifications* allowed?
2. Is *Exact alarms* allowed? If not, tap the button.
3. Battery mode set to **Unrestricted**?
4. Run the 1-minute scheduled test with the app closed.
5. Still nothing? Open an issue and attach the diagnostic log (⚙ → Diagnostic log → Share). It never contains your topic or key.

### The exe won't overwrite when rebuilding

Refuel is still running. Quit it from the tray (right-click → Quit) and build again.

---

## Uninstall

- Delete `Refuel.exe`
- Delete `%USERPROFILE%\.refuel\` (config, history, log)
- If you enabled autostart, turn it off first (⚙ → Start with Windows), or remove the `Refuel` entry from `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- On the phone, uninstall the app as usual

Nothing else is written to your system.
