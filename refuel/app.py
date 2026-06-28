"""Refuel GUI - 다크 트레이 앱.

- 순수 Tkinter 다크 창 (재충전 카운트다운 + 사용량 카드 + 모델별 막대)
- pystray 트레이 상주 (창 닫으면 트레이로 숨김)
- winotify 토스트 알림 (재충전 완료 / 리셋 임박 / 사용량 경고)

pystray·Pillow·winotify 가 없어도 동작(트레이/토스트만 비활성).
"""
import threading
import time
import tkinter as tk
from datetime import datetime, timezone

from . import core

# ---------------- 팔레트 ----------------
BG = "#0d0f14"
PANEL = "#141821"
BORDER = "#252c3a"
TX = "#e7eaf0"
MUT = "#8a93a4"
ACC = "#46e08a"
WARN = "#f5c451"
DNG = "#f3766b"
BLU = "#5a8dee"
MONO = ("Consolas", 11)
MONO_BIG = ("Consolas", 46, "bold")
SANS = ("Segoe UI", 10)
REFRESH_SECONDS = 20

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


def _fmt_dur(sec):
    if sec is None:
        return "--:--:--"
    sec = max(0, int(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_n(v):
    return f"{int(v or 0):,}"


def _short_model(m):
    m = (m or "unknown").replace("claude-", "")
    parts = m.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        return parts[0]
    return m


class RefuelApp:
    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()
        self._ns = {"last_start": None, "warned_ratio": False, "warned_soon": False}
        self.tray = None

        self.root = tk.Tk()
        self.root.title("Refuel")
        self.root.configure(bg=BG)
        self.root.geometry("520x600")
        self.root.minsize(460, 560)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 첫 스캔(동기) 후 백그라운드 루프 시작
        self._update_state(core.build_state(), first=True)
        threading.Thread(target=self._worker, daemon=True).start()
        self._tick()

    # ---------- UI ----------
    def _card(self, parent, label):
        f = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER,
                     highlightthickness=1)
        tk.Label(f, text=label, bg=PANEL, fg=MUT, font=SANS).pack(anchor="w", padx=14, pady=(12, 0))
        val = tk.Label(f, text="0", bg=PANEL, fg=TX, font=("Consolas", 19, "bold"))
        val.pack(anchor="w", padx=14, pady=(2, 12))
        return f, val

    def _build_ui(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=16)

        top = tk.Frame(wrap, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="● Refuel", bg=BG, fg=ACC, font=("Segoe UI", 13, "bold")).pack(side="left")
        self.meta = tk.Label(top, text="", bg=BG, fg=MUT, font=MONO)
        self.meta.pack(side="right")

        hero = tk.Frame(wrap, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        hero.pack(fill="x", pady=(14, 12))
        self.hlabel = tk.Label(hero, text="재충전까지", bg=PANEL, fg=MUT, font=("Segoe UI", 9))
        self.hlabel.pack(anchor="w", padx=18, pady=(16, 2))
        self.count = tk.Label(hero, text="--:--:--", bg=PANEL, fg=TX, font=MONO_BIG)
        self.count.pack(anchor="w", padx=16)
        self.sub = tk.Label(hero, text="", bg=PANEL, fg=MUT, font=MONO)
        self.sub.pack(anchor="w", padx=18, pady=(6, 4))
        self.bar = tk.Canvas(hero, height=8, bg="#0a0c11", highlightthickness=0)
        self.bar.pack(fill="x", padx=18, pady=(6, 18))

        grid = tk.Frame(wrap, bg=BG)
        grid.pack(fill="x")
        c1, self.v_cw = self._card(grid, "현재 윈도우")
        c2, self.v_today = self._card(grid, "오늘")
        c3, self.v_week = self._card(grid, "최근 7일")
        for i, c in enumerate((c1, c2, c3)):
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            grid.columnconfigure(i, weight=1)

        mp = tk.Frame(wrap, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        mp.pack(fill="both", expand=True, pady=(12, 0))
        tk.Label(mp, text="모델별 누적", bg=PANEL, fg=MUT, font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(12, 6))
        self.models = tk.Frame(mp, bg=PANEL)
        self.models.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    # ---------- 상태 ----------
    def _update_state(self, s, first=False):
        with self.lock:
            self.state = s
        if first:
            self._ns["last_start"] = s["block"]["start"] if s["block"] else None
        else:
            self._check_notifications(s)

    def _check_notifications(self, s):
        b = s.get("block")
        ns = self._ns
        if b is None:
            if ns["last_start"] is not None:
                _notify("재충전 완료", "5시간 윈도우가 리셋됐어. 다시 써도 돼.")
                ns.update(last_start=None, warned_ratio=False, warned_soon=False)
            return
        if ns["last_start"] != b["start"]:
            if ns["last_start"] is not None:
                _notify("재충전 완료", "새 5시간 윈도우 시작 - 한도 리셋됨.")
            ns.update(last_start=b["start"], warned_ratio=False, warned_soon=False)
        if 0 < b["remaining_sec"] <= core.RESET_SOON_MIN * 60 and not ns["warned_soon"]:
            _notify("리셋 임박", f"{b['remaining_sec'] // 60}분 뒤 윈도우 리셋. 마무리 정리해.")
            ns["warned_soon"] = True
        if b["ratio"] is not None and b["ratio"] >= core.WARN_RATIO and not ns["warned_ratio"]:
            _notify("사용량 경고", f"이번 윈도우 {int(b['ratio'] * 100)}% 사용. 곧 끊길 수 있어.")
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
        self.meta.config(text=f"{'알림 ON' if _HAVE_TOAST else '알림 OFF'} · 이벤트 {_fmt_n(s.get('total_events'))}")
        b = s.get("block")
        w = max(self.bar.winfo_width(), 1)
        self.bar.delete("all")
        if not b:
            self.hlabel.config(text="상태")
            self.count.config(text="완충", fg=ACC)
            self.sub.config(text="활성 윈도우 없음 - 지금 바로 사용 가능")
            self.v_cw.config(text="0")
        else:
            now = datetime.now(timezone.utc)
            remaining = max(0, int((b["reset_at"] - now.astimezone()).total_seconds()))
            ratio = min(1.0, (18000 - remaining) / 18000)
            soon = remaining <= core.RESET_SOON_MIN * 60
            over = b["ratio"] is not None and b["ratio"] >= core.WARN_RATIO
            col = DNG if over else (WARN if soon else ACC)
            self.hlabel.config(text="재충전까지")
            self.count.config(text=_fmt_dur(remaining), fg=col)
            t = b["reset_at"].strftime("%H:%M")
            extra = f" · 한도 {int(b['ratio'] * 100)}%" if b["ratio"] is not None else ""
            self.sub.config(text=f"리셋 {t} · 윈도우 {_fmt_n(b['tokens'])} 토큰{extra}")
            self.v_cw.config(text=_fmt_n(b["tokens"]))
            self.bar.create_rectangle(0, 0, int(w * ratio), 8, fill=col, width=0)

        self.v_today.config(text=_fmt_n(s.get("today_tokens")))
        self.v_week.config(text=_fmt_n(s.get("week_tokens")))
        self._render_models(s.get("by_model", {}))
        self.root.after(1000, self._tick)

    def _render_models(self, by_model):
        for ch in self.models.winfo_children():
            ch.destroy()
        items = sorted(by_model.items(), key=lambda kv: kv[1], reverse=True)
        mx = items[0][1] if items else 1
        if not items:
            tk.Label(self.models, text="데이터 없음", bg=PANEL, fg=MUT, font=MONO).pack(anchor="w")
            return
        for name, val in items:
            row = tk.Frame(self.models, bg=PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=_short_model(name), bg=PANEL, fg=TX, font=("Consolas", 9),
                     width=20, anchor="w").pack(side="left")
            tk.Label(row, text=_fmt_n(val), bg=PANEL, fg=MUT, font=("Consolas", 9),
                     width=12, anchor="e").pack(side="right")
            track = tk.Canvas(row, height=7, bg="#0a0c11", highlightthickness=0)
            track.pack(side="left", fill="x", expand=True, padx=8)
            track.update_idletasks()
            tw = max(track.winfo_width(), 1)
            track.create_rectangle(0, 0, int(tw * (val / mx)), 7, fill=BLU, width=0)

    # ---------- 트레이 / 종료 ----------
    def _on_close(self):
        if self.tray:
            self.root.withdraw()
        else:
            self._quit()

    def _show(self):
        self.root.after(0, self.root.deiconify)

    def _quit(self):
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def _make_icon_image(self):
        img = Image.new("RGB", (64, 64), BG)
        d = ImageDraw.Draw(img)
        d.ellipse((16, 16, 48, 48), fill=ACC)
        return img

    def _start_tray(self):
        if not _HAVE_TRAY:
            return
        menu = pystray.Menu(
            pystray.MenuItem("열기", lambda: self._show(), default=True),
            pystray.MenuItem("종료", lambda: self.root.after(0, self._quit)),
        )
        self.tray = pystray.Icon("Refuel", self._make_icon_image(), "Refuel", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def run(self):
        self._start_tray()
        self.root.mainloop()


def main():
    RefuelApp().run()


if __name__ == "__main__":
    main()
