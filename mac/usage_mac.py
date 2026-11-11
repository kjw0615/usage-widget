"""AI 남은 사용량 - macOS 상태표시줄(메뉴바) 앱 - Claude / Codex / Gemini

- Claude : ~/.claude/.credentials.json (macOS는 키체인) 토큰으로 api.anthropic.com/api/oauth/usage 조회
- Codex  : ~/.codex/sessions/**/*.jsonl 에 기록된 최신 rate_limits 읽기 (로컬 파일만 사용)
- Gemini : ~/.gemini/oauth_creds.json 토큰으로 Code Assist 쿼터 조회 (Gemini CLI 로그인 필요, 비공식 API)
조작: 상태표시줄 항목 클릭 -> 상세 내용 / 표시할 AI 선택 / 새로고침 / 종료
"""
import json
import os
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
SHORT = {"5시간": "5h", "주간": "주", "Pro 일일": "Pro", "Flash 일일": "Flash"}


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
            return {"error": "토큰 만료 - Claude/Gemini CLI 실행 시 자동 갱신", "short": "만료", "retry": 120}
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


def fetch_gemini():
    """Gemini CLI(Code Assist) 일일 쿼터. 비공식 API라 구조가 바뀌면 동작하지 않을 수 있음."""
    try:
        f = HOME / ".gemini" / "oauth_creds.json"
        if not f.exists():
            return {"error": "Gemini CLI 로그인 필요 (gemini 실행 후 Google 로그인)"}
        creds = json.loads(f.read_text(encoding="utf-8"))
        exp = creds.get("expiry_date", 0) / 1000
        if exp and exp < datetime.now().timestamp():
            return {"error": "토큰 만료 - gemini 를 한 번 실행하세요"}
        tok = creds["access_token"]
        base = "https://cloudcode-pa.googleapis.com/v1internal"
        meta = {"metadata": {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}}
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or http_json(
            f"{base}:loadCodeAssist", tok, meta).get("cloudaicompanionProject")
        if isinstance(project, dict):
            project = project.get("id")
        q = http_json(f"{base}:retrieveUserQuota", tok, {"project": project} if project else {})
        groups = {}  # "Pro"/"Flash" -> (남은 비율 최소, 갱신시각)
        for b in q.get("buckets", []):
            m = (b.get("modelId") or "").lower()
            g = "Pro" if "pro" in m else "Flash" if "flash" in m else None
            if not g or b.get("remainingFraction") is None:
                continue
            frac = float(b["remainingFraction"])
            if g not in groups or frac < groups[g][0]:
                groups[g] = (frac, b.get("resetTime"))
        rows = []
        for g in ("Pro", "Flash"):
            if g in groups:
                frac, rt = groups[g]
                reset = datetime.fromisoformat(rt.replace("Z", "+00:00")).astimezone() if rt else None
                rows.append((f"{g} 일일", frac * 100, reset))
        return {"rows": rows} if rows else {"error": "쿼터 정보 없음"}
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
    "Claude": lambda: cached("Claude", fetch_claude, 180),
    "Codex": lambda: cached("Codex", fetch_codex, 120),
    "Gemini": lambda: cached("Gemini", fetch_gemini, 180),
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
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


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


ABBR = {"Claude": "C", "Codex": "X", "Gemini": "G"}
DOT = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}


def title_text(data):
    parts = []
    for n, d in data.items():
        if "error" in d or not d.get("rows"):
            parts.append(f"{ABBR[n]} ⚠{d.get('short', '')}")
        else:
            remain = d["rows"][0][1]  # 5시간(또는 일일) 남은 %
            parts.append(f"{DOT[color_for(remain)]}{ABBR[n]} {remain:.0f}%" + ("~" if d.get("stale") else ""))
    return "  ".join(parts) or "AI …"


def main():
    import rumps

    cfg = load_cfg()

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
            self.menu.add(None)
            self.menu.add(rumps.MenuItem("지금 새로고침", callback=self.tick))
            self.menu.add(rumps.MenuItem("종료", callback=lambda _: rumps.quit_application()))

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
