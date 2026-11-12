"""AI 남은 사용량 위젯 - Claude / Codex / Antigravity (Windows / macOS, tkinter)

- Claude : ~/.claude/.credentials.json (macOS는 키체인) 토큰으로 api.anthropic.com/api/oauth/usage 조회
- Codex  : ~/.codex/sessions/**/*.jsonl 에 기록된 최신 rate_limits 읽기 (로컬 파일만 사용)
- Antigravity : agy(Antigravity CLI) 로그인 정보(OS 보안 저장소)로 모델 그룹별 5시간 쿼터 조회 (비공식 API)
조작: 드래그=이동, 더블클릭=새로고침, 우클릭=메뉴(표시할 AI 선택 / 간소화 모드 / 항상 위 / 종료)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
REFRESH_MS = 60_000
FULL_WIDTH = 275  # 전체 모드 가로폭(px) - 기존 약 410px 의 2/3
IS_MAC = sys.platform == "darwin"
KR = "Apple SD Gothic Neo" if IS_MAC else "Malgun Gothic"
UI = "Helvetica Neue" if IS_MAC else "Segoe UI"

THEMES = {
    "dark": dict(BG="#1e1f24", CARD="#2a2c33", FG="#f2f3f5", DIM="#8b8f99", BAR="#3a3d46", BORDER="#3a3d46",
                 GREEN="#4ade80", YELLOW="#facc15", RED="#f87171"),
    "light": dict(BG="#f3f3f3", CARD="#ffffff", FG="#1a1a1a", DIM="#5f6368", BAR="#d9dce1", BORDER="#c8cbd0",
                  GREEN="#15803d", YELLOW="#b45309", RED="#dc2626"),
}
BG = CARD = FG = DIM = BAR = BORDER = GREEN = YELLOW = RED = ""


def apply_theme(name):
    """위젯 창 모드의 색 테마(dark/light)를 전역 색 변수에 적용"""
    globals().update(THEMES.get(name, THEMES["dark"]))


apply_theme("dark")  # 작업표시줄 막대는 자체 로직(시스템 테마/설정)으로 색을 정한다
SHORT = {"5시간": "5h", "주간": "주", "Gemini 5시간": "Gem", "Claude·GPT 5시간": "Cl·GPT"}


# ---------- 공통 ----------
def http_json(url, token, body=None, headers=None):
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def err_dict(e):
    """예외 -> {"error": 설명, "short": 막대용 짧은 표시, "retry": 재시도 대기(초)}"""
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return {"error": "토큰 만료 - Claude Code·agy 실행 시 자동 갱신", "short": "만료", "retry": 120}
        if e.code == 429:
            try:
                wait = int(e.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            return {"error": "요청 제한(429) - 잠시 후 자동 재시도", "short": "대기", "retry": max(wait, 300)}
        return {"error": f"서버 오류 HTTP {e.code}", "short": "오류", "retry": 120}
    if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
        return {"error": "네트워크 연결 확인", "short": "연결", "retry": 60}
    return {"error": str(e)[:50], "short": "오류", "retry": 120}


# ---------- 데이터 조회: {"rows": [(라벨, 남은%, 갱신시각)]} 또는 {"error": str} ----------
def read_claude_oauth():
    f = HOME / ".claude" / ".credentials.json"
    if f.exists():
        raw = f.read_text(encoding="utf-8")
    elif IS_MAC:  # macOS: Claude Code 는 로그인 정보를 키체인에 저장
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, check=True).stdout
    else:
        raise FileNotFoundError("Claude Code 로그인 정보 없음")
    return json.loads(raw)["claudeAiOauth"]


def fetch_claude():
    try:
        oauth = read_claude_oauth()
        exp = oauth.get("expiresAt")
        if exp and exp / 1000 < time.time():  # 만료된 토큰으로는 호출하지 않음 (Claude Code 가 갱신하면 자동 복구)
            return {"error": "토큰 만료 - Claude Code 실행 시 자동 갱신", "short": "만료", "retry": 60}
        d = http_json("https://api.anthropic.com/api/oauth/usage", oauth["accessToken"],
                      headers={"anthropic-beta": "oauth-2025-04-20"})
        rows = []
        for label, key in (("5시간", "five_hour"), ("주간", "seven_day")):
            v = d.get(key)
            if v:
                reset = datetime.fromisoformat(v["resets_at"]).astimezone()
                rows.append((label, max(0.0, 100.0 - float(v["utilization"])), reset))
        return {"rows": rows}
    except Exception as e:  # noqa: BLE001
        return err_dict(e)


SHARED_CLAUDE = HOME / ".claude" / "ai-usage-cache.json"  # Claude Code 플러그인(ai-usage)과 공유하는 캐시


def fetch_claude_shared():
    """위젯·플러그인·여러 세션이 각자 조회하면 요청 제한(429)에 걸리므로, 최근 조회 결과를 파일로 공유한다."""
    now = time.time() * 1000
    sh = {}
    try:
        sh = json.loads(SHARED_CLAUDE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    view = sh.get("view")
    if view and (now < sh.get("until", 0) or now - sh.get("t", 0) < 180_000):
        if view.get("rows"):
            d = {"rows": [(r["label"], float(r["remain"]),
                           datetime.fromtimestamp(r["resetMs"] / 1000).astimezone()) for r in view["rows"]]}
            if view.get("stale"):
                d["stale"] = view["stale"]
                d["short"] = view.get("short", "")
            return d
        return {"error": view.get("error", "오류"), "short": view.get("short", ""),
                "retry": max(0, (sh.get("until", 0) - now) / 1000)}
    d = fetch_claude()
    try:
        good = sh.get("good")
        if "rows" in d:
            rows = [{"label": l, "remain": r, "resetMs": int(t.timestamp() * 1000)} for l, r, t in d["rows"]]
            view = good = {"rows": rows}
            until = 0
        else:
            view = {"error": d["error"], "short": d.get("short", "")}
            if good and good.get("rows"):
                view = {"rows": good["rows"], "stale": d["error"], "short": d.get("short", "")}
            until = now + d.get("retry", 0) * 1000
        SHARED_CLAUDE.write_text(json.dumps({"t": now, "until": until, "view": view, "good": good}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return d


def fetch_codex_local():
    try:
        files = sorted((HOME / ".codex" / "sessions").rglob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:6]
        best_ts, best = "", None
        for f in files:
            with open(f, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 400_000))
                lines = fh.read().decode("utf-8", "ignore").splitlines()
            for line in reversed(lines):
                if '"rate_limits"' not in line:
                    continue
                try:
                    o = json.loads(line)
                    rl = o["payload"]["rate_limits"]
                    if not rl or not rl.get("primary"):
                        continue
                except Exception:  # noqa: BLE001
                    continue
                if o["timestamp"] > best_ts:
                    best_ts, best = o["timestamp"], rl
                break
        if not best:
            return {"error": "Codex 사용 기록 없음"}
        now = datetime.now().timestamp()
        rows = []
        for label, w in (("5시간", best["primary"]), ("주간", best.get("secondary"))):
            if not w:
                continue
            used = 0.0 if w["resets_at"] < now else float(w["used_percent"])  # 기록 후 이미 갱신됨
            rows.append((label, max(0.0, 100.0 - used), datetime.fromtimestamp(w["resets_at"]).astimezone()))
        return {"rows": rows}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:50]}


def fetch_codex_live():
    """Codex 실시간 사용량 (ChatGPT 백엔드, Codex 앱이 쓰는 것과 같은 로그인 토큰). 비공식 API."""
    a = json.loads((HOME / ".codex" / "auth.json").read_text(encoding="utf-8"))
    t = a["tokens"]
    h = {"chatgpt-account-id": t["account_id"]} if t.get("account_id") else {}
    d = http_json("https://chatgpt.com/backend-api/wham/usage", t["access_token"], headers=h)
    rl = d["rate_limit"]
    rows = []
    for label, key in (("5시간", "primary_window"), ("주간", "secondary_window")):
        w = rl.get(key)
        if w:
            rows.append((label, max(0.0, 100.0 - float(w["used_percent"])),
                         datetime.fromtimestamp(w["reset_at"]).astimezone()))
    if not rows:
        raise ValueError("Codex 사용량 응답 없음")
    return {"rows": rows}


def fetch_codex():
    """실시간 조회 우선, 실패하면 로컬 세션 기록(마지막으로 Codex를 쓴 시점 기준)으로 대체"""
    try:
        return fetch_codex_live()
    except Exception as e:  # noqa: BLE001
        local = fetch_codex_local()
        if "rows" in local:
            reason = err_dict(e)["error"]
            return {"rows": local["rows"], "stale": f"실시간 조회 실패, 로컬 기록 기준 ({reason})"}
        return err_dict(e)


AGY_HEADERS = {"User-Agent": "antigravity"}  # 이 값이 없으면 프로젝트 정보가 비어 조회가 거부된다


def read_agy_token():
    """Antigravity CLI(agy) 로그인 정보: OS 보안 저장소(Windows 자격 증명 관리자 / macOS 키체인)의 gemini:antigravity"""
    if IS_MAC:
        raw = subprocess.run(["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                             capture_output=True, text=True, check=True).stdout.strip()
        if raw.startswith("go-keyring-base64:"):
            import base64
            raw = base64.b64decode(raw.split(":", 1)[1]).decode("utf-8")
    else:
        import ctypes
        from ctypes import wintypes as w

        class CRED(ctypes.Structure):
            _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD), ("TargetName", w.LPWSTR), ("Comment", w.LPWSTR),
                        ("LastWritten", w.FILETIME), ("CredentialBlobSize", w.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", w.DWORD),
                        ("AttributeCount", w.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", w.LPWSTR), ("UserName", w.LPWSTR)]
        p = ctypes.POINTER(CRED)()
        if not ctypes.windll.advapi32.CredReadW("gemini:antigravity", 1, 0, ctypes.byref(p)):
            raise FileNotFoundError("Antigravity 로그인 필요")
        try:
            c = p.contents
            raw = bytes(c.CredentialBlob[:c.CredentialBlobSize]).decode("utf-8")
        finally:
            ctypes.windll.advapi32.CredFree(p)
    return json.loads(raw)["token"]


def fetch_antigravity():
    """Antigravity CLI 모델 그룹별 남은 쿼터(5시간 창). 비공식 API라 구조가 바뀌면 동작하지 않을 수 있음."""
    try:
        try:
            tok = read_agy_token()
        except (FileNotFoundError, subprocess.CalledProcessError):
            return {"error": "Antigravity 로그인 필요 (agy 실행 후 Google 로그인)", "short": "로그인"}
        exp = re.sub(r"(\.\d{6})\d+", r"\1", tok.get("expiry", ""))
        if exp and datetime.fromisoformat(exp).timestamp() < time.time():  # 갱신은 agy 가 실행될 때 자동
            return {"error": "토큰 만료 - agy 를 한 번 실행하세요", "short": "만료", "retry": 120}
        t = tok["access_token"]
        err = None
        for host in ("daily-cloudcode-pa.googleapis.com", "cloudcode-pa.googleapis.com"):
            base = f"https://{host}/v1internal"
            try:
                load = http_json(f"{base}:loadCodeAssist", t, {"metadata": {"ideType": "ANTIGRAVITY"}}, headers=AGY_HEADERS)
                project = load.get("cloudaicompanionProject")
                if isinstance(project, dict):
                    project = project.get("id")
                q = http_json(f"{base}:fetchAvailableModels", t, {"project": project} if project else {}, headers=AGY_HEADERS)
                break
            except urllib.error.HTTPError as e:
                err = e
                if e.code in (401, 403, 429):
                    raise
        else:
            raise err
        groups = {}  # 그룹 -> (남은 비율 최소, 갱신시각)
        for mid, m in (q.get("models") or {}).items():
            qi = m.get("quotaInfo") or {}
            if qi.get("remainingFraction") is None or not qi.get("resetTime"):
                continue
            g = "Gemini" if mid.startswith("gemini") else "Claude·GPT" if mid.startswith(("claude", "gpt")) else None
            if not g:
                continue
            frac = float(qi["remainingFraction"])
            if g not in groups or frac < groups[g][0]:
                groups[g] = (frac, qi["resetTime"])
        rows = []
        for g in ("Gemini", "Claude·GPT"):
            if g in groups:
                frac, rt = groups[g]
                rows.append((f"{g} 5시간", frac * 100, datetime.fromisoformat(rt.replace("Z", "+00:00")).astimezone()))
        return {"rows": rows} if rows else {"error": "쿼터 정보 없음", "short": "없음"}
    except Exception as e:  # noqa: BLE001
        return err_dict(e)


_state = {}


def cached(name, fn, interval):
    """오류가 나도 마지막 정상값을 유지(stale)하고, 429 등은 대기 후 재시도. 수동 새로고침도 최소 간격은 지킴."""
    st = _state.setdefault(name, {"t": 0.0, "until": 0.0, "view": None, "good": None})
    now = time.time()
    if st["view"] is not None and (now < st["until"] or now - st["t"] < interval):
        return st["view"]
    st["t"] = now
    d = fn()
    if "error" in d:
        st["until"] = now + d.get("retry", 0)
        if st["good"]:
            d = {"rows": st["good"]["rows"], "stale": d["error"], "short": d.get("short", "")}
    else:
        st["good"] = d
        st["until"] = 0.0
    st["view"] = d
    return d


PROVIDERS = {
    "Claude": lambda: cached("Claude", fetch_claude_shared, 180),
    "Codex": lambda: cached("Codex", fetch_codex, 120),
    "Antigravity": lambda: cached("Antigravity", fetch_antigravity, 180),
}


# ---------- 표시 보조 ----------
def color_for(remain):
    return GREEN if remain >= 40 else YELLOW if remain >= 15 else RED


def short_reset(dt, tilde=False):
    """24시간 이내면 HH:MM, 아니면 M/D HH:MM"""
    if not dt:
        return ""
    near = (dt - datetime.now(timezone.utc).astimezone()).total_seconds() < 86400
    s = f"{dt:%H:%M}" if near else f"{dt.month}/{dt.day} {dt:%H:%M}"
    return ("~" if tilde else "") + s


# ---------- UI ----------
class Row:
    def __init__(self, parent):
        self.f = tk.Frame(parent, bg=CARD)
        top = tk.Frame(self.f, bg=CARD)
        top.pack(fill="x", padx=6, pady=(3, 1))
        self.label = tk.Label(top, text="", bg=CARD, fg=DIM, font=(KR, 8))
        self.label.pack(side="left")
        self.pct = tk.Label(top, text="--%", bg=CARD, fg=FG, font=(UI, 10, "bold"))
        self.pct.pack(side="right")
        self.reset = tk.Label(top, text="", bg=CARD, fg=DIM, font=(KR, 8))
        self.reset.pack(side="right", padx=(0, 6))
        self.bar = tk.Canvas(self.f, width=10, height=4, bg=BAR, highlightthickness=0)
        self.bar.pack(fill="x", padx=6, pady=(0, 4))
        self.fill = self.bar.create_rectangle(0, 0, 0, 4, width=0, fill=GREEN)
        self.val = 0.0

    def set(self, label, remain, dt):
        self.val = remain
        self.label.config(text=label)
        self.pct.config(text=f"{remain:.0f}%", fg=color_for(remain))
        self.reset.config(text=short_reset(dt))
        self.draw()

    def draw(self):
        self.bar.coords(self.fill, 0, 0, self.bar.winfo_width() * self.val / 100, 4)
        self.bar.itemconfig(self.fill, fill=color_for(self.val))


class Section:
    def __init__(self, parent, name):
        self.f = tk.Frame(parent, bg=BG)
        self.title = tk.Frame(self.f, bg=BG)
        self.title.pack(fill="x", padx=10, pady=(5, 0))
        tk.Label(self.title, text=name, bg=BG, fg=FG, font=(UI, 9, "bold")).pack(side="left")
        self.rows = [Row(self.f), Row(self.f)]
        self.err = tk.Label(self.f, text="", bg=BG, fg=RED, font=(KR, 8), anchor="w", wraplength=230, justify="left")

    def show(self, data):
        for r in self.rows:
            r.f.pack_forget()
        self.err.pack_forget()
        if "error" in data:
            self.err.config(text="⚠ " + data["error"])
            self.err.pack(fill="x", padx=10, pady=(0, 2))
            return
        for r, (label, remain, dt) in zip(self.rows, data["rows"]):
            r.f.pack(fill="x", padx=6, pady=2)
            r.set(label, remain, dt)
        if data.get("stale"):
            self.err.config(text="⚠ 이전 값 · " + data["stale"])
            self.err.pack(fill="x", padx=10, pady=(0, 2))

    def redraw(self):
        for r in self.rows:
            r.draw()


class CompactRow:
    """간소화 모드 한 줄: 이름  5h 85% ~22:30  주 94% ~11/17 13:00"""

    def __init__(self, parent, name):
        self.f = tk.Frame(parent, bg=BG)
        tk.Label(self.f, text=name, bg=BG, fg=FG, font=(UI, 9, "bold"), width=6, anchor="w").pack(side="left")
        self.cells = []
        for _ in range(2):
            self.cells.append((tk.Label(self.f, bg=BG, fg=DIM, font=(KR, 8)),
                               tk.Label(self.f, bg=BG, fg=FG, font=(UI, 10, "bold")),
                               tk.Label(self.f, bg=BG, fg=DIM, font=(KR, 8))))
        self.err = tk.Label(self.f, text="", bg=BG, fg=RED, font=(KR, 8))

    def show(self, data):
        for cell in self.cells:
            for w in cell:
                w.pack_forget()
        self.err.pack_forget()
        if "error" in data:
            self.err.config(text="⚠ " + data["error"][:22])
            self.err.pack(side="left", padx=4)
            return
        for (t, p, r), (label, remain, dt) in zip(self.cells, data["rows"]):
            t.config(text=SHORT.get(label, label))
            p.config(text=f"{remain:.0f}%", fg=color_for(remain))
            r.config(text=short_reset(dt, tilde=True))
            t.pack(side="left", padx=(4, 1))
            p.pack(side="left")
            r.pack(side="left", padx=(1, 0))


CFG_PATH = Path(os.environ.get("APPDATA", str(HOME))) / "UsageWidget" / "config.json"


def load_cfg():
    try:
        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    # 예전 설정의 Gemini(CLI 구독 종료) -> Antigravity
    cfg["enabled"] = [("Antigravity" if n == "Gemini" else n) for n in cfg.get("enabled", [])] or cfg.get("enabled", [])
    return cfg


def save_cfg(cfg):
    try:
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CFG_PATH.write_text(json.dumps(cfg), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


class Widget(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self.compact_mode = bool(self.cfg.get("compact", False))
        enabled = self.cfg.get("enabled", ["Claude", "Codex"])
        self.vars = {n: tk.BooleanVar(value=n in enabled) for n in PROVIDERS}
        if not any(v.get() for v in self.vars.values()):
            self.vars["Claude"].set(True)

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG, highlightbackground=BORDER, highlightthickness=1)
        self.geometry(f"+{self.cfg.get('x', 60)}+{self.cfg.get('y', 60)}")
        self.full = tk.Frame(self, bg=BG)
        self.compact = tk.Frame(self, bg=BG)
        self.sections = {n: Section(self.full, n) for n in PROVIDERS}
        self.crows = {n: CompactRow(self.compact, n) for n in PROVIDERS}
        self.updated = None
        self.data = {}
        self.topmost = True

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="표시할 AI 선택", state="disabled")
        for n in PROVIDERS:
            menu.add_checkbutton(label=n, variable=self.vars[n], command=self.on_select)
        menu.add_separator()
        menu.add_command(label="간소화/전체 모드 전환", command=self.toggle_mode)
        tmenu = tk.Menu(menu, tearoff=0)
        self.theme_var = tk.StringVar(value=self.cfg.get("theme", "dark"))
        for key, label in (("light", "밝은 테마"), ("dark", "다크 테마")):
            tmenu.add_radiobutton(label=label, value=key, variable=self.theme_var, command=self.change_theme)
        menu.add_cascade(label="테마", menu=tmenu)
        menu.add_command(label="지금 새로고침", command=self.refresh)
        menu.add_command(label="항상 위 토글", command=self.toggle_top)
        menu.add_command(label="상태표시줄 모드로 전환" if IS_MAC else "작업표시줄 모드로 전환",
                         command=lambda: self.to_display("taskbar"))
        menu.add_separator()
        menu.add_command(label="종료", command=self.destroy)
        self.menu = menu

        self.bind_all("<ButtonPress-1>", self._drag_start, add="+")
        self.bind_all("<B1-Motion>", self._drag, add="+")
        self.bind_all("<ButtonRelease-1>", lambda e: self.save(), add="+")
        self.bind_all("<Double-Button-1>", lambda e: self.refresh(), add="+")
        self.bind_all("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root), add="+")

        self.layout()
        self.after(100, self.refresh)

    def enabled(self):
        return [n for n in PROVIDERS if self.vars[n].get()]

    def layout(self):
        for w in (*self.full.winfo_children(), *self.compact.winfo_children()):
            w.pack_forget()
        self.full.pack_forget()
        self.compact.pack_forget()
        txt = self.updated.cget("text") if self.updated else ""
        if self.updated:
            self.updated.destroy()
        en = self.enabled()
        self.updated = tk.Label(self.sections[en[0]].title, text=txt, bg=BG, fg=DIM, font=(KR, 7))
        self.updated.pack(side="right")
        tk.Frame(self.compact, bg=BG, height=4).pack()
        for n in en:
            self.sections[n].f.pack(fill="x")
            self.crows[n].f.pack(fill="x", padx=8, pady=2)
        tk.Frame(self.full, bg=BG, width=FULL_WIDTH, height=3).pack()  # 전체 모드 가로폭
        tk.Frame(self.compact, bg=BG, height=4).pack()
        (self.compact if self.compact_mode else self.full).pack(fill="x")
        for n in en:
            if n in self.data:
                self.sections[n].show(self.data[n])
                self.crows[n].show(self.data[n])
        self.update_idletasks()
        self.redraw_bars()

    def on_select(self):
        if not self.enabled():  # 최소 1개는 유지
            self.vars["Claude"].set(True)
        self.save()
        self.layout()
        self.refresh()

    def toggle_mode(self):
        self.compact_mode = not self.compact_mode
        self.save()
        self.layout()

    def save(self):
        self.cfg.update(x=self.winfo_x(), y=self.winfo_y(), compact=self.compact_mode, enabled=self.enabled())
        save_cfg(self.cfg)

    def _drag_start(self, e):
        self._ox, self._oy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _drag(self, e):
        if hasattr(self, "_ox"):
            self.geometry(f"+{e.x_root - self._ox}+{e.y_root - self._oy}")

    def change_theme(self):
        self.cfg["theme"] = self.theme_var.get()
        self.save()
        relaunch("widget")  # 색은 생성 시점에 정해지므로 다시 실행
        self.destroy()

    def to_display(self, display):
        self.save()
        relaunch(display)
        self.destroy()

    def toggle_top(self):
        self.topmost = not self.topmost
        self.attributes("-topmost", self.topmost)

    def redraw_bars(self):
        for s in self.sections.values():
            s.redraw()

    # 네트워크 조회는 별도 스레드 (UI 멈춤 방지)
    def refresh(self):
        if getattr(self, "_busy", False):
            return
        self._busy = True
        names = self.enabled()

        self._result = None

        def work():  # 스레드에서는 tk 를 건드리지 않고 결과만 저장
            self._result = {n: PROVIDERS[n]() for n in names}

        def poll():
            if self._result is None:
                self.after(100, poll)
            else:
                self.done(self._result)

        threading.Thread(target=work, daemon=True).start()
        self.after(100, poll)

    def done(self, res):
        self._busy = False
        self.data.update(res)
        for n, d in res.items():
            self.sections[n].show(d)
            self.crows[n].show(d)
        self.updated.config(text=f"{datetime.now():%H:%M:%S}")
        self.update_idletasks()
        self.redraw_bars()
        if getattr(self, "_job", None):
            self.after_cancel(self._job)
        self._job = self.after(REFRESH_MS, self.refresh)


# ---------- 작업표시줄 모드 (Windows): 날씨/뉴스 위젯처럼 작업표시줄 위에 얹는 막대 ----------
def taskbar_geometry():
    """(작업표시줄 left, top, right, bottom, 알림영역 left) - 실패 시 None"""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    tray = u.FindWindowW("Shell_TrayWnd", None)
    if not tray:
        return None
    r = wintypes.RECT()
    u.GetWindowRect(tray, ctypes.byref(r))
    notify_left = r.right - 300
    nw = u.FindWindowExW(tray, None, "TrayNotifyWnd", None)
    if nw:
        n = wintypes.RECT()
        u.GetWindowRect(nw, ctypes.byref(n))
        if n.right > n.left:
            notify_left = n.left
    return r.left, r.top, r.right, r.bottom, notify_left


def taskbar_is_light():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except Exception:  # noqa: BLE001
        return False


class TaskbarBar(tk.Tk):
    """작업표시줄 높이에 맞춘 막대. 좌/우 위치 선택, 가로 드래그 가능, 우클릭 메뉴."""

    def __init__(self):
        try:  # 실제 픽셀 좌표로 작업표시줄과 맞추기 위해 DPI 인식
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
        super().__init__()
        self.cfg = load_cfg()
        pref = self.cfg.get("theme_bar", "auto")
        self.light = taskbar_is_light() if pref == "auto" else pref == "light"
        self.bg = "#f3f3f3" if self.light else "#202020"  # 이 색을 투명 처리(글자 가장자리도 이 색과 섞임)
        self.fg = "#1a1a1a" if self.light else "#f2f3f5"
        self.dim = "#5f6368" if self.light else "#9aa0a6"
        self.colors = ({GREEN: "#15803d", YELLOW: "#b45309", RED: "#dc2626"} if self.light
                       else {GREEN: GREEN, YELLOW: YELLOW, RED: RED})
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.bg)
        self.attributes("-transparentcolor", self.bg)  # 배경 투명
        self.data = {}
        self.cols = {}
        self.tip = None
        self._drag_dx = None

        self.vars = {n: tk.BooleanVar(value=n in self.cfg.get("enabled", ["Claude", "Codex"])) for n in PROVIDERS}
        if not any(v.get() for v in self.vars.values()):
            self.vars["Claude"].set(True)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="표시할 AI 선택", state="disabled")
        for n in PROVIDERS:
            menu.add_checkbutton(label=n, variable=self.vars[n], command=self.on_select)
        menu.add_separator()
        menu.add_command(label="위치: 왼쪽 끝", command=lambda: self.set_side("left"))
        menu.add_command(label="위치: 오른쪽 (알림영역 옆)", command=lambda: self.set_side("right"))
        menu.add_command(label="지금 새로고침", command=self.refresh)
        tmenu = tk.Menu(menu, tearoff=0)
        self.bar_theme = tk.StringVar(value=self.cfg.get("theme_bar", "auto"))
        for key, label in (("auto", "자동 (작업표시줄 테마 따라감)"), ("light", "밝은 테마"), ("dark", "다크 테마")):
            tmenu.add_radiobutton(label=label, value=key, variable=self.bar_theme, command=self.change_bar_theme)
        menu.add_cascade(label="테마", menu=tmenu)
        menu.add_separator()
        menu.add_command(label="위젯 창 모드로 전환", command=lambda: self.switch("widget"))
        menu.add_command(label="종료", command=self.destroy)
        self.menu = menu

        # 배경이 투명해 빈 곳은 클릭이 통과하므로, 잡고 끌 수 있는 작은 손잡이를 둔다
        # 손잡이: 세로 막대 | (불투명한 3px 선). 막대의 어느 곳이든 "끌지 않고 클릭"하면 메뉴가 열린다
        self.grip = tk.Frame(self, bg=self.dim, width=3, cursor="hand2")
        self.grip.pack(side="left", fill="y", padx=(4, 6), pady=10)
        self.inner = tk.Frame(self, bg=self.bg)
        self.inner.pack(side="left", expand=True, padx=(2, 8))
        self.bind_all("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root), add="+")
        self.bind_all("<ButtonPress-1>", self._drag_start, add="+")
        self.bind_all("<B1-Motion>", self._drag, add="+")
        self.bind_all("<ButtonRelease-1>", self._drag_end, add="+")
        self.bind_all("<Enter>", self._tip_show, add="+")
        self.bind_all("<Leave>", self._tip_hide, add="+")

        # 배경이 투명(color key)이라 빈 곳은 클릭이 통과한다. 알파 1% 의 받침 창이 그 클릭을 받는다
        self.hit = tk.Toplevel(self)
        self.hit.overrideredirect(True)
        self.hit.attributes("-topmost", True)
        self.hit.attributes("-alpha", 0.01)
        self.hit.configure(bg="black")

        self.build()
        self.update_idletasks()
        try:
            self.hit.update_idletasks()
            self.no_activate()
        except Exception:  # noqa: BLE001
            pass
        self.after(100, self.refresh)
        self.after(50, self.keep_on_top)

    # --- 구성 ---
    def enabled(self):
        return [n for n in PROVIDERS if self.vars[n].get()]

    def build(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.cols = {}
        for i, n in enumerate(self.enabled()):
            col = tk.Frame(self.inner, bg=self.bg)
            col.pack(side="left", padx=(0 if i == 0 else 12, 0))
            tk.Label(col, text=n, bg=self.bg, fg=self.dim, font=(UI, 9, "bold"), anchor="w").pack(fill="x")
            line = tk.Frame(col, bg=self.bg)
            line.pack(fill="x")
            self.cols[n] = line
        for n, d in self.data.items():
            if n in self.cols:
                self.fill(n, d)
        self.place_bar()

    def fill(self, n, d):
        line = self.cols[n]
        for w in line.winfo_children():
            w.destroy()
        if "error" in d or not d["rows"]:
            tk.Label(line, text="⚠ " + d.get("short", "확인 필요"), bg=self.bg, fg=self.colors[RED],
                     font=(KR, 9, "bold")).pack(side="left")
            return
        for label, remain, dt in d["rows"]:
            tk.Label(line, text=SHORT.get(label, label), bg=self.bg, fg=self.dim, font=(KR, 9, "bold")).pack(side="left", padx=(0, 2))
            tk.Label(line, text=f"{remain:.0f}%", bg=self.bg,
                     fg=self.dim if d.get("stale") else self.colors[color_for(remain)],
                     font=(UI, 11, "bold")).pack(side="left", padx=(0, 8))

    # --- 위치: 작업표시줄 높이에 맞춰 좌/우에 붙임 ---
    def place_bar(self):
        self.update_idletasks()
        w = self.inner.winfo_reqwidth() + self.grip.winfo_reqwidth() + 16
        g = taskbar_geometry()
        if not g:
            self._geo(f"{w}x48+40+40")
            return
        left, top, right, bottom, notify_left = g
        h = max(bottom - top, 32)
        if self.cfg.get("tb_x") is not None:
            x = self.cfg["tb_x"]
        elif self.cfg.get("tb_side", "right") == "left":
            x = left + 4
        else:
            x = notify_left - w - 4
        x = min(max(x, left), right - w)
        self._geo(f"{w}x{h}+{x}+{top}")

    def _hwnd(self, win=None):
        import ctypes
        return ctypes.windll.user32.GetAncestor((win or self).winfo_id(), 2)  # GA_ROOT

    def _geo(self, spec):
        """막대와 받침 창의 위치·크기를 함께 맞춘다"""
        self.geometry(spec)
        self.hit.geometry(spec)

    def no_activate(self):
        """클릭해도 활성화(포커스 이동)되지 않는 도구창 스타일 - 작업표시줄과 z-순서 다툼 방지"""
        import ctypes
        u = ctypes.windll.user32
        for h in (self._hwnd(), self._hwnd(self.hit)):
            ex = u.GetWindowLongW(h, -20)
            u.SetWindowLongW(h, -20, ex | 0x08000000 | 0x00000080)  # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

    def keep_on_top(self):
        """작업표시줄이 클릭돼 앞으로 올라오면 0.1초 안에 다시 최상위로 (활성화 없이)"""
        import ctypes
        try:
            # 이미 TOPMOST 인 창에 HWND_TOPMOST 를 주면 순서가 안 바뀌므로 HWND_TOP(0)으로
            # 최상위 그룹 맨 앞으로 올린다. SWP_NOSIZE|NOMOVE|NOACTIVATE|SHOWWINDOW = 0x1|0x2|0x10|0x40
            sp = ctypes.windll.user32.SetWindowPos
            sp(self._hwnd(self.hit), 0, 0, 0, 0, 0, 0x0053)  # 받침 먼저, 그 위에 막대
            sp(self._hwnd(), 0, 0, 0, 0, 0, 0x0053)
        except Exception:  # noqa: BLE001
            self.lift()
        self.after(50, self.keep_on_top)

    def set_side(self, side):
        self.cfg["tb_side"] = side
        self.cfg["tb_x"] = None
        save_cfg(self.cfg)
        self.place_bar()

    def _drag_start(self, e):
        self._drag_dx = e.x_root - self.winfo_x()
        self._x0 = self.winfo_x()

    def _drag(self, e):  # 가로로만 이동
        if self._drag_dx is not None:
            self._geo(f"+{e.x_root - self._drag_dx}+{self.winfo_y()}")

    def _drag_end(self, e):
        if self._drag_dx is not None and abs(self.winfo_x() - self._x0) > 3:  # 실제로 끌었을 때만 위치 저장
            self.cfg["tb_x"] = self.winfo_x()
            save_cfg(self.cfg)
        elif self._drag_dx is not None:  # 끌지 않고 클릭(손잡이든 글자든) -> 메뉴
            self._tip_hide(None)
            self.menu.tk_popup(e.x_root, e.y_root)
            self.menu.grab_release()
        self._drag_dx = None

    # --- 툴팁: 갱신 시각 등 상세 ---
    def _tip_show(self, _):
        if self.tip or not self.data:
            return
        self.tip = tk.Toplevel(self)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text="\n".join(detail_lines(self.data)), justify="left", bg=self.bg, fg=self.fg,
                 font=(KR, 9), padx=8, pady=6, highlightthickness=1, highlightbackground=self.dim).pack()
        self.tip.update_idletasks()
        x = max(self.winfo_x(), 0)
        self.tip.geometry(f"+{x}+{self.winfo_y() - self.tip.winfo_reqheight() - 6}")

    def _tip_hide(self, _):
        if self.tip:
            self.tip.destroy()
            self.tip = None

    # --- 동작 ---
    def on_select(self):
        if not self.enabled():
            self.vars["Claude"].set(True)
        self.cfg["enabled"] = self.enabled()
        save_cfg(self.cfg)
        self.cfg["tb_x"] = None if self.cfg.get("tb_x") is None else self.cfg["tb_x"]
        self.build()
        self.refresh()

    def change_bar_theme(self):
        self.cfg["theme_bar"] = self.bar_theme.get()
        save_cfg(self.cfg)
        relaunch("taskbar")
        self.destroy()

    def switch(self, display):
        relaunch(display)
        self.destroy()

    def refresh(self):
        if getattr(self, "_busy", False):
            return
        self._busy = True
        names = self.enabled()
        self._result = None

        def work():
            self._result = {n: PROVIDERS[n]() for n in names}

        def poll():
            if self._result is None:
                self.after(100, poll)
                return
            self._busy = False
            self.data.update(self._result)
            for n, d in self._result.items():
                if n in self.cols:
                    self.fill(n, d)
            self.place_bar()
            if getattr(self, "_job", None):
                self.after_cancel(self._job)
            self._job = self.after(REFRESH_MS, self.refresh)

        threading.Thread(target=work, daemon=True).start()
        self.after(100, poll)


# ---------- 상태표시줄(macOS) 모드 ----------
def relaunch(display):
    """표시 방식(widget/tray)을 바꿔 자기 자신을 다시 실행"""
    cfg = load_cfg()
    cfg["display"] = display
    save_cfg(cfg)
    cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen(cmd, env=env, close_fds=True)


def detail_lines(data):
    """메뉴/툴팁에 쓸 줄 목록"""
    out = []
    for name, d in data.items():
        if "error" in d:
            out.append(f"{name}: ⚠ {d['error']}")
        else:
            out.append(f"{name}  " + " · ".join(
                f"{SHORT.get(l, l)} {r:.0f}% ({short_reset(dt)})" for l, r, dt in d["rows"])
                + (f"  [이전 값: {d['stale']}]" if d.get("stale") else ""))
    return out


def tray_fetch(cfg):
    names = [n for n in PROVIDERS if n in cfg.get("enabled", ["Claude", "Codex"])] or ["Claude"]
    return {n: PROVIDERS[n]() for n in names}


def run_tray_mac():
    """macOS: 화면 위 상태표시줄(메뉴바) 텍스트 + 드롭다운 상세 (rumps 필요: pip install rumps)"""
    import rumps

    cfg = load_cfg()
    ABBR = {"Claude": "C", "Codex": "X", "Antigravity": "A"}

    class App(rumps.App):
        def __init__(self):
            super().__init__("AI …", quit_button=None)
            self.busy = False
            self.timer = rumps.Timer(self.tick, REFRESH_MS / 1000)
            self.timer.start()
            self.tick(None)

        def tick(self, _):
            if self.busy:
                return
            self.busy = True
            threading.Thread(target=self.work, daemon=True).start()

        def work(self):
            try:
                data = tray_fetch(cfg)
                parts = []
                for n, d in data.items():
                    parts.append(f"{ABBR[n]} " + ("⚠" if "error" in d or not d["rows"]
                                                  else f"{d['rows'][0][1]:.0f}%"))
                self.title = " · ".join(parts)
                self.menu.clear()
                for l in detail_lines(data):
                    self.menu.add(rumps.MenuItem(l))
                self.menu.add(None)
                for n in PROVIDERS:
                    it = rumps.MenuItem(n, callback=self.toggle)
                    it.state = n in cfg.get("enabled", ["Claude", "Codex"])
                    self.menu.add(it)
                self.menu.add(None)
                self.menu.add(rumps.MenuItem("지금 새로고침", callback=self.tick))
                self.menu.add(rumps.MenuItem("위젯 창 모드로 전환", callback=self.to_widget))
                self.menu.add(rumps.MenuItem("종료", callback=lambda _: rumps.quit_application()))
            finally:
                self.busy = False

        def toggle(self, sender):
            en = set(cfg.get("enabled", ["Claude", "Codex"]))
            en.symmetric_difference_update({sender.title})
            cfg["enabled"] = [n for n in PROVIDERS if n in en] or [sender.title]
            save_cfg(cfg)
            self.tick(None)

        def to_widget(self, _):
            relaunch("widget")
            rumps.quit_application()

    App().run()


def main():
    disp = load_cfg().get("display", "widget")
    if disp in ("taskbar", "tray") and not IS_MAC:  # 예전 tray 설정은 작업표시줄 모드로 취급
        TaskbarBar().mainloop()
    elif disp in ("taskbar", "tray") and IS_MAC:  # Mac: 화면 위 상태표시줄 (pip install rumps)
        try:
            run_tray_mac()
        except ImportError as e:
            print("상태표시줄 모드에는 rumps 가 필요합니다: pip install rumps", e)
    else:
        apply_theme(load_cfg().get("theme", "dark"))
        Widget().mainloop()


if __name__ == "__main__":
    main()
