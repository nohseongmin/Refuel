"""Refuel GUI. A dark tray app showing one accordion card per agent.

Collapsed a card is a single line, expanded it shows detail. Scanning runs on a
background thread so the UI never blocks, while alerts are always shown from the
main thread in _tick.
"""
import os
import sys
import threading
import time
import logging
import queue
import json
import re
import colorsys
import urllib.request
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timezone

from . import core, sync, __version__

log = logging.getLogger("refuel")

_RELEASES_API = "https://api.github.com/repos/nohseongmin/Refuel/releases/latest"
_RELEASES_URL = "github.com/nohseongmin/Refuel/releases"


def _parse_ver(v):
    try:
        return tuple(int(x) for x in re.sub(r"[^0-9.]", "", str(v)).split(".") if x)
    except Exception:
        return ()


def _latest_release_tag():
    """Read the latest GitHub release tag. Returns None on failure."""
    req = urllib.request.Request(_RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Refuel"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r).get("tag_name")

# ---------------- Palette ----------------
BG = "#0d0f14"
PANEL = "#141821"
BORDER = "#252c3a"
TRACK = "#0a0c11"
TX = "#e7eaf0"
MUT = "#8a93a4"

# ---------------- Theme ----------------
# Every colour is derived from a single accent. The hue stays fixed and meaning is
# carried by lightness and saturation, so picking blue turns warnings, danger and the
# grass blue too. Backgrounds, body text and the app icon stay neutral.
ACCENT_HUES = [150, 205, 45, 5, 275, 320]   # green, blue, amber, red, purple, pink
_ACCENT_L, _ACCENT_S = 0.58, 0.70           # equal lightness keeps derived shades consistent across swatches


def _shade(hue, light, sat):
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _hue_of(hex_color):
    try:
        h = hex_color.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        return colorsys.rgb_to_hls(*rgb)[0]
    except Exception:
        return ACCENT_HUES[0] / 360.0


SWATCHES = [_shade(h / 360.0, _ACCENT_L, _ACCENT_S) for h in ACCENT_HUES]
_theme_cache = {}


def theme():
    """Colours derived from the current accent. Brighter means a stronger signal:
    normal < reset imminent < limit reached."""
    acc = core.CONFIG["accent"]
    t = _theme_cache.get(acc)
    if t is None:
        hue = _hue_of(acc)
        t = {
            "acc": acc,
            "warn": _shade(hue, 0.70, 0.90),    # reset imminent
            "dng": _shade(hue, 0.82, 1.00),     # limit nearly or fully used
            "bar": _shade(hue, 0.45, 0.60),     # daily bars
            # grass: unused day blends into the background, heavy day reaches the accent
            "grass": [_shade(hue, 0.11, 0.15)] +
                     [_shade(hue, 0.22 + i * 0.10, 0.40 + i * 0.12) for i in range(5)],
        }
        _theme_cache[acc] = t
    return t

F = "Malgun Gothic"   # replaced by auto-detection in __init__
REFRESH_SECONDS = 20
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_mutex_handle = None  # keeps the single-instance mutex handle alive
_WAKE_EVENT = "Refuel_ShowWindow_Event"   # second launch signals the running instance to show its window

# ---------------- Optional dependencies ----------------
try:
    from winotify import Notification, audio
    _HAVE_TOAST = True
except Exception:
    _HAVE_TOAST = False

try:
    import pystray
    from PIL import Image, ImageDraw
    _HAVE_TRAY = True
except Exception:
    _HAVE_TRAY = False

try:
    import winreg
    _HAVE_REG = True
except Exception:
    _HAVE_REG = False

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "Refuel"


def _enable_dpi():
    """Keeps text crisp on high-DPI screens. Must run before Tk is created."""
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _single_instance():
    """Prevents a second copy from running, using a named mutex rather than a port.
    Returns False if another instance already holds it."""
    global _mutex_handle
    try:
        from ctypes import windll
        _mutex_handle = windll.kernel32.CreateMutexW(None, False, "Refuel_SingleInstance_Mutex")
        return windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _wake_running_instance():
    """Tell the running instance to show its window. True if the signal was sent.

    Popping an "already running" notification would still leave the user hunting for
    the tray icon. Bringing the existing window forward is what they actually wanted.
    """
    try:
        from ctypes import windll
        h = windll.kernel32.OpenEventW(0x0002, False, _WAKE_EVENT)  # EVENT_MODIFY_STATE
        if not h:
            return False
        windll.kernel32.SetEvent(h)
        windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def _pick_font(root):
    try:
        fams = set(tkfont.families(root))
    except Exception:
        return "Malgun Gothic"
    for p in ("D2Coding", "NanumGothicCoding", "Nanum Gothic Coding",
              "Sarasa Mono K", "Malgun Gothic", "\ub9d1\uc740 \uace0\ub515", "Consolas"):   # "\ub9d1\uc740 \uace0\ub515" is the Korean name of Malgun Gothic; Consolas is the final fallback
        if p in fams:
            return p
    return "Malgun Gothic"


_APP = None  # current app instance, used for the tray fallback
_notify_q = queue.Queue()  # hands alerts from worker threads to the main thread


def _deliver(title, msg):
    """Show an alert on screen. Must be called from the Tk main thread.

    A self-drawn toast is not used because it never renders while the app sits in the
    tray with the root window withdrawn.
    """
    channel = None
    if _HAVE_TOAST:                       # first choice: native Windows toast
        try:
            n = Notification(app_id="Refuel", title=title, msg=msg)
            n.set_audio(audio.Default, loop=False)
            n.show()
            channel = "winotify"
        except Exception as e:
            log.warning("winotify failed: %s", e)
    if channel is None:                   # fallback: tray balloon
        tray = getattr(_APP, "tray", None)
        if tray is not None:
            try:
                tray.remove_notification()
                tray.notify(msg, title)
                channel = "tray"
            except Exception as e:
                log.warning("tray notification failed: %s", e)
    log.info("alert shown: %s [%s]", title, channel or "failed-not-shown")


def _drain_notifications():
    """Show queued alerts. Called only from the Tk main thread via _tick."""
    while True:
        try:
            title, msg = _notify_q.get_nowait()
        except queue.Empty:
            break
        _deliver(title, msg)


def _notify(title, msg, phone=True):
    """Safe from any thread. Display is queued for the main thread to pick up in _tick,
    because showing it directly from a worker makes the balloon silently fail.
    phone=False keeps it on the PC, so a pre-scheduled phone push is not duplicated."""
    log.info("alert: %s - %s", title, msg)
    if phone:
        try:
            sync.post_alert(title, msg)
        except Exception as e:
            log.warning("phone alert failed: %s", e)
    if getattr(_APP, "root", None) is not None:
        _notify_q.put((title, msg))     # _tick picks it up on the main thread
    else:
        _deliver(title, msg)            # before the app or main loop exists


def _set_autostart(enable):
    if not _HAVE_REG:
        return
    try:
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --minimized'
        else:
            script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "run.py"))
            cmd = f'"{sys.executable}" "{script}" --minimized'
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, _APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        log.warning("autostart setting failed: %s", e)


def _fmt_n(v):
    return f"{int(v or 0):,}"


def _fmt_short(v):
    v = int(v or 0)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def _fmt_dur(sec):
    """HH:MM:SS under a day, otherwise 'Nd HH:MM:SS'."""
    if sec is None:
        return "--:--:--"
    sec = max(0, int(sec))
    days, rem = divmod(sec, 86400)
    base = f"{rem // 3600:02d}:{rem % 3600 // 60:02d}:{rem % 60:02d}"
    return f"{days}d {base}" if days else base


# ---------------- Widget helpers, to avoid repeating theme options ----------------
def _font(size, bold=False):
    return (F, size, "bold") if bold else (F, size)


def _lbl(parent, text, *, fg=MUT, size=9, bold=False, bg=PANEL, **kw):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=_font(size, bold), **kw)


def _btn(parent, text, cmd, *, fg=TX, bg=PANEL, size=9, bold=False, cursor="hand2", **kw):
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, font=_font(size, bold),
                     bd=0, relief="flat", activebackground=BORDER, activeforeground=TX,
                     cursor=cursor, **kw)


def _chk(parent, text, var, cmd=None):
    return tk.Checkbutton(parent, text=text, variable=var, command=cmd, bg=BG, fg=TX,
                          font=_font(9), selectcolor=PANEL, activebackground=BG,
                          activeforeground=TX, bd=0, highlightthickness=0)


def _readonly(parent, value, size=9):
    """A copy-only field, used for the pairing URL and topic."""
    e = tk.Entry(parent, bg=PANEL, fg=MUT, relief="flat", font=_font(size),
                 highlightbackground=BORDER, highlightthickness=1)
    e.insert(0, value)
    e.config(state="readonly")
    return e


def _dialog(parent, title, padx=20, pady=18):
    win = tk.Toplevel(parent, bg=BG)
    win.title(title)
    win.configure(padx=padx, pady=pady)
    return win


class AgentCard:
    """One card per agent. Collapsed shows a header line, expanded shows detail."""

    def __init__(self, app, parent, aid, name):
        self.app, self.id, self.name = app, aid, name
        self.reset_at = None
        self.usage_ratio = None
        self.has_block = False
        self.primary = "full"          # "5h" | "weekly" | "full"
        self.wk_reset = None
        self.wk_ratio = None
        self.wk_rem = None

        self.outer = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        self.outer.pack(fill="x", pady=(0, 10))

        h = tk.Frame(self.outer, bg=PANEL, cursor="hand2")
        h.pack(fill="x")
        self.chev = _lbl(h, "▸", size=10, width=2)
        self.chev.pack(side="left", padx=(8, 0), pady=9)
        self.dot = _lbl(h, "●", fg=app.accent(), size=10)
        self.dot.pack(side="left")
        self.name_lbl = _lbl(h, name, fg=TX, size=11, bold=True)
        self.name_lbl.pack(side="left", padx=6)
        self.hstreak = _lbl(h, "", size=9)      # streak stays visible while collapsed
        self.hstreak.pack(side="left")
        self.hcount = _lbl(h, "--:--:--", size=11)
        self.hcount.pack(side="right", padx=14)
        for wdg in (h, self.chev, self.dot, self.name_lbl, self.hstreak, self.hcount):
            wdg.bind("<Button-1>", lambda e: self.app.toggle(self.id))

        self.hbar = tk.Canvas(self.outer, height=4, bg=TRACK, highlightthickness=0)
        self.hbar.pack(fill="x", padx=12, pady=(0, 10))

        self.detail = tk.Frame(self.outer, bg=PANEL)
        self._build_detail()

    def _build_detail(self):
        d = self.detail
        self.count = _lbl(d, "--:--:--", fg=TX, size=38, bold=True)
        self.count.pack(anchor="w", padx=16, pady=(2, 6))

        # The grass is the centrepiece: directly under the countdown, full card width.
        self.streak_lbl = _lbl(d, "", fg=TX, size=10, bold=True)
        self.streak_lbl.pack(anchor="w", padx=16, pady=(0, 4))
        self._streak_data = None
        self.grass = tk.Canvas(d, height=80, bg=PANEL, highlightthickness=0)
        self.grass.pack(fill="x", padx=16, pady=(0, 4))
        self.grass.bind("<Configure>", lambda e: self._render_grass(self._streak_data))
        legend = tk.Frame(d, bg=PANEL)
        legend.pack(anchor="w", padx=16, pady=(0, 12))
        _lbl(legend, "Less", size=8).pack(side="left", padx=(0, 4))
        for c in theme()["grass"][1:]:
            tk.Canvas(legend, width=9, height=9, bg=c,
                      highlightthickness=0).pack(side="left", padx=1)
        _lbl(legend, "More · brighter = closer to the limit", size=8).pack(side="left", padx=(4, 0))

        self.sub = _lbl(d, "", size=10)
        self.sub.pack(anchor="w", padx=16, pady=(0, 2))
        self.bd = _lbl(d, "")
        self.bd.pack(anchor="w", padx=16, pady=(0, 2))
        self.wk = _lbl(d, "")
        self.wk.pack(anchor="w", padx=16, pady=(0, 4))
        self.bar = tk.Canvas(d, height=8, bg=TRACK, highlightthickness=0)
        self.bar.pack(fill="x", padx=16, pady=(2, 12))

        _lbl(d, "Last 7 days").pack(anchor="w", padx=16, pady=(0, 4))
        self.daily = tk.Frame(d, bg=PANEL)
        self.daily.pack(fill="x", padx=16, pady=(0, 12))

    def set_expanded(self, val):
        self.chev.config(text="▾" if val else "▸")
        if val:
            self.detail.pack(fill="x")
        else:
            self.detail.pack_forget()

    def update(self, a):
        b = a["block"]
        self.has_block = b is not None
        wk = a.get("weekly") or {}
        self.wk_reset = wk.get("reset_at")
        self.wk_ratio = wk.get("ratio")
        self.wk_rem = wk.get("remaining_sec")
        wk_over = self.wk_ratio is not None and self.wk_ratio >= core.CONFIG["warn_ratio"]

        if b:
            self.primary = "5h"
            self.reset_at = b["reset_at"]
            self.usage_ratio = b["ratio"]
            extra = f" · est. limit {int(b['ratio'] * 100)}%" if b["ratio"] is not None else ""
            self.sub.config(text=f"5h reset {b['reset_at'].strftime('%H:%M')} · window {_fmt_n(b['tokens'])} tokens{extra}")
            self.bd.config(text=f"Today {_fmt_n(a['today_tokens'])} · in {_fmt_short(b['inp'])} · out {_fmt_short(b['out'])} · cache {_fmt_short(b['cache'])}")
        else:
            self.reset_at = None
            self.usage_ratio = None
            self.bd.config(text=f"Today {_fmt_n(a['today_tokens'])}")
            if wk_over:
                self.primary = "weekly"
                self.sub.config(text="Weekly limit reached - waiting for reset")
            else:
                self.primary = "full"
                self.sub.config(text="No active window · weekly has room")
        if self.wk_reset:
            days = (self.wk_rem or 0) // 86400
            when = f"resets {_WD[self.wk_reset.weekday()]} {self.wk_reset.strftime('%H:%M')}"
            rtxt = f" · est. {int(self.wk_ratio * 100)}%" if self.wk_ratio is not None else ""
            self.wk.config(text=f"Weekly {_fmt_n(wk.get('tokens'))} tokens · {when} (D-{days}){rtxt}")
        st = a.get("streak") or {}
        cur, best = st.get("current", 0), st.get("best", 0)
        # Tk renders emoji as flat monochrome glyphs, so the desktop uses text only.
        # The phone keeps the emoji.
        self.hstreak.config(text=f"{cur}d streak" if cur else "", fg=self.app.accent())
        self.streak_lbl.config(
            text=f"{cur} day streak · best {best}" if cur else f"No active streak · best {best}",
            fg=self.app.accent() if cur else MUT)
        self._render_grass(st)
        self._render_daily(a.get("daily", []))

    def _render_grass(self, streak):
        """A GitHub-style contribution calendar. Columns are weeks, rows are weekdays
        from Monday, and today is the final square.

        Cell size is derived from the canvas width so the grid always fills it, and it
        is redrawn whenever the window is resized.
        """
        self._streak_data = streak
        self.grass.delete("all")
        g = theme()["grass"]
        levels = (streak or {}).get("levels") or ""
        if not levels:
            return
        try:
            start = datetime.strptime(streak["from"], "%Y-%m-%d").date()
        except (KeyError, ValueError, TypeError):
            return
        pad = start.weekday()          # leave the first column blank up to the starting weekday
        cols = -(-(len(levels) + pad) // 7)
        cell = max(4.0, self.grass.winfo_width() / cols)
        size = cell - max(1, round(cell * 0.15))
        height = int(cell * 7)
        if self.grass.winfo_height() != height:
            self.grass.config(height=height)   # keep canvas height in step with cell size
        for i, ch in enumerate(levels):
            col, row = divmod(i + pad, 7)
            x, y = col * cell, row * cell
            fill = g[int(ch)] if ch.isdigit() and int(ch) < len(g) else g[0]
            self.grass.create_rectangle(x, y, x + size, y + size, fill=fill, width=0)

    def _render_daily(self, daily):
        for ch in self.daily.winfo_children():
            ch.destroy()
        if not daily:
            return
        mx = max((v for _, v in daily), default=1) or 1
        today = datetime.now().astimezone().date()
        for d, v in reversed(daily):
            row = tk.Frame(self.daily, bg=PANEL)
            row.pack(fill="x", pady=3)
            tag = "Today" if d == today else f"{d.month:02d}/{d.day:02d} {_WD[d.weekday()]}"
            _lbl(row, tag, fg=TX, width=9, anchor="w").pack(side="left")
            _lbl(row, _fmt_short(v), width=7, anchor="e").pack(side="right")
            tr = tk.Canvas(row, height=7, bg=TRACK, highlightthickness=0)
            tr.pack(side="left", fill="x", expand=True, padx=8)
            tr.update_idletasks()
            tw = max(tr.winfo_width(), 1)
            col = self.app.accent() if d == today else theme()["bar"]
            tr.create_rectangle(0, 0, int(tw * (v / mx)), 7, fill=col, width=0)

    def _set_count(self, text, col):
        self.hcount.config(text=text, fg=col)
        self.count.config(text=text, fg=col)

    def _set_bars(self, prog, ratio, col):
        w = max(self.bar.winfo_width(), 1)
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, int(w * min(1.0, prog or 0)), 8, fill=col, width=0)
        uw = max(self.hbar.winfo_width(), 1)
        self.hbar.delete("all")
        self.hbar.create_rectangle(0, 0, int(uw * min(1.0, ratio or 0)), 4, fill=col, width=0)

    def tick(self):
        th = theme()
        acc = th["acc"]
        warn = core.CONFIG["warn_ratio"]
        self.dot.config(fg=acc)
        self.wk.config(fg=th["dng"] if (self.wk_ratio is not None and self.wk_ratio >= warn) else MUT)
        now = datetime.now(timezone.utc).astimezone()

        if self.primary == "5h":
            rem = max(0, int((self.reset_at - now).total_seconds()))
            over = self.usage_ratio is not None and self.usage_ratio >= warn
            col = th["dng"] if over else (th["warn"] if rem <= core.CONFIG["reset_soon_min"] * 60 else acc)
            self._set_count(_fmt_dur(rem), col)
            self._set_bars((core.SESSION_WINDOW_SEC - rem) / core.SESSION_WINDOW_SEC, self.usage_ratio, col)
            return rem
        if self.primary == "weekly":
            rem = max(0, int((self.wk_reset - now).total_seconds())) if self.wk_reset else 0
            self._set_count(_fmt_dur(rem), th["dng"])
            self._set_bars(1 - rem / core.WEEKLY_WINDOW_SEC, self.wk_ratio, th["dng"])
            return rem
        self._set_count("Refueled", acc)
        self.bar.delete("all")
        self.hbar.delete("all")
        return None


class RefuelApp:
    def __init__(self, start_hidden=False):
        core.load_config()
        self.start_hidden = start_hidden
        self.state = {}
        self.ready = False
        self.lock = threading.Lock()
        self._ns = {}
        self.cards = {}
        self._card_order = []
        self.expanded_id = None
        self.tray = None
        global _APP
        _APP = self

        self.root = tk.Tk()
        global F
        F = _pick_font(self.root)
        self._apply_dpi_scaling()
        self.root.title("Refuel")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()

        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._update_checker, daemon=True).start()
        threading.Thread(target=self._watch_wake_signal, daemon=True).start()
        self._tick()

    def accent(self):
        return core.CONFIG["accent"]

    def _apply_dpi_scaling(self):
        scale = 1.0
        try:
            dpi = self.root.winfo_fpixels("1i")
            if dpi and dpi > 0:
                scale = max(1.0, dpi / 96.0)
                self.root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        self.root.geometry(f"{int(500 * scale)}x{int(660 * scale)}")
        self.root.minsize(int(450 * scale), int(420 * scale))

    # ---------- UI ----------
    def _build_ui(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=16)
        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="x", pady=(0, 12))
        self.brand = _lbl(top, "● Refuel", fg=self.accent(), size=13, bold=True, bg=BG)
        self.brand.pack(side="left")
        _btn(top, "⚙", self._open_settings, size=11).pack(side="right", padx=(8, 0))
        self.meta = _lbl(top, "", bg=BG)
        self.meta.pack(side="right")
        # The grass pushes "Last 7 days" below the fold, so the list is made wheel-scrollable.
        self.scroll = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        self.scroll.pack(fill="both", expand=True)
        self.cards_box = tk.Frame(self.scroll, bg=BG)
        self._scroll_win = self.scroll.create_window((0, 0), window=self.cards_box, anchor="nw")
        self.cards_box.bind(
            "<Configure>", lambda e: self.scroll.config(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind(
            "<Configure>", lambda e: self.scroll.itemconfigure(self._scroll_win, width=e.width))
        # Only capture the wheel while the pointer is over the list, so other windows
        # such as settings keep their own scrolling.
        self.scroll.bind("<Enter>", lambda e: self.scroll.bind_all("<MouseWheel>", self._on_wheel))
        self.scroll.bind("<Leave>", lambda e: self.scroll.unbind_all("<MouseWheel>"))
        self.empty = _lbl(self.cards_box, "Loading…", size=10, bg=BG)
        self.empty.pack(anchor="w", pady=4)

    def _on_wheel(self, e):
        first, last = self.scroll.yview()
        if first <= 0.0 and last >= 1.0:      # nothing to scroll when everything already fits
            return
        self.scroll.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # ---------- State ----------
    def _check_notifications(self, s):
        """Only two alerts exist: the 5-hour window reset and the weekly reset.
        Figures are on screen whenever you want them, so alerts are reserved for
        telling you that you can use it again."""
        live = set()
        for a in s.get("agents", []):
            live.add(a["id"])
            ns = self._ns.setdefault(a["id"], {"last_start": None, "wk_reset": None})
            nm, b = a["name"], a["block"]

            # --- weekly reset ---
            wkr = (a.get("weekly") or {}).get("reset_at")
            if wkr is not None:
                if ns["wk_reset"] is None:          # first sighting only sets the baseline
                    ns["wk_reset"] = wkr
                elif wkr != ns["wk_reset"]:         # the next reset moved, so the week rolled over
                    _notify(f"{nm} weekly reset", "Your weekly usage limit has reset.", phone=False)
                    ns["wk_reset"] = wkr

            if b is not None:
                # Pre-schedule the push on ntfy so it arrives with the PC off. Once per block.
                try:
                    sync.schedule_refill(a["id"], nm, b["start"], b["reset_at"])
                except Exception as e:
                    log.warning("scheduled push failed: %s", e)

            # --- 5-hour reset ---
            # The window either expired or rolled into a new one, so the previous one reset.
            # A first sighting with last_start=None only sets the baseline.
            # The phone has its own pre-scheduled push, so this stays on the PC.
            start = b["start"] if b else None
            if ns["last_start"] is not None and start != ns["last_start"]:
                _notify(f"{nm} refueled", "Your 5-hour usage limit has reset.", phone=False)
            ns["last_start"] = start
        for dead in [k for k in self._ns if k not in live]:
            self._ns.pop(dead, None)

    def _worker(self):
        while True:
            try:
                s = core.build_state()
                with self.lock:
                    self.state = s
                    self.ready = True
                self._check_notifications(s)
                sync.post_state(s)
            except Exception:
                log.exception("scan failed")
            time.sleep(REFRESH_SECONDS)

    def _update_checker(self):
        """Checks for a new release once a day, notifies once, then stops.
        Can be turned off in settings."""
        time.sleep(10)
        while True:
            if core.CONFIG.get("check_updates", True):
                try:
                    tag = _latest_release_tag()
                    if tag and _parse_ver(tag) > _parse_ver(__version__):
                        _notify("Update available", f"Refuel {tag} is out - {_RELEASES_URL}")
                        return
                except Exception as e:
                    log.info("update check failed (ignored): %s", e)
            time.sleep(86400)

    # ---------- Rendering ----------
    def toggle(self, aid):
        self.expanded_id = None if self.expanded_id == aid else aid
        self._apply_expand()

    def _apply_accent(self):
        """Widgets drawn only once, such as the brand label and grass legend, keep the old
        colour after a theme change, so the cards are rebuilt."""
        self.brand.config(fg=self.accent())
        for c in self.cards.values():
            c.outer.destroy()
        self.cards.clear()
        self._card_order = []      # _reconcile rebuilds with the new theme on the next tick

    def _apply_expand(self):
        for c in self.cards.values():
            c.set_expanded(c.id == self.expanded_id)

    def _reconcile(self, agents):
        ids = [a["id"] for a in agents]
        if ids == self._card_order:
            return
        for c in self.cards.values():
            c.outer.destroy()
        self.cards.clear()
        self._card_order = ids
        self.empty.pack_forget()
        if not ids:
            self.empty.config(text="Loading…" if not self.ready else "No agents detected")
            self.empty.pack(anchor="w", pady=4)
            return
        for a in agents:
            self.cards[a["id"]] = AgentCard(self, self.cards_box, a["id"], a["name"])
        if self.expanded_id not in self.cards:
            self.expanded_id = ids[0]
        self._apply_expand()

    def _tick(self):
        """Runs every second. An escaping exception would break the reschedule and silently
        freeze the app, so nothing is allowed to propagate."""
        try:
            self._tick_body()
        except Exception:
            log.exception("_tick failed (continuing)")
        finally:
            self.root.after(1000, self._tick)

    def _tick_body(self):
        _drain_notifications()          # show alerts queued by the worker thread
        with self.lock:
            s = dict(self.state)
            ready = self.ready
        if ready:
            on = "Alerts ON" if (self.tray or _HAVE_TOAST) else "Alerts OFF"
            self.meta.config(text=f"{on} · {_fmt_n(s.get('total_events'))} events")
        agents = s.get("agents", [])    # already sorted by soonest reset in core.build_state
        self._reconcile(agents)
        soonest, soonest_name = None, ""
        for a in agents:
            c = self.cards.get(a["id"])
            if not c:
                continue
            c.update(a)
            rem = c.tick()
            if rem is not None and (soonest is None or rem < soonest):
                soonest, soonest_name = rem, a["name"]
        self._update_tray_tip(soonest, soonest_name)

    def _update_tray_tip(self, soonest, name):
        if not self.tray:
            return
        tip = f"Refuel · {name} {_fmt_dur(soonest)}" if soonest is not None else "Refuel"
        try:
            if self.tray.title != tip:
                self.tray.title = tip
        except Exception:
            pass

    # ---------- Settings window ----------
    def _open_settings(self):
        if getattr(self, "_settings_win", None) and tk.Toplevel.winfo_exists(self._settings_win):
            self._settings_win.lift()
            return
        win = _dialog(self.root, "Refuel Settings")
        self._settings_win = win
        cfg = core.CONFIG

        _lbl(win, "Weekly reset", bg=BG).pack(anchor="w", pady=(2, 2))
        wkrow = tk.Frame(win, bg=BG)
        wkrow.pack(fill="x")
        dow_var = tk.StringVar(value=_WD[cfg["weekly_reset_dow"]])
        om = tk.OptionMenu(wkrow, dow_var, *_WD)
        om.config(bg=PANEL, fg=TX, font=_font(10), relief="flat", highlightthickness=1,
                  highlightbackground=BORDER, activebackground=BORDER, width=4)
        om["menu"].config(bg=PANEL, fg=TX)
        om.pack(side="left")
        e_hour = tk.Entry(wkrow, bg=PANEL, fg=TX, insertbackground=TX, relief="flat", font=_font(10),
                          highlightbackground=BORDER, highlightthickness=1, width=6)
        e_hour.insert(0, str(cfg["weekly_reset_hour"]))
        e_hour.pack(side="left", padx=8, ipady=4)
        _lbl(wkrow, ":00", bg=BG).pack(side="left")

        tray_var = tk.BooleanVar(value=cfg["minimize_to_tray"])
        auto_var = tk.BooleanVar(value=cfg["autostart"])
        upd_var = tk.BooleanVar(value=cfg.get("check_updates", True))
        for text, var in (("Close to tray. Right-click the tray icon to quit", tray_var),
                          ("Start with Windows, silently into the tray", auto_var),
                          ("Check GitHub for updates once a day", upd_var)):
            _chk(win, text, var).pack(anchor="w", pady=(10, 0))

        sync_var = tk.BooleanVar(value=cfg.get("sync_enabled", False))
        syncrow = tk.Frame(win, bg=BG)
        syncrow.pack(fill="x", pady=(10, 0))
        _chk(syncrow, "Phone sync: send alerts and status to your phone", sync_var).pack(side="left")
        _btn(syncrow, "Pair with QR",
             lambda: (cfg.__setitem__("sync_enabled", True), sync_var.set(True),
                      core.save_config(), self._open_qr())).pack(side="right", ipadx=8, ipady=2)

        _lbl(win, "Accent color", bg=BG).pack(anchor="w", pady=(12, 2))
        accrow = tk.Frame(win, bg=BG)
        accrow.pack(anchor="w")
        acc_var = tk.StringVar(value=cfg["accent"])
        for c in SWATCHES:
            tk.Button(accrow, bg=c, width=2, bd=2, relief="flat",
                      command=lambda c=c: acc_var.set(c)).pack(side="left", padx=3, pady=4)

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", pady=(18, 0))
        _btn(btns, "Send a test alert",
             lambda: _notify("Refuel test", "If you can see this, alerts work.")).pack(side="left", ipady=4, ipadx=8)
        _lbl(win, "A test alert also reaches your phone within 30 seconds, if phone sync is on and the app is open.",
             size=8, bg=BG, wraplength=380, justify="left").pack(anchor="w", pady=(6, 0))

        def save():
            try:
                cfg["weekly_reset_dow"] = _WD.index(dow_var.get())
                cfg["weekly_reset_hour"] = max(0, min(23, int(e_hour.get())))
            except ValueError:
                pass
            cfg["minimize_to_tray"] = tray_var.get()
            cfg["sync_enabled"] = sync_var.get()
            cfg["check_updates"] = upd_var.get()
            if acc_var.get() != cfg["accent"]:
                cfg["accent"] = acc_var.get()
                self._apply_accent()
            if auto_var.get() != cfg["autostart"]:
                cfg["autostart"] = auto_var.get()
                _set_autostart(cfg["autostart"])
            core.save_config()
            win.destroy()

        _btn(btns, "Save", save, fg=BG, bg=acc_var.get(), size=10,
             bold=True).pack(side="right", ipady=4, ipadx=20)

    # ---------- QR pairing ----------
    def _open_qr(self):
        url = sync.pair_url()
        alert_topic = sync.topic() + "-a"
        win = _dialog(self.root, "Pair your phone", pady=16)
        _lbl(win, "1. Scan this QR with the Refuel app to open your dashboard", fg=TX, size=10, bg=BG).pack(anchor="w")
        try:
            import qrcode
            from PIL import ImageTk
            img = qrcode.make(url).resize((260, 260))
            self._qr_photo = ImageTk.PhotoImage(img)
            tk.Label(win, image=self._qr_photo, bg="white").pack(pady=10)
        except Exception as e:
            log.warning("QR generation failed: %s", e)
            _lbl(win, "QR module missing. Open the link below instead.", fg=theme()["warn"], bg=BG).pack(pady=6)
        _readonly(win, url, size=8).pack(fill="x", ipady=3)
        _lbl(win, "2. Optional. Subscribe to this topic in the ntfy app",
             fg=TX, size=10, bg=BG).pack(anchor="w", pady=(12, 2))
        _readonly(win, alert_topic).pack(fill="x", ipady=3)
        _lbl(win, "Status is end-to-end encrypted. Only token counts and times leave your PC.",
             size=8, bg=BG).pack(anchor="w", pady=(8, 0))

        def rotate():
            sync.rotate()
            win.destroy()
            self._open_qr()
            _notify("Pairing reset", "The old QR and topic are now invalid. Scan again on your phone.")

        _btn(win, "Regenerate topic and key", rotate,
             fg=theme()["warn"]).pack(anchor="w", pady=(10, 0), ipadx=8, ipady=3)

    # ---------- Tray and shutdown ----------
    def _on_close(self):
        if self.tray and core.CONFIG["minimize_to_tray"]:
            self.root.withdraw()
        else:
            self._quit()

    def _show(self):
        self.root.after(0, self._raise_window)

    def _raise_window(self):
        """Brings the window forward whether it was hidden or merely behind something."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.root.attributes("-topmost", True)          # make sure it really comes to the front
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception as e:
            log.warning("window raise failed: %s", e)

    def _watch_wake_signal(self):
        """Daemon thread waiting for another launch to ask for the window."""
        try:
            from ctypes import windll
        except Exception:
            return
        h = windll.kernel32.CreateEventW(None, False, False, _WAKE_EVENT)
        if not h:
            return
        while True:
            if windll.kernel32.WaitForSingleObject(h, 0xFFFFFFFF) != 0:
                return
            self.root.after(0, self._raise_window)

    def _quit(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    def _make_icon_image(self):
        img = Image.new("RGB", (64, 64), BG)
        d = ImageDraw.Draw(img)
        d.ellipse((16, 16, 48, 48), fill=core.CONFIG["accent"])
        return img

    def _start_tray(self):
        if not _HAVE_TRAY:
            return
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Open", lambda: self._show(), default=True),
                pystray.MenuItem("Settings", lambda: self.root.after(0, self._open_settings)),
                pystray.MenuItem("Quit", lambda: self.root.after(0, self._quit)),
            )
            self.tray = pystray.Icon("Refuel", self._make_icon_image(), "Refuel", menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            log.warning("tray start failed: %s", e)
            self.tray = None

    def _consent_gate(self):
        """Asks for agreement to the disclaimer on first run. False if declined."""
        if core.has_consented():
            return True
        self.root.deiconify()
        win = _dialog(self.root, "Refuel Terms", padx=22)
        win.transient(self.root)
        result = {"ok": False}

        _lbl(win, "Before you start", fg=self.accent(), size=14, bold=True, bg=BG).pack(anchor="w")

        # Pin the button row to the bottom first. Adding the text first grows the window
        # until the agree button is pushed off screen.
        agree = tk.BooleanVar(value=False)
        row = tk.Frame(win, bg=BG)
        row.pack(side="bottom", fill="x", pady=(12, 0))
        chk = _chk(win, "I have read and agree to the above.", agree)
        chk.config(font=_font(10))
        chk.pack(side="bottom", anchor="w")

        btn = _btn(row, "Agree & start", None, fg=MUT, bg=BORDER, size=10, bold=True,
                   state="disabled", cursor="")
        btn.pack(side="right", ipadx=16, ipady=5)
        _btn(row, "Decline and quit", win.destroy, fg=MUT, bg=BG).pack(side="left")

        def toggle():
            if agree.get():
                btn.config(state="normal", bg=self.accent(), fg=BG, cursor="hand2")
            else:
                btn.config(state="disabled", bg=BORDER, fg=MUT, cursor="")

        chk.config(command=toggle)

        # The body fills the rest and scrolls, so buttons stay visible on small screens.
        body = tk.Frame(win, bg=BG)
        body.pack(side="top", fill="both", expand=True, pady=(10, 4))
        sb = tk.Scrollbar(body)
        sb.pack(side="right", fill="y")
        box = tk.Text(body, width=50, height=11, bg=PANEL, fg=TX, bd=0,
                      relief="flat", wrap="word", font=_font(9), padx=12, pady=10,
                      highlightbackground=BORDER, highlightthickness=1,
                      yscrollcommand=sb.set)
        box.insert("1.0", core.DISCLAIMER_TEXT)
        box.config(state="disabled")
        box.pack(side="left", fill="both", expand=True)
        sb.config(command=box.yview)

        def accept():
            core.set_consented()
            result["ok"] = True
            win.destroy()

        btn.config(command=accept)

        # clamp the size to the screen and centre it
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        h = min(h, int(sh * 0.8))
        win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")
        win.minsize(420, 360)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        self.root.wait_window(win)
        return result["ok"]

    def run(self):
        self._start_tray()
        if not self._consent_gate():
            log.info("Disclaimer declined - exiting")
            self._quit()
            return
        if core.CONFIG["autostart"]:
            _set_autostart(True)
        if self.start_hidden and self.tray:
            self.root.withdraw()
        self.root.mainloop()


def main():
    core.setup_logging()
    _enable_dpi()
    if not _single_instance():
        # Bring the existing window forward instead of firing a notification.
        if not _wake_running_instance():
            _notify("Refuel", "Refuel is already running.", phone=False)   # only if the signal could not be delivered
        return
    RefuelApp(start_hidden="--minimized" in sys.argv).run()


if __name__ == "__main__":
    main()
