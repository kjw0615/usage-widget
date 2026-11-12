"""AI 남은 사용량 - macOS 상태표시줄(메뉴바) 앱 - Claude / Codex / Antigravity

- Claude : ~/.claude/.credentials.json (macOS는 키체인) 토큰으로 api.anthropic.com/api/oauth/usage 조회
- Codex  : ~/.codex/sessions/**/*.jsonl 에 기록된 최신 rate_limits 읽기 (로컬 파일만 사용)
- Antigravity : agy(Antigravity CLI) 로그인 정보(OS 보안 저장소)로 모델 그룹별 5시간 쿼터 조회 (비공식 API)
조작: 상태표시줄 항목 클릭 -> 상세 내용 / 표시할 AI 선택 / 새로고침 / 종료
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
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

BG, CARD, FG, DIM = "#1e1f24", "#2a2c33", "#f2f3f5", "#8b8f99"
GREEN, YELLOW, RED = "#4ade80", "#facc15", "#f87171"
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


AGY_LABELS = ("Gemini 5시간", "Claude·GPT 5시간")  # Antigravity 에서 고를 수 있는 항목
AGY = {"items": list(AGY_LABELS)}


def agy_apply(cfg):
    """저장된 설정의 Antigravity 표시 항목을 적용 (비어 있으면 전부)"""
    AGY["items"] = [l for l in AGY_LABELS if l in cfg.get("agy_items", AGY_LABELS)] or list(AGY_LABELS)


def fetch_antigravity_selected():
    d = cached("Antigravity", fetch_antigravity, 180)
    if "rows" not in d:
        return d
    return {**d, "rows": [r for r in d["rows"] if r[0] in AGY["items"]] or d["rows"]}


PROVIDERS = {
    "Claude": lambda: cached("Claude", fetch_claude, 180),
    "Codex": lambda: cached("Codex", fetch_codex, 120),
    "Antigravity": fetch_antigravity_selected,
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



# ---------- 설정 저장 (~/Library/Application Support/UsageWidget) ----------
CFG_PATH = HOME / "Library" / "Application Support" / "UsageWidget" / "config.json"


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


def detail_lines(data):
    out = []
    for name, d in data.items():
        if "error" in d:
            out.append(f"{name}: ⚠ {d['error']}")
        else:
            out.append(f"{name}  " + " · ".join(
                f"{SHORT.get(l, l)} {r:.0f}% ({short_reset(dt)})" for l, r, dt in d["rows"])
                + (f"  [이전 값: {d['stale']}]" if d.get("stale") else ""))
    return out


ABBR = {"Claude": "C", "Codex": "X", "Antigravity": "A"}
DOT = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}


def title_text(data):
    """상태표시줄 문구: AI마다 모든 구간(5시간·주간 / Antigravity 는 Gemini·Claude·GPT 5시간)을 함께 표시"""
    parts = []
    for n, d in data.items():
        if "error" in d or not d.get("rows"):
            parts.append(f"{ABBR[n]} ⚠{d.get('short', '')}")
        else:
            vals = " ".join(f"{DOT[color_for(r)]}{SHORT.get(l, l)} {r:.0f}%" for l, r, _ in d["rows"])
            parts.append(f"{ABBR[n]} {vals}" + ("~" if d.get("stale") else ""))
    return "  ".join(parts) or "AI …"


def main():
    import rumps

    cfg = load_cfg()
    agy_apply(cfg)

    def enabled():
        en = [n for n in PROVIDERS if n in cfg.get("enabled", ["Claude", "Codex"])]
        return en or ["Claude"]

    class App(rumps.App):
        def __init__(self):
            super().__init__("AI …", quit_button=None)
            self.data, self.pending, self.busy = {}, None, False
            self.rebuild()
            self.fetch_timer = rumps.Timer(self.tick, REFRESH_MS / 1000)
            self.fetch_timer.start()
            self.ui_timer = rumps.Timer(self.apply, 1)  # 화면 갱신은 메인 스레드에서만
            self.ui_timer.start()
            self.tick(None)

        def tick(self, _):
            if self.busy:
                return
            self.busy = True
            names = enabled()

            def work():
                try:
                    self.pending = {n: PROVIDERS[n]() for n in names}
                finally:
                    self.busy = False

            threading.Thread(target=work, daemon=True).start()

        def apply(self, _):
            if self.pending is not None:
                self.data = {n: d for n, d in self.pending.items() if n in enabled()}
                self.pending = None
                self.rebuild()

        def rebuild(self):
            self.title = title_text({n: self.data[n] for n in enabled() if n in self.data})
            self.menu.clear()
            for line in detail_lines({n: self.data[n] for n in enabled() if n in self.data}):
                self.menu.add(rumps.MenuItem(line))
            self.menu.add(None)
            for n in PROVIDERS:
                item = rumps.MenuItem(n, callback=self.toggle)
                item.state = n in enabled()
                self.menu.add(item)
            sub = rumps.MenuItem("Antigravity 항목")
            for l in AGY_LABELS:
                it = rumps.MenuItem(l, callback=self.toggle_agy)
                it.state = l in AGY["items"]
                sub.add(it)
            self.menu.add(sub)
            self.menu.add(None)
            self.menu.add(rumps.MenuItem("지금 새로고침", callback=self.tick))
            self.menu.add(rumps.MenuItem("종료", callback=lambda _: rumps.quit_application()))

        def toggle_agy(self, sender):
            chosen = set(AGY["items"])
            chosen.symmetric_difference_update({sender.title})
            cfg["agy_items"] = [l for l in AGY_LABELS if l in chosen] or [sender.title]
            save_cfg(cfg)
            agy_apply(cfg)
            self.pending = {n: PROVIDERS[n]() for n in enabled()}  # 캐시된 값에서 항목만 다시 거름
            self.apply(None)

        def toggle(self, sender):
            en = set(enabled())
            en.symmetric_difference_update({sender.title})
            cfg["enabled"] = [n for n in PROVIDERS if n in en] or [sender.title]
            save_cfg(cfg)
            self.rebuild()
            self.tick(None)

    App().run()


if __name__ == "__main__":
    main()
