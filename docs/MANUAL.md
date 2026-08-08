# Manual

Everything Refuel does, and what each number actually means.

---

## Reading the card

Each detected agent gets one card. Collapsed it is a single line; click it to expand.

```
▾ ● Claude Code   13d streak                    02:41:55
  ─────────────────────────────────────────────────────
  02:41:55                        ← time until the 5-hour window resets
  13 day streak · best 14
  ▓▓▒▒░░▓▓▒▒░░▓▓▒▒  ← 16 weeks of daily usage
  Less ▪▪▪▪▪ More · brighter = closer to the limit
  5h reset 02:45 · window 11,194,035 tokens · est. limit 14%
  Today 7,047,477 · in 53 · out 41K · cache 11.2M
  Weekly 193,698,741 tokens · resets Tue 11:00 (D-2) · est. 48%
  ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░
  Last 7 days
  Today    ▓▓▓░░░░░░░░░  7.0M
```

### The big number

Time until your **5-hour rolling window** resets.

- Normal colour — you have room
- Brighter — reset is within 30 minutes
- Brightest — you're past 80% of the estimated limit, or the weekly limit is reached

When there is no active window it reads **Refueled** — nothing is being consumed and you can start fresh.

### The 5-hour window

Claude subscriptions use a rolling 5-hour session window. The window starts **the moment you send your first message**, not when the reply finishes — Refuel anchors on your message timestamp, because anchoring on the reply pushes the estimated reset later the slower the model is.

### Weekly

If the 5-hour window is free but you've exhausted the weekly limit, the card automatically switches to a weekly countdown and says so.

Set when your week rolls over in ⚙ → **Weekly reset** (day + hour).

### "est. limit" — what it really is

Providers do not publish token quotas, so Refuel derives the ceiling from **the largest completed window it has actually observed on your account**.

That means:

- Before you've ever hit the limit, the percentage is really "how close to your own record", not "how close to the cap".
- Once you *have* hit the cap a few times, the observed maximum converges on the real limit and the percentage becomes meaningful.
- It's per-account and per-model-mix, which is arguably more useful than a published number would be.

Treat it as a trend indicator, not a billing figure.

---

## Grass and streaks

The calendar is 16 weeks (112 days), one square per day, laid out like a GitHub contribution graph — columns are weeks, rows are weekdays, today is the last square.

| Shade | Meaning |
|---|---|
| Empty | You didn't use the agent that day |
| Dark → bright, 5 steps | How much you used, as a share of the estimated 5-hour limit |
| Brightest | You used the full estimated limit or more that day |

**Streak** = consecutive days with any usage. If today is still empty, the streak is counted up to yesterday, so it doesn't look broken halfway through the day.

History is kept in SQLite (`~/.refuel/history.db`). Refuel stores every day it finds in your logs, so grass fills in retroactively the first time you run it — and stays filled even if the agent's own logs get cleaned up later.

---

## Alerts

Only two, deliberately:

| Alert | When |
|---|---|
| **`<agent>` refueled** | The 5-hour window reset |
| **`<agent>` weekly reset** | The weekly limit reset |

Earlier versions also sent "limit approaching", "80% used" and burn-rate predictions. In daily use that was noise — the numbers are on screen whenever you want them, so alerts are reserved for "you can use it again".

### How phone alerts survive a sleeping PC

Two independent paths:

1. **The phone schedules it locally.** Every time it syncs, it registers a local notification for the next reset time. The PC does not need to be awake — the phone rings it itself.
2. **The PC pre-schedules a push.** Refuel asks the relay to deliver a push at the reset time, which also arrives with the PC off. This one only shows up if you subscribed to the topic in the ntfy app.

The app deliberately ignores the relayed reset push so you don't get the same alert twice.

Live alerts from the PC (test alert, update notice) reach the phone app within 30 seconds — but only while the app is running. Instant push to a closed app would need Firebase, which Refuel doesn't use.

---

## Theming

⚙ → **Accent color**, six choices.

Everything derives from that one colour: the hue stays fixed and meaning is carried by brightness, so warnings, danger, grass and bars all become shades of your colour. Picking blue makes the whole app blue.

Brighter always means "stronger signal" — normal < reset imminent < limit reached.

Backgrounds, body text and the app icon stay neutral on purpose.

Your phone picks up the same colour automatically, usually within 30 seconds. Both sides compute the shades from the same formula, so they always match exactly.

---

## Settings (PC ⚙)

| Setting | What it does |
|---|---|
| **Weekly reset** | Day and hour your weekly limit rolls over |
| **Close to tray** | Closing the window hides it instead of quitting (right-click the tray icon to actually quit) |
| **Start with Windows** | Launches quietly into the tray on boot |
| **Check for updates** | Reads the GitHub releases API once a day. Read-only, no account, can be turned off |
| **Phone sync (beta)** | Turns on the encrypted relay. Off by default |
| **Pair with QR** | Shows the pairing QR and the ntfy topic; can regenerate topic + key |
| **Accent color** | Themes the entire app |
| **Test alert** | Fires a notification right now (also reaches the phone if sync is on and the app is open) |

Launching Refuel a second time doesn't start another copy — it brings the existing window to the front.

---

## Settings (phone ⚙)

| Section | What's in it |
|---|---|
| **Connection** | Pairing status, scan again, disconnect |
| **Alerts** | Receive alerts / sound / vibration, plus an instant test |
| **Alert diagnostics** | Notification permission, exact-alarm permission (with a fix button), the list of scheduled alerts, and the 1-minute closed-app test |
| **Diagnostic log** | Last 300 events, shareable. Never contains your topic or key |
| **Advanced** | The ntfy topic, if you want to subscribe in the ntfy app too |

---

## Files and locations

| Path | What |
|---|---|
| `~/.refuel/config.json` | Settings, including the sync topic and key |
| `~/.refuel/history.db` | Daily usage history (SQLite) |
| `~/.refuel/refuel.log` | Diagnostic log |
| `~/.claude/projects/**/*.jsonl` | Source logs — **read only**, never modified |

`config.json` holds your sync key. Anyone with it can read your status payloads, so don't paste it into an issue. If it leaks, regenerate from ⚙ → Pair with QR → Regenerate topic & key.

---

## Supported agents

| Agent | Status | Log location |
|---|---|---|
| Claude Code | Supported | `~/.claude/projects/**/*.jsonl` (or `CLAUDE_CONFIG_DIR`) |
| Codex CLI | Experimental, unverified | `~/.codex/sessions/**/*.jsonl` (or `CODEX_HOME`) |

Adding another agent means one entry in the `AGENTS` registry in `refuel/core.py`: where the logs live, a glob, and a parser that returns events. PRs welcome.

---

## Limits and known constraints

- **Windows only** for the collector. The parsing core is pure stdlib and platform-independent; the GUI, tray and toasts are Windows-specific.
- **No exact quota.** See "est. limit" above.
- **Instant push to a closed phone app** requires Firebase; Refuel doesn't use it. Reset alerts are unaffected because the phone schedules those locally.
- **Aggressive battery savers** on some Android devices can still kill scheduled alarms. Unrestricted battery mode is the fix.
- **iOS** has no native build; the PWA works but background push is limited.
