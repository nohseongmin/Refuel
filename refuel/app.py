"""Refuel GUI - 다크 트레이 앱.

- 순수 Tkinter 다크 창 — 폰트 자동선택(한글+숫자 통일), 재충전 카운트다운 + 사용량 + 일별
- pystray 트레이 상주 (창 닫으면 트레이로, 우클릭 종료로만 완전 종료)
- winotify 토스트 알림 (재충전 완료 / 리셋 임박 / 사용량 경고)
- 설정창 (강조색 / 트레이동작 / 자동시작 / 주간리셋). 한도는 자동 추정이라 입력칸 없음.
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
    """한글+숫자가 한 폰트로 통일되도록 선택. 한글 코딩폰트가 있으면 우선."""
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


class RefuelApp:
    def __init__(self):
        core.load_config()
        self.state = {}
        self.lock = threading.Lock()
        self._ns = {"last_start": None, "warned_ratio": False, "warned_soon": False}
        self.tray = None

        self.root = tk.Tk()
        global F
        F = _pick_font(self.root)
        self.root.title("Refuel")
        self.root.configure(bg=BG)
        self.root.geometry("520x600")
        self.root.minsize(480, 560)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._update_state(core.build_state(), first=True)
        threading.Thread(target=self._worker, daemon=True).start()
        self._tick()

    # ---------- UI ----------
    def _card(self, parent, label):
        f = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        tk.Label(f, text=label, bg=PANEL, fg=MUT, font=(F, 9)).pack(anchor="w", padx=14, pady=(11, 0))
        val = tk.Label(f, text="0", bg=PANEL, fg=TX, font=(F, 17, "bold"))
        val.pack(anchor="w", padx=14, pady=(2, 0))
        sub = tk.Label(f, text="", bg=PANEL, fg=MUT, font=(F, 8))
        sub.pack(anchor="w", padx=14, pady=(0, 10))
        return f, val, sub

    def _build_ui(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=16)

        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="● Refuel", bg=BG, fg=core.CONFIG["accent"], font=(F, 13, "bold")).pack(side="left")
        tk.Button(top, text="⚙", bg=PANEL, fg=TX, font=(F, 11), bd=0, relief="flat",
                  activebackground=BORDER, activeforeground=TX, cursor="hand2",
                  command=self._open_settings).pack(side="right", padx=(8, 0))
        self.meta = tk.Label(top, text="", bg=BG, fg=MUT, font=(F, 9))
        self.meta.pack(side="right")

        self.agents = tk.Label(wrap, text="", bg=BG, fg=MUT, font=(F, 8), anchor="w")
        self.agents.pack(fill="x", pady=(6, 0))

        hero = tk.Frame(wrap, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        hero.pack(fill="x", pady=(8, 12))
        self.hlabel = tk.Label(hero, text="재충전까지", bg=PANEL, fg=MUT, font=(F, 9))
        self.hlabel.pack(anchor="w", padx=18, pady=(16, 2))
        self.count = tk.Label(hero, text="--:--:--", bg=PANEL, fg=TX, font=(F, 44, "bold"))
        self.count.pack(anchor="w", padx=16)
        self.sub = tk.Label(hero, text="", bg=PANEL, fg=MUT, font=(F, 10))
        self.sub.pack(anchor="w", padx=18, pady=(6, 4))
        self.bar = tk.Canvas(hero, height=8, bg=TRACK, highlightthickness=0)
        self.bar.pack(fill="x", padx=18, pady=(6, 18))

        grid = tk.Frame(wrap, bg=BG)
        grid.pack(fill="x")
        c1, self.v_cw, _ = self._card(grid, "현재 윈도우")
        c2, self.v_today, _ = self._card(grid, "오늘")
        c3, self.v_week, self.s_week = self._card(grid, "이번 주")
        for i, c in enumerate((c1, c2, c3)):
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            grid.columnconfigure(i, weight=1)

        dp = tk.Frame(wrap, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        dp.pack(fill="both", expand=True, pady=(12, 0))
        tk.Label(dp, text="최근 7일 사용량", bg=PANEL, fg=MUT, font=(F, 9)).pack(anchor="w", padx=16, pady=(12, 6))
        self.daily = tk.Frame(dp, bg=PANEL)
        self.daily.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    # ---------- 상태 ----------
    def _update_state(self, s, first=False):
        with self.lock:
            self.state = s
        if first:
            self._ns["last_start"] = s["block"]["start"] if s["block"] else None
        else:
            self._check_notifications(s)

    def _check_notifications(self, s):
        b, ns = s.get("block"), self._ns
        soon_sec = core.CONFIG["reset_soon_min"] * 60
        if b is None:
            if ns["last_start"] is not None:
                _notify("재충전 완료", "5시간 윈도우가 리셋됐어. 다시 써도 돼.")
                ns.update(last_start=None, warned_ratio=False, warned_soon=False)
            return
        if ns["last_start"] != b["start"]:
            if ns["last_start"] is not None:
                _notify("재충전 완료", "새 5시간 윈도우 시작 - 한도 리셋됨.")
            ns.update(last_start=b["start"], warned_ratio=False, warned_soon=False)
        if 0 < b["remaining_sec"] <= soon_sec and not ns["warned_soon"]:
            _notify("리셋 임박", f"{b['remaining_sec'] // 60}분 뒤 윈도우 리셋. 마무리 정리해.")
            ns["warned_soon"] = True
        if b["ratio"] is not None and b["ratio"] >= core.CONFIG["warn_ratio"] and not ns["warned_ratio"]:
            _notify("사용량 경고", f"이번 윈도우가 평소 최대의 {int(b['ratio'] * 100)}%. 곧 끊길 수 있어.")
            ns["warned_ratio"] = True

    def _worker(self):
        while True:
            time.sleep(REFRESH_SECONDS)
            try:
                self._update_state(core.build_state())
            except Exception as e:
                print("refresh 오류:", e)

    # ---------- 렌더 ----------
    def _tick(self):
        with self.lock:
            s = dict(self.state)
        acc = core.CONFIG["accent"]
        ag = s.get("agents", [])
        self.agents.config(text="감지된 에이전트 · " + (", ".join(a["name"] for a in ag) if ag else "없음"))
        self.meta.config(text=f"{'알림 ON' if _HAVE_TOAST else '알림 OFF'} · 이벤트 {_fmt_n(s.get('total_events'))}")
        b = s.get("block")
        w = max(self.bar.winfo_width(), 1)
        self.bar.delete("all")
        if not b:
            self.hlabel.config(text="상태")
            self.count.config(text="완충", fg=acc)
            self.sub.config(text="활성 윈도우 없음 - 지금 바로 사용 가능")
            self.v_cw.config(text="0")
        else:
            now = datetime.now(timezone.utc).astimezone()
            remaining = max(0, int((b["reset_at"] - now).total_seconds()))
            ratio = min(1.0, (18000 - remaining) / 18000)
            soon = remaining <= core.CONFIG["reset_soon_min"] * 60
            over = b["ratio"] is not None and b["ratio"] >= core.CONFIG["warn_ratio"]
            col = DNG if over else (WARN if soon else acc)
            self.hlabel.config(text="재충전까지")
            self.count.config(text=_fmt_dur(remaining), fg=col)
            extra = f" · 추정한도 {int(b['ratio'] * 100)}%" if b["ratio"] is not None else ""
            self.sub.config(text=f"리셋 {b['reset_at'].strftime('%H:%M')} · 윈도우 {_fmt_n(b['tokens'])} 토큰{extra}")
            self.v_cw.config(text=_fmt_n(b["tokens"]))
            self.bar.create_rectangle(0, 0, int(w * ratio), 8, fill=col, width=0)

        self.v_today.config(text=_fmt_n(s.get("today_tokens")))
        self.v_week.config(text=_fmt_n(s.get("week_tokens")))
        wr = s.get("weekly_reset")
        if wr:
            days = s.get("weekly_remaining_sec", 0) // 86400
            self.s_week.config(text=f"리셋 {_WD[wr.weekday()]} {wr.strftime('%H:%M')} · D-{days}")
        self._render_daily(s.get("daily", []))
        self.root.after(1000, self._tick)

    def _render_daily(self, daily):
        for ch in self.daily.winfo_children():
            ch.destroy()
        if not daily:
            tk.Label(self.daily, text="데이터 없음", bg=PANEL, fg=MUT, font=(F, 9)).pack(anchor="w")
            return
        mx = max((v for _, v in daily), default=1) or 1
        today = datetime.now().astimezone().date()
        for d, v in reversed(daily):
            row = tk.Frame(self.daily, bg=PANEL)
            row.pack(fill="x", pady=3)
            tag = "오늘" if d == today else f"{d.month:02d}/{d.day:02d} {_WD[d.weekday()]}"
            tk.Label(row, text=tag, bg=PANEL, fg=TX, font=(F, 9), width=9, anchor="w").pack(side="left")
            tk.Label(row, text=_fmt_short(v), bg=PANEL, fg=MUT, font=(F, 9), width=7, anchor="e").pack(side="right")
            track = tk.Canvas(row, height=7, bg=TRACK, highlightthickness=0)
            track.pack(side="left", fill="x", expand=True, padx=8)
            track.update_idletasks()
            tw = max(track.winfo_width(), 1)
            col = core.CONFIG["accent"] if d == today else BLU
            track.create_rectangle(0, 0, int(tw * (v / mx)), 7, fill=col, width=0)

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
