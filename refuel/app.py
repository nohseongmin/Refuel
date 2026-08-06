"""Refuel GUI - 에이전트별 아코디언 카드를 띄우는 다크 트레이 앱.

카드는 접으면 한 줄, 펼치면 상세. 스캔은 백그라운드 스레드(UI 안 멈춤),
알림 표시는 항상 메인 스레드(_tick)에서 한다.
"""
import os
import sys
import threading
import time
import logging
import queue
import json
import re
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
    """GitHub 최신 릴리스 태그 조회(읽기 전용). 실패 시 None."""
    req = urllib.request.Request(_RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Refuel"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r).get("tag_name")

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
# 잔디 5단계(연두→초록) + 안 쓴 날. 사용량이 5시간 한도 추정치의 몇 %인지에 비례.
GRASS = ["#161b26", "#d9f99d", "#a3e635", "#65d64f", "#3aba48", "#1a9c3c"]

F = "Malgun Gothic"   # __init__에서 자동선택으로 덮어씀
REFRESH_SECONDS = 20
_WD = ["월", "화", "수", "목", "금", "토", "일"]
_mutex_handle = None  # 단일 인스턴스 뮤텍스 참조 유지

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


def _enable_dpi():
    """고해상도에서 또렷하게. Tk 생성 전에 호출."""
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _single_instance():
    """네임드 뮤텍스로 중복 실행 방지(포트 안 엶). 이미 있으면 False."""
    global _mutex_handle
    try:
        from ctypes import windll
        _mutex_handle = windll.kernel32.CreateMutexW(None, False, "Refuel_SingleInstance_Mutex")
        return windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


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


_APP = None  # 현재 앱 인스턴스 참조(트레이 폴백용)
_notify_q = queue.Queue()  # 워커 스레드 → 메인 스레드 알림 전달 큐


def _deliver(title, msg):
    """실제 화면 알림 표시. 반드시 Tk 메인 스레드에서 호출할 것.

    자체 토스트는 트레이 상주(root withdraw) 상태에서 안 그려져 쓰지 않는다.
    """
    channel = None
    if _HAVE_TOAST:                       # 1순위: 윈도우 네이티브 토스트
        try:
            n = Notification(app_id="Refuel", title=title, msg=msg)
            n.set_audio(audio.Default, loop=False)
            n.show()
            channel = "winotify"
        except Exception as e:
            log.warning("winotify 실패: %s", e)
    if channel is None:                   # 2순위: 트레이 풍선(폴백)
        tray = getattr(_APP, "tray", None)
        if tray is not None:
            try:
                tray.remove_notification()
                tray.notify(msg, title)
                channel = "tray"
            except Exception as e:
                log.warning("트레이 알림 실패: %s", e)
    log.info("알림 표시: %s [%s]", title, channel or "실패-표시안됨")


def _drain_notifications():
    """큐에 쌓인 알림을 실제로 표시. Tk 메인 스레드(_tick)에서만 호출된다."""
    while True:
        try:
            title, msg = _notify_q.get_nowait()
        except queue.Empty:
            break
        _deliver(title, msg)


def _notify(title, msg, phone=True):
    """어느 스레드에서든 안전. 화면 표시는 큐에 넣어 메인 스레드(_tick)가 꺼내 처리한다.
    (워커 스레드에서 직접 띄우면 풍선이 조용히 안 뜬다.)
    phone=False면 PC에만 표시 — 폰은 예약 발송이 담당하므로 중복을 막는다."""
    log.info("알림: %s - %s", title, msg)
    if phone:
        try:
            sync.post_alert(title, msg)
        except Exception as e:
            log.warning("폰 알림 실패: %s", e)
    if getattr(_APP, "root", None) is not None:
        _notify_q.put((title, msg))     # 메인 스레드 _tick 이 꺼내 표시
    else:
        _deliver(title, msg)            # 앱/메인루프 이전(단일 인스턴스 안내 등)


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
        log.warning("autostart 설정 실패: %s", e)


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
    """1일 미만이면 HH:MM:SS, 이상이면 'N일 HH:MM:SS'."""
    if sec is None:
        return "--:--:--"
    sec = max(0, int(sec))
    days, rem = divmod(sec, 86400)
    base = f"{rem // 3600:02d}:{rem % 3600 // 60:02d}:{rem % 60:02d}"
    return f"{days}일 {base}" if days else base


# ---------------- 위젯 헬퍼 (테마 반복 제거) ----------------
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
    """복사만 가능한 입력칸(페어링 URL·토픽 표시용)."""
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
    """에이전트 1개 카드 (접힘=헤더 한 줄, 펼침=상세)."""

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
        self.hstreak = _lbl(h, "", size=9)      # 접힌 상태에서도 연속일수는 보이게
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

        # 잔디가 이 카드의 주인공 — 카운트다운 바로 밑, 카드 폭을 꽉 채운다.
        self.streak_lbl = _lbl(d, "", fg=TX, size=10, bold=True)
        self.streak_lbl.pack(anchor="w", padx=16, pady=(0, 4))
        self._streak_data = None
        self.grass = tk.Canvas(d, height=80, bg=PANEL, highlightthickness=0)
        self.grass.pack(fill="x", padx=16, pady=(0, 4))
        self.grass.bind("<Configure>", lambda e: self._render_grass(self._streak_data))
        legend = tk.Frame(d, bg=PANEL)
        legend.pack(anchor="w", padx=16, pady=(0, 12))
        _lbl(legend, "적음", size=8).pack(side="left", padx=(0, 4))
        for c in GRASS[1:]:
            tk.Canvas(legend, width=9, height=9, bg=c,
                      highlightthickness=0).pack(side="left", padx=1)
        _lbl(legend, "많음 · 진할수록 한도까지 사용", size=8).pack(side="left", padx=(4, 0))

        self.sub = _lbl(d, "", size=10)
        self.sub.pack(anchor="w", padx=16, pady=(0, 2))
        self.bd = _lbl(d, "")
        self.bd.pack(anchor="w", padx=16, pady=(0, 2))
        self.wk = _lbl(d, "")
        self.wk.pack(anchor="w", padx=16, pady=(0, 4))
        self.bar = tk.Canvas(d, height=8, bg=TRACK, highlightthickness=0)
        self.bar.pack(fill="x", padx=16, pady=(2, 12))

        _lbl(d, "최근 7일").pack(anchor="w", padx=16, pady=(0, 4))
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
            extra = f" · 추정한도 {int(b['ratio'] * 100)}%" if b["ratio"] is not None else ""
            self.sub.config(text=f"5시간 리셋 {b['reset_at'].strftime('%H:%M')} · 윈도우 {_fmt_n(b['tokens'])} 토큰{extra}")
            self.bd.config(text=f"오늘 {_fmt_n(a['today_tokens'])} · 입력 {_fmt_short(b['inp'])} · 출력 {_fmt_short(b['out'])} · 캐시 {_fmt_short(b['cache'])}")
        else:
            self.reset_at = None
            self.usage_ratio = None
            self.bd.config(text=f"오늘 {_fmt_n(a['today_tokens'])}")
            if wk_over:
                self.primary = "weekly"
                self.sub.config(text="주간 한도 도달 - 풀릴 때까지 대기")
            else:
                self.primary = "full"
                self.sub.config(text="활성 윈도우 없음 · 주간도 여유")
        if self.wk_reset:
            days = (self.wk_rem or 0) // 86400
            when = f"리셋 {_WD[self.wk_reset.weekday()]} {self.wk_reset.strftime('%H:%M')}"
            rtxt = f" · 추정 {int(self.wk_ratio * 100)}%" if self.wk_ratio is not None else ""
            self.wk.config(text=f"주간 {_fmt_n(wk.get('tokens'))} 토큰 · {when} (D-{days}){rtxt}")
        st = a.get("streak") or {}
        cur, best = st.get("current", 0), st.get("best", 0)
        # Tk는 이모지를 단색으로 그려서 뭉개진다 → PC는 글자만 쓴다(폰은 이모지 그대로).
        self.hstreak.config(text=f"연속 {cur}일" if cur else "", fg=self.app.accent())
        self.streak_lbl.config(
            text=f"{cur}일 연속 사용 · 최고 {best}일" if cur else f"연속 기록 없음 · 최고 {best}일",
            fg=self.app.accent() if cur else MUT)
        self._render_grass(st)
        self._render_daily(a.get("daily", []))

    def _render_grass(self, streak):
        """깃허브 잔디식 달력. 열=주, 행=요일(월~일). 오늘이 마지막 칸.

        칸 크기는 캔버스 폭에서 역산해 가로를 꽉 채운다(창 크기 따라 다시 그림).
        """
        self._streak_data = streak
        self.grass.delete("all")
        levels = (streak or {}).get("levels") or ""
        if not levels:
            return
        try:
            start = datetime.strptime(streak["from"], "%Y-%m-%d").date()
        except (KeyError, ValueError, TypeError):
            return
        pad = start.weekday()          # 첫 칸이 무슨 요일인지에 맞춰 첫 열을 비운다
        cols = -(-(len(levels) + pad) // 7)
        cell = max(4.0, self.grass.winfo_width() / cols)
        size = cell - max(1, round(cell * 0.15))
        height = int(cell * 7)
        if self.grass.winfo_height() != height:
            self.grass.config(height=height)   # 칸 크기에 맞춰 캔버스 높이도 같이
        for i, ch in enumerate(levels):
            col, row = divmod(i + pad, 7)
            x, y = col * cell, row * cell
            fill = GRASS[int(ch)] if ch.isdigit() and int(ch) < len(GRASS) else GRASS[0]
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
            tag = "오늘" if d == today else f"{d.month:02d}/{d.day:02d} {_WD[d.weekday()]}"
            _lbl(row, tag, fg=TX, width=9, anchor="w").pack(side="left")
            _lbl(row, _fmt_short(v), width=7, anchor="e").pack(side="right")
            tr = tk.Canvas(row, height=7, bg=TRACK, highlightthickness=0)
            tr.pack(side="left", fill="x", expand=True, padx=8)
            tr.update_idletasks()
            tw = max(tr.winfo_width(), 1)
            col = self.app.accent() if d == today else BLU
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
        acc = self.app.accent()
        warn = core.CONFIG["warn_ratio"]
        self.dot.config(fg=acc)
        self.wk.config(fg=DNG if (self.wk_ratio is not None and self.wk_ratio >= warn) else MUT)
        now = datetime.now(timezone.utc).astimezone()

        if self.primary == "5h":
            rem = max(0, int((self.reset_at - now).total_seconds()))
            over = self.usage_ratio is not None and self.usage_ratio >= warn
            col = DNG if over else (WARN if rem <= core.CONFIG["reset_soon_min"] * 60 else acc)
            self._set_count(_fmt_dur(rem), col)
            self._set_bars((core.SESSION_WINDOW_SEC - rem) / core.SESSION_WINDOW_SEC, self.usage_ratio, col)
            return rem
        if self.primary == "weekly":
            rem = max(0, int((self.wk_reset - now).total_seconds())) if self.wk_reset else 0
            self._set_count(_fmt_dur(rem), DNG)
            self._set_bars(1 - rem / core.WEEKLY_WINDOW_SEC, self.wk_ratio, DNG)
            return rem
        self._set_count("충전완료", acc)
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
        _lbl(top, "● Refuel", fg=self.accent(), size=13, bold=True, bg=BG).pack(side="left")
        _btn(top, "⚙", self._open_settings, size=11).pack(side="right", padx=(8, 0))
        self.meta = _lbl(top, "", bg=BG)
        self.meta.pack(side="right")
        # 잔디가 자리를 크게 차지해 '최근 7일'이 아래로 밀린다 → 휠로 내려볼 수 있게 감싼다.
        self.scroll = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        self.scroll.pack(fill="both", expand=True)
        self.cards_box = tk.Frame(self.scroll, bg=BG)
        self._scroll_win = self.scroll.create_window((0, 0), window=self.cards_box, anchor="nw")
        self.cards_box.bind(
            "<Configure>", lambda e: self.scroll.config(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind(
            "<Configure>", lambda e: self.scroll.itemconfigure(self._scroll_win, width=e.width))
        # 포인터가 목록 위에 있을 때만 휠을 가로챈다(설정창 등 다른 창 스크롤을 뺏지 않도록).
        self.scroll.bind("<Enter>", lambda e: self.scroll.bind_all("<MouseWheel>", self._on_wheel))
        self.scroll.bind("<Leave>", lambda e: self.scroll.unbind_all("<MouseWheel>"))
        self.empty = _lbl(self.cards_box, "불러오는 중…", size=10, bg=BG)
        self.empty.pack(anchor="w", pady=4)

    def _on_wheel(self, e):
        first, last = self.scroll.yview()
        if first <= 0.0 and last >= 1.0:      # 다 보이면 스크롤하지 않는다
            return
        self.scroll.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # ---------- 상태 ----------
    def _check_notifications(self, s):
        """알림은 '초기화' 2종만 — 5시간 윈도우 리셋, 주간 사용량 리셋.
        (수치는 화면에서 보면 되니 알림은 '이제 다시 써도 된다'만 알린다.)"""
        live = set()
        for a in s.get("agents", []):
            live.add(a["id"])
            ns = self._ns.setdefault(a["id"], {"last_start": None, "wk_reset": None})
            nm, b = a["name"], a["block"]

            # --- 주간 사용량 초기화 ---
            wkr = (a.get("weekly") or {}).get("reset_at")
            if wkr is not None:
                if ns["wk_reset"] is None:          # 첫 관측은 기준만 잡고 알리지 않음
                    ns["wk_reset"] = wkr
                elif wkr != ns["wk_reset"]:         # 다음 리셋 시각이 밀림 = 주간이 초기화됨
                    _notify(f"{nm} 주간 초기화", "주간 사용량 한도가 초기화되었습니다.", phone=False)
                    ns["wk_reset"] = wkr

            if b is not None:
                # 리셋 시각 푸시를 ntfy에 예약(PC 꺼져 있어도 도착, 블록당 1회)
                try:
                    sync.schedule_refill(a["id"], nm, b["start"], b["reset_at"])
                except Exception as e:
                    log.warning("예약 발송 실패: %s", e)

            # --- 5시간 초기화 ---
            # 윈도우가 사라졌거나(만료) 새 윈도우로 바뀌었으면 직전 윈도우가 리셋된 것.
            # 첫 관측(last_start=None)은 기준만 잡고 알리지 않는다.
            # 폰 알림은 예약 발송이 담당(PC 꺼져도 도착) → 여기선 PC 화면만.
            start = b["start"] if b else None
            if ns["last_start"] is not None and start != ns["last_start"]:
                _notify(f"{nm} 재충전 완료", "5시간 사용량 한도가 초기화되었습니다.", phone=False)
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
                log.exception("스캔 실패")
            time.sleep(REFRESH_SECONDS)

    def _update_checker(self):
        """하루 1회 새 릴리스 확인. 발견 시 1회 알림 후 종료. 설정으로 끔 가능."""
        time.sleep(10)
        while True:
            if core.CONFIG.get("check_updates", True):
                try:
                    tag = _latest_release_tag()
                    if tag and _parse_ver(tag) > _parse_ver(__version__):
                        _notify("업데이트 있음", f"Refuel {tag} 나왔어 - {_RELEASES_URL}")
                        return
                except Exception as e:
                    log.info("업데이트 확인 실패(무시): %s", e)
            time.sleep(86400)

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
            self.empty.config(text="불러오는 중…" if not self.ready else "감지된 에이전트 없음")
            self.empty.pack(anchor="w", pady=4)
            return
        for a in agents:
            self.cards[a["id"]] = AgentCard(self, self.cards_box, a["id"], a["name"])
        if self.expanded_id not in self.cards:
            self.expanded_id = ids[0]
        self._apply_expand()

    def _tick(self):
        """1초마다. 여기서 예외가 나면 재예약이 끊겨 앱이 조용히 멎으므로 절대 새어나가지 않게 한다."""
        try:
            self._tick_body()
        except Exception:
            log.exception("_tick 실패(무시하고 계속)")
        finally:
            self.root.after(1000, self._tick)

    def _tick_body(self):
        _drain_notifications()          # 워커 스레드가 큐에 넣은 알림을 메인 스레드에서 표시
        with self.lock:
            s = dict(self.state)
            ready = self.ready
        if ready:
            on = "알림 ON" if (self.tray or _HAVE_TOAST) else "알림 OFF"
            self.meta.config(text=f"{on} · 이벤트 {_fmt_n(s.get('total_events'))}")
        agents = s.get("agents", [])    # 정렬(리셋 임박순)은 core.build_state가 끝냄
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

    # ---------- 설정창 ----------
    def _open_settings(self):
        if getattr(self, "_settings_win", None) and tk.Toplevel.winfo_exists(self._settings_win):
            self._settings_win.lift()
            return
        win = _dialog(self.root, "Refuel 설정")
        self._settings_win = win
        cfg = core.CONFIG

        _lbl(win, "주간 리셋", bg=BG).pack(anchor="w", pady=(2, 2))
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
        _lbl(wkrow, "시", bg=BG).pack(side="left")

        tray_var = tk.BooleanVar(value=cfg["minimize_to_tray"])
        auto_var = tk.BooleanVar(value=cfg["autostart"])
        upd_var = tk.BooleanVar(value=cfg.get("check_updates", True))
        for text, var in (("창 닫으면 트레이로 (우클릭 종료로만 완전 종료)", tray_var),
                          ("윈도우 시작 시 자동 실행 (트레이로 조용히)", auto_var),
                          ("새 버전 자동 확인 (GitHub, 하루 1회)", upd_var)):
            _chk(win, text, var).pack(anchor="w", pady=(10, 0))

        sync_var = tk.BooleanVar(value=cfg.get("sync_enabled", False))
        syncrow = tk.Frame(win, bg=BG)
        syncrow.pack(fill="x", pady=(10, 0))
        _chk(syncrow, "폰 연동 (베타) - 알림·상태를 폰으로", sync_var).pack(side="left")
        _btn(syncrow, "QR 페어링",
             lambda: (cfg.__setitem__("sync_enabled", True), sync_var.set(True),
                      core.save_config(), self._open_qr())).pack(side="right", ipadx=8, ipady=2)

        _lbl(win, "강조 색상", bg=BG).pack(anchor="w", pady=(12, 2))
        accrow = tk.Frame(win, bg=BG)
        accrow.pack(anchor="w")
        acc_var = tk.StringVar(value=cfg["accent"])
        for c in ["#46e08a", "#5a8dee", "#f5c451", "#f3766b", "#b388ff"]:
            tk.Button(accrow, bg=c, width=2, bd=2, relief="flat",
                      command=lambda c=c: acc_var.set(c)).pack(side="left", padx=3, pady=4)

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", pady=(18, 0))
        _btn(btns, "테스트 알림",
             lambda: _notify("Refuel 테스트", "알림이 잘 보이면 성공!")).pack(side="left", ipady=4, ipadx=8)

        def save():
            try:
                cfg["weekly_reset_dow"] = _WD.index(dow_var.get())
                cfg["weekly_reset_hour"] = max(0, min(23, int(e_hour.get())))
            except ValueError:
                pass
            cfg["minimize_to_tray"] = tray_var.get()
            cfg["sync_enabled"] = sync_var.get()
            cfg["check_updates"] = upd_var.get()
            cfg["accent"] = acc_var.get()
            if auto_var.get() != cfg["autostart"]:
                cfg["autostart"] = auto_var.get()
                _set_autostart(cfg["autostart"])
            core.save_config()
            win.destroy()

        _btn(btns, "저장", save, fg=BG, bg=acc_var.get(), size=10,
             bold=True).pack(side="right", ipady=4, ipadx=20)

    # ---------- QR 페어링 ----------
    def _open_qr(self):
        url = sync.pair_url()
        alert_topic = sync.topic() + "-a"
        win = _dialog(self.root, "폰 페어링", pady=16)
        _lbl(win, "1) 폰 카메라로 QR 스캔 → 대시보드 열림", fg=TX, size=10, bg=BG).pack(anchor="w")
        try:
            import qrcode
            from PIL import ImageTk
            img = qrcode.make(url).resize((260, 260))
            self._qr_photo = ImageTk.PhotoImage(img)
            tk.Label(win, image=self._qr_photo, bg="white").pack(pady=10)
        except Exception as e:
            log.warning("QR 생성 실패: %s", e)
            _lbl(win, "(QR 모듈 없음 - 아래 링크를 직접 열기)", fg=WARN, bg=BG).pack(pady=6)
        _readonly(win, url, size=8).pack(fill="x", ipady=3)
        _lbl(win, "2) 푸시 알림: 폰에 ntfy 앱 설치 후 아래 토픽 구독",
             fg=TX, size=10, bg=BG).pack(anchor="w", pady=(12, 2))
        _readonly(win, alert_topic).pack(fill="x", ipady=3)
        _lbl(win, "* 상태는 종단간 암호화(AES-GCM) · 나가는 데이터는 토큰 수·시각뿐",
             size=8, bg=BG).pack(anchor="w", pady=(8, 0))

        def rotate():
            sync.rotate()
            win.destroy()
            self._open_qr()
            _notify("페어링 재발급", "이전 QR·토픽은 무효화됨. 폰에서 다시 스캔해줘.")

        _btn(win, "토픽·키 재발급 (유출 의심 시)", rotate,
             fg=WARN).pack(anchor="w", pady=(10, 0), ipadx=8, ipady=3)

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
            log.warning("트레이 시작 실패: %s", e)
            self.tray = None

    def _consent_gate(self):
        """최초 실행 시 면책조항 동의를 받는다. 동의하지 않으면 False."""
        if core.has_consented():
            return True
        self.root.deiconify()
        win = _dialog(self.root, "Refuel — 사용 동의", padx=22)
        win.transient(self.root)
        result = {"ok": False}

        _lbl(win, "사용 전 동의", fg=self.accent(), size=14, bold=True, bg=BG).pack(anchor="w")

        # 버튼 영역을 '먼저' 하단에 고정한다. 텍스트를 먼저 넣으면 창이 커지면서
        # 화면 밖으로 밀려 동의 버튼이 안 보이는 문제가 생긴다.
        agree = tk.BooleanVar(value=False)
        row = tk.Frame(win, bg=BG)
        row.pack(side="bottom", fill="x", pady=(12, 0))
        chk = _chk(win, "위 내용을 읽었으며 이에 동의합니다.", agree)
        chk.config(font=_font(10))
        chk.pack(side="bottom", anchor="w")

        btn = _btn(row, "동의하고 시작", None, fg=MUT, bg=BORDER, size=10, bold=True,
                   state="disabled", cursor="")
        btn.pack(side="right", ipadx=16, ipady=5)
        _btn(row, "동의 안 함 (종료)", win.destroy, fg=MUT, bg=BG).pack(side="left")

        def toggle():
            if agree.get():
                btn.config(state="normal", bg=self.accent(), fg=BG, cursor="hand2")
            else:
                btn.config(state="disabled", bg=BORDER, fg=MUT, cursor="")

        chk.config(command=toggle)

        # 남은 공간에 본문(스크롤 가능). 화면이 작아도 버튼은 항상 보인다.
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

        # 창이 화면을 벗어나지 않게 크기 제한 + 중앙 배치
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
            log.info("면책조항 미동의 - 종료")
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
        _notify("Refuel", "이미 실행 중이에요.")
        return
    RefuelApp(start_hidden="--minimized" in sys.argv).run()


if __name__ == "__main__":
    main()
