"""Refuel GUI - 다크 트레이 앱 (에이전트별 아코디언 카드).

- 에이전트별 카드 스택: 접힌 상태=한 줄(이름·미니게이지·리셋), 클릭하면 펼쳐져 상세
- 폰트 자동선택(한글+숫자 통일), 한도 자동추정, 로그 위치 자동탐지
- pystray 트레이 상주(닫으면 트레이로, 우클릭 종료로만 완전 종료) + winotify 토스트
"""
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timezone

from . import core

# ---------------- 팔레트 ----------------
BG = "#0d0f14"
PANEL = "#141821"
CARD = "#11151d"
BORDER = "#252c3a"
TRACK = "#0a0c11"
TX = "#e7eaf0"
MUT = "#8a93a4"
WARN = "#f5c451"
DNG = "#f3766b"
BLU = "#5a8dee"

F = "Malgun Gothic"   # __init__에서 자동선택으로 덮어씀
REFRESH_SECONDS = 20
_WD = ["월", "화", "수", "목", "금", "토", "일"]

# ---------------- 선택 의존성 ----------------
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


def _pick_font(root):
    try:
        fams = set(tkfont.families(root))
    except Exception:
        return "Malgun Gothic"
    for p in ("D2Coding", "NanumGothicCoding", "Nanum Gothic Coding",
              "Sarasa Mono K", "Malgun Gothic", "맑은 고딕", "Consolas"):
        if p in fams:
            return p
    return "Malgun Gothic"


def _notify(title, msg):
    print(f"[알림] {title} - {msg}")
    if not _HAVE_TOAST:
        return
    try:
        t = Notification(app_id="Refuel", title=title, msg=msg)
        t.set_audio(audio.Default, loop=False)
        t.show()
    except Exception as e:
        print("  (토스트 실패:", e, ")")


def _set_autostart(enable):
    if not _HAVE_REG:
        return
    try:
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}"'
        else:
            script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "run.py"))
            cmd = f'"{sys.executable}" "{script}"'
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
        print("autostart 설정 실패:", e)


def _fmt_dur(sec):
    if sec is None:
        return "--:--:--"
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _fmt_n(v):
    return f"{int(v or 0):,}"


def _fmt_short(v):
    v = int(v or 0)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


class AgentCard:
    """에이전트 1개 카드 (접힘=헤더 한 줄, 펼침=상세)."""

    def __init__(self, app, parent, aid, name):
        self.app, self.id, self.name = app, aid, name
        self.expanded = False
        self.reset_at = None
        self.usage_ratio = None
        self.has_block = False

        self.outer = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        self.outer.pack(fill="x", pady=(0, 10))

        h = tk.Frame(self.outer, bg=PANEL, cursor="hand2")
        h.pack(fill="x")
        self.chev = tk.Label(h, text="▸", bg=PANEL, fg=MUT, font=(F, 10), width=2)
        self.chev.pack(side="left", padx=(8, 0), pady=9)
        self.dot = tk.Label(h, text="●", bg=PANEL, fg=app.accent(), font=(F, 10))
        self.dot.pack(side="left")
        self.name_lbl = tk.Label(h, text=name, bg=PANEL, fg=TX, font=(F, 11, "bold"))
        self.name_lbl.pack(side="left", padx=6)
        self.hcount = tk.Label(h, text="--:--:--", bg=PANEL, fg=MUT, font=(F, 11))
        self.hcount.pack(side="right", padx=14)
        for wdg in (h, self.chev, self.dot, self.name_lbl, self.hcount):
            wdg.bind("<Button-1>", lambda e: self.app.toggle(self.id))

        self.hbar = tk.Canvas(self.outer, height=4, bg=TRACK, highlightthickness=0)
        self.hbar.pack(fill="x", padx=12, pady=(0, 10))

        self.detail = tk.Frame(self.outer, bg=PANEL)
        self._build_detail()

    def _mkcard(self, parent, label, col):
        f = tk.Frame(parent, bg=CARD)
        f.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
        tk.Label(f, text=label, bg=CARD, fg=MUT, font=(F, 8)).pack(anchor="w", padx=10, pady=(8, 0))
        val = tk.Label(f, text="0", bg=CARD, fg=TX, font=(F, 15, "bold"))
        val.pack(anchor="w", padx=10, pady=(1, 0))
        sub = tk.Label(f, text="", bg=CARD, fg=MUT, font=(F, 8))
        sub.pack(anchor="w", padx=10, pady=(0, 8))
        return val, sub

    def _build_detail(self):
        d = self.detail
        self.count = tk.Label(d, text="--:--:--", bg=PANEL, fg=TX, font=(F, 38, "bold"))
        self.count.pack(anchor="w", padx=16, pady=(2, 0))
        self.sub = tk.Label(d, text="", bg=PANEL, fg=MUT, font=(F, 10))
        self.sub.pack(anchor="w", padx=16, pady=(4, 4))
        self.bar = tk.Canvas(d, height=8, bg=TRACK, highlightthickness=0)
        self.bar.pack(fill="x", padx=16, pady=(2, 12))
        grid = tk.Frame(d, bg=PANEL)
        grid.pack(fill="x", padx=12)
        self.cw_val, _ = self._mkcard(grid, "현재 윈도우", 0)
        self.today_val, _ = self._mkcard(grid, "오늘", 1)
        self.week_val, self.week_sub = self._mkcard(grid, "이번 주", 2)
        for i in range(3):
            grid.columnconfigure(i, weight=1)
        tk.Label(d, text="최근 7일", bg=PANEL, fg=MUT, font=(F, 9)).pack(anchor="w", padx=16, pady=(12, 4))
        self.daily = tk.Frame(d, bg=PANEL)
        self.daily.pack(fill="x", padx=16, pady=(0, 12))

    def set_expanded(self, val):
        self.expanded = val
        self.chev.config(text="▾" if val else "▸")
        if val:
            self.detail.pack(fill="x")
        else:
            self.detail.pack_forget()

    def update(self, a):
        b = a["block"]
        self.has_block = b is not None
        if b:
            self.reset_at = b["reset_at"]
            self.usage_ratio = b["ratio"]
            self.cw_val.config(text=_fmt_n(b["tokens"]))
            extra = f" · 추정한도 {int(b['ratio'] * 100)}%" if b["ratio"] is not None else ""
            self.sub.config(text=f"리셋 {b['reset_at'].strftime('%H:%M')} · 윈도우 {_fmt_n(b['tokens'])} 토큰{extra}")
        else:
            self.reset_at = None
            self.usage_ratio = None
            self.cw_val.config(text="0")
            self.sub.config(text="활성 윈도우 없음 - 지금 바로 사용 가능")
        self.today_val.config(text=_fmt_n(a["today_tokens"]))
        self.week_val.config(text=_fmt_n(a["week_tokens"]))
        wr = a.get("weekly_reset")
        if wr:
            days = a.get("weekly_remaining_sec", 0) // 86400
            self.week_sub.config(text=f"리셋 {_WD[wr.weekday()]} {wr.strftime('%H:%M')} · D-{days}")
        self._render_daily(a.get("daily", []))

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
            tag = "오늘" if d == today else f"{d.month:02d}/{d.day:02d} {_WD[d.weekday()]}"
            tk.Label(row, text=tag, bg=PANEL, fg=TX, font=(F, 9), width=9, anchor="w").pack(side="left")
            tk.Label(row, text=_fmt_short(v), bg=PANEL, fg=MUT, font=(F, 9), width=7, anchor="e").pack(side="right")
            tr = tk.Canvas(row, height=7, bg=TRACK, highlightthickness=0)
            tr.pack(side="left", fill="x", expand=True, padx=8)
            tr.update_idletasks()
            tw = max(tr.winfo_width(), 1)
            col = self.app.accent() if d == today else BLU
            tr.create_rectangle(0, 0, int(tw * (v / mx)), 7, fill=col, width=0)

    def tick(self):
        acc = self.app.accent()
        self.dot.config(fg=acc)
        if not self.has_block:
            self.hcount.config(text="완충", fg=acc)
            self.count.config(text="완충", fg=acc)
            self.hbar.delete("all")
            self.bar.delete("all")
            return
        now = datetime.now(timezone.utc).astimezone()
        rem = max(0, int((self.reset_at - now).total_seconds()))
        prog = min(1.0, (18000 - rem) / 18000)
        soon = rem <= core.CONFIG["reset_soon_min"] * 60
        over = self.usage_ratio is not None and self.usage_ratio >= core.CONFIG["warn_ratio"]
        col = DNG if over else (WARN if soon else acc)
        self.hcount.config(text=_fmt_dur(rem), fg=col)
        self.count.config(text=_fmt_dur(rem), fg=col)
        w = max(self.bar.winfo_width(), 1)
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, int(w * prog), 8, fill=col, width=0)
        uw = max(self.hbar.winfo_width(), 1)
        self.hbar.delete("all")
        self.hbar.create_rectangle(0, 0, int(uw * min(1.0, self.usage_ratio or 0)), 4, fill=col, width=0)


class RefuelApp:
    def __init__(self):
        core.load_config()
        self.state = {}
        self.lock = threading.Lock()
        self._ns = {}        # agent_id -> 알림 상태
        self.cards = {}
        self._card_order = []
        self.expanded_id = None
        self.tray = None

        self.root = tk.Tk()
        global F
        F = _pick_font(self.root)
        self.root.title("Refuel")
        self.root.configure(bg=BG)
        self.root.geometry("500x640")
        self.root.minsize(460, 420)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._update_state(core.build_state(), first=True)
        threading.Thread(target=self._worker, daemon=True).start()
        self._tick()

    def accent(self):
        return core.CONFIG["accent"]

    # ---------- UI ----------
    def _build_ui(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=16)
        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="x", pady=(0, 12))
        tk.Label(top, text="● Refuel", bg=BG, fg=self.accent(), font=(F, 13, "bold")).pack(side="left")
        tk.Button(top, text="⚙", bg=PANEL, fg=TX, font=(F, 11), bd=0, relief="flat",
                  activebackground=BORDER, activeforeground=TX, cursor="hand2",
                  command=self._open_settings).pack(side="right", padx=(8, 0))
        self.meta = tk.Label(top, text="", bg=BG, fg=MUT, font=(F, 9))
        self.meta.pack(side="right")
        self.cards_box = tk.Frame(wrap, bg=BG)
        self.cards_box.pack(fill="both", expand=True)
        self.empty = tk.Label(self.cards_box, text="감지된 에이전트 없음", bg=BG, fg=MUT, font=(F, 10))

    # ---------- 상태 ----------
    def _update_state(self, s, first=False):
        with self.lock:
            self.state = s
        if not first:
            self._check_notifications(s)

    def _check_notifications(self, s):
        soon_sec = core.CONFIG["reset_soon_min"] * 60
        warn = core.CONFIG["warn_ratio"]
        live = set()
        for a in s.get("agents", []):
            live.add(a["id"])
            ns = self._ns.setdefault(a["id"], {"last_start": None, "warned_ratio": False, "warned_soon": False})
            b = a["block"]
            nm = a["name"]
            if b is None:
                if ns["last_start"] is not None:
                    _notify(f"{nm} 재충전 완료", "윈도우 리셋됨. 다시 써도 돼.")
                    ns.update(last_start=None, warned_ratio=False, warned_soon=False)
                continue
            if ns["last_start"] != b["start"]:
                if ns["last_start"] is not None:
                    _notify(f"{nm} 재충전 완료", "새 윈도우 시작 - 한도 리셋됨.")
                ns.update(last_start=b["start"], warned_ratio=False, warned_soon=False)
            if 0 < b["remaining_sec"] <= soon_sec and not ns["warned_soon"]:
                _notify(f"{nm} 리셋 임박", f"{b['remaining_sec'] // 60}분 뒤 리셋. 마무리 정리해.")
                ns["warned_soon"] = True
            if b["ratio"] is not None and b["ratio"] >= warn and not ns["warned_ratio"]:
                _notify(f"{nm} 사용량 경고", f"평소 최대의 {int(b['ratio'] * 100)}%. 곧 끊길 수 있어.")
                ns["warned_ratio"] = True
        for dead in [k for k in self._ns if k not in live]:
            self._ns.pop(dead, None)

    def _worker(self):
        while True:
            time.sleep(REFRESH_SECONDS)
            try:
                self._update_state(core.build_state())
            except Exception as e:
                print("refresh 오류:", e)

    # ---------- 렌더 ----------
    def toggle(self, aid):
        self.expanded_id = None if self.expanded_id == aid else aid
        self._apply_expand()

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
            self.empty.pack(anchor="w", pady=4)
            return
        for a in agents:
            self.cards[a["id"]] = AgentCard(self, self.cards_box, a["id"], a["name"])
        if self.expanded_id not in self.cards:
            self.expanded_id = ids[0]
        self._apply_expand()

    def _tick(self):
        with self.lock:
            s = dict(self.state)
        self.meta.config(text=f"{'알림 ON' if _HAVE_TOAST else '알림 OFF'} · 이벤트 {_fmt_n(s.get('total_events'))}")
        agents = sorted(s.get("agents", []),
                        key=lambda a: a["block"]["remaining_sec"] if a["block"] else 10 ** 9)
        self._reconcile(agents)
        for a in agents:
            c = self.cards.get(a["id"])
            if c:
                c.update(a)
                c.tick()
        self.root.after(1000, self._tick)

    # ---------- 설정창 ----------
    def _open_settings(self):
        if getattr(self, "_settings_win", None) and tk.Toplevel.winfo_exists(self._settings_win):
            self._settings_win.lift()
            return
        win = tk.Toplevel(self.root, bg=BG)
        self._settings_win = win
        win.title("Refuel 설정")
        win.geometry("340x320")
        win.configure(padx=20, pady=18)
        cfg = core.CONFIG

        tk.Label(win, text="주간 리셋", bg=BG, fg=MUT, font=(F, 9)).pack(anchor="w", pady=(2, 2))
        wkrow = tk.Frame(win, bg=BG)
        wkrow.pack(fill="x")
        dow_var = tk.StringVar(value=_WD[cfg["weekly_reset_dow"]])
        om = tk.OptionMenu(wkrow, dow_var, *_WD)
        om.config(bg=PANEL, fg=TX, font=(F, 10), relief="flat", highlightthickness=1,
                  highlightbackground=BORDER, activebackground=BORDER, width=4)
        om["menu"].config(bg=PANEL, fg=TX)
        om.pack(side="left")
        e_hour = tk.Entry(wkrow, bg=PANEL, fg=TX, insertbackground=TX, relief="flat", font=(F, 10),
                          highlightbackground=BORDER, highlightthickness=1, width=6)
        e_hour.insert(0, str(cfg["weekly_reset_hour"]))
        e_hour.pack(side="left", padx=8, ipady=4)
        tk.Label(wkrow, text="시", bg=BG, fg=MUT, font=(F, 9)).pack(side="left")

        tray_var = tk.BooleanVar(value=cfg["minimize_to_tray"])
        auto_var = tk.BooleanVar(value=cfg["autostart"])

        def check(t, var):
            tk.Checkbutton(win, text=t, variable=var, bg=BG, fg=TX, font=(F, 9),
                           selectcolor=PANEL, activebackground=BG, activeforeground=TX,
                           bd=0, highlightthickness=0).pack(anchor="w", pady=(10, 0))

        check("창 닫으면 트레이로 (우클릭 종료로만 완전 종료)", tray_var)
        check("윈도우 시작 시 자동 실행", auto_var)

        tk.Label(win, text="강조 색상", bg=BG, fg=MUT, font=(F, 9)).pack(anchor="w", pady=(12, 2))
        accrow = tk.Frame(win, bg=BG)
        accrow.pack(anchor="w")
        acc_var = tk.StringVar(value=cfg["accent"])
        for c in ["#46e08a", "#5a8dee", "#f5c451", "#f3766b", "#b388ff"]:
            tk.Button(accrow, bg=c, width=2, bd=2, relief="flat",
                      command=lambda c=c: acc_var.set(c)).pack(side="left", padx=3, pady=4)

        def save():
            try:
                cfg["weekly_reset_dow"] = _WD.index(dow_var.get())
                cfg["weekly_reset_hour"] = max(0, min(23, int(e_hour.get())))
            except ValueError:
                pass
            cfg["minimize_to_tray"] = tray_var.get()
            cfg["accent"] = acc_var.get()
            if auto_var.get() != cfg["autostart"]:
                cfg["autostart"] = auto_var.get()
                _set_autostart(cfg["autostart"])
            core.save_config()
            self._update_state(core.build_state())
            win.destroy()

        tk.Button(win, text="저장", bg=acc_var.get(), fg=BG, font=(F, 10, "bold"), bd=0,
                  relief="flat", cursor="hand2", command=save).pack(fill="x", pady=(18, 0), ipady=6)

    # ---------- 트레이 / 종료 ----------
    def _on_close(self):
        if self.tray and core.CONFIG["minimize_to_tray"]:
            self.root.withdraw()
        else:
            self._quit()

    def _show(self):
        self.root.after(0, self.root.deiconify)

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
                pystray.MenuItem("열기", lambda: self._show(), default=True),
                pystray.MenuItem("설정", lambda: self.root.after(0, self._open_settings)),
                pystray.MenuItem("종료", lambda: self.root.after(0, self._quit)),
            )
            self.tray = pystray.Icon("Refuel", self._make_icon_image(), "Refuel", menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            print("트레이 시작 실패:", e)
            self.tray = None

    def run(self):
        self._start_tray()
        if core.CONFIG["autostart"]:
            _set_autostart(True)
        self.root.mainloop()


def main():
    RefuelApp().run()


if __name__ == "__main__":
    main()
