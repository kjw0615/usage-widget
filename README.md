# AI Usage Widget

Claude · Codex · Antigravity(agy) 의 **남은 사용량(5시간 / 주간)과 갱신 시각**을 한눈에 보여주는 작은 위젯입니다.

| OS | 표시 방식 |
|----|-----------|
| Windows | 작업표시줄 위 투명 막대 (기본) · 위젯 창 · 간소화 모드 |
| macOS | 화면 위 상태표시줄(메뉴바) |

- 표시할 AI는 우클릭(Windows) / 메뉴 클릭(Mac)으로 선택합니다. 선택은 저장됩니다.
- 1분마다 갱신하며, Claude·Antigravity·Codex 서버 조회는 요청 제한을 피하려고 2~3분 간격으로만 보냅니다.
- 오류가 나도 마지막 정상값을 흐리게 유지합니다.

> ⚠ Claude·Codex·Antigravity 의 사용량 조회 주소는 **공식 문서에 없는 비공식 API** 입니다. 서비스 쪽 변경으로 언제든 동작이 바뀌거나 멈출 수 있습니다.

## 먼저 확인: 로그인되어 있어야 합니다

위젯은 **그 PC에 이미 로그인된 CLI/앱의 로그인 정보**를 읽습니다. 별도 로그인 화면은 없습니다.

| AI | 필요한 것 |
|----|-----------|
| Claude | Claude Code(CLI 또는 데스크톱 앱 Code 탭) 로그인 |
| Codex | Codex CLI/앱 로그인 (`~/.codex/auth.json`) |
| Antigravity | Antigravity CLI(`agy`) 에서 Google 로그인 (OS 보안 저장소의 `gemini:antigravity`). Gemini 5시간, Claude·GPT 5시간·주간을 보여주고, 서버가 내려주는 경우 Gemini 주간과 Argon 주간도 표시합니다(우클릭 메뉴 `Antigravity 항목`에서 고르기). 로그인 토큰은 `agy` 를 실행할 때 갱신되므로, 한동안 쓰지 않으면 `만료` 로 표시됩니다. (Gemini CLI 는 구독 종료로 지원하지 않습니다) **`windows_v2` 는 `agy -p /usage` 명령 결과를 직접 읽어** Gemini·Claude·GPT·Argon 의 5시간/주간 값을 더 정확하게 가져옵니다. 이 명령이 실패하면 기존 내부 API 방식으로 자동 전환됩니다. |

한 AI만 로그인돼 있으면 그 AI 값만 나오고 나머지는 `⚠` 로 표시됩니다.

## 개인정보

- 로그인 토큰은 **이 PC 안에서 읽기만** 하며, 각 서비스의 공식 서버(api.anthropic.com, chatgpt.com, cloudcode-pa.googleapis.com)로만 전송됩니다.
- 토큰을 저장·갱신·외부 전송하지 않습니다. 만료되면 해당 CLI 를 한 번 실행하세요.
- 위젯 설정(위치, 선택한 AI)만 로컬에 저장됩니다. Windows `%APPDATA%\UsageWidget`, Mac `~/Library/Application Support/UsageWidget`.

---

## Windows 설치

### 방법 1: exe (권장, Python 불필요)
1. [Releases](../../releases) 에서 `UsageWidget.exe` 를 받습니다.
2. 더블클릭하면 작업표시줄 오른쪽(알림영역 옆)에 막대가 뜹니다.
3. SmartScreen 경고가 뜨면 **추가 정보 → 실행**. (서명되지 않은 개인 빌드라 나오는 경고입니다.)

조작: `⋮` 손잡이를 가로로 드래그해 이동 · 더블클릭 새로고침 · 우클릭 메뉴(AI 선택, 위치, 위젯 창 전환, 종료)

시작 시 자동 실행: `Win + R` → `shell:startup` → 열린 폴더에 `UsageWidget.exe` 바로가기를 넣습니다.

### 방법 2: 소스로 실행
Python 3.10+ 설치 후:
```bash
python windows/usage_widget.pyw
```
직접 exe 만들기: `pip install pyinstaller` → `pyinstaller --onefile --noconsole --name UsageWidget windows/usage_widget.pyw`

### windows_v2 (Antigravity 사용량 개선판)
`windows_v2/usage_widget.pyw` 는 `windows/` 와 같은 위젯이며, Antigravity 사용량을 `agy -p /usage` 명령으로 조회합니다.
- `agy` 는 PATH 또는 `%LOCALAPPDATA%\agy\bin\agy.exe` 에서 찾습니다. (Antigravity CLI 설치·로그인 필요)
- 명령이 실패하면 기존 방식(저장된 로그인 토큰으로 서버 조회)으로 자동 전환됩니다.
```bash
python windows_v2/usage_widget.pyw
```
exe 로 만들기: `pyinstaller --onefile --noconsole --name UsageWidget windows_v2/usage_widget.pyw`
(Releases 의 exe 는 아직 `windows/` 기준 빌드입니다.)

## macOS 설치

### 방법 1: 앱 (Python 불필요)
1. [Releases](../../releases) 에서 `AI-Usage-mac.zip` 을 받아 압축을 풉니다.
2. `AI Usage.app` 을 `응용 프로그램` 폴더로 옮깁니다.
3. 처음 실행할 때 "확인되지 않은 개발자" 경고가 뜨면 **앱을 우클릭 → 열기**, 또는 터미널에서
   ```bash
   xattr -cr "/Applications/AI Usage.app"
   ```
4. 상태표시줄에 `C 🟢5h 71% 🟢주 93%  X 🟢5h 65% 🟡주 35%` 처럼 5시간·주간 사용량이 함께 나타납니다. 클릭하면 상세(5h·주간·갱신 시각)와 AI 선택 메뉴가 열립니다.
5. Claude 로그인 정보는 키체인에서 읽으므로 처음에 키체인 접근 허용 창이 뜰 수 있습니다.

### 방법 2: 직접 빌드
`mac/` 폴더에서 `build_mac.command` 를 실행하면 `dist/AI Usage.app` 이 만들어집니다. (만들 때만 Python 3 필요)

### 방법 3: 소스로 실행
```bash
pip install rumps
python3 mac/usage_mac.py
```

> Mac 에는 Python/tkinter 로 만들 수 있는 "위젯"(알림센터·바탕화면 위젯)이 없어서(Apple 전용 WidgetKit 필요), 상태표시줄 방식으로 제공합니다.

## Claude Code 플러그인 (선택, Claude 전용)

위젯 앱과 별개로, Claude Code 안에서 **Claude 남은 사용량(5시간 · 주간)** 을 상태줄(`Claude 🟢5h 71% 🟢주 93%`)과
`/ai-usage` 명령으로 보는 플러그인이 들어 있습니다. Windows / macOS 모두 같은 명령으로 설치합니다 (별도 빌드·경고 없음).

```text
/plugin install ai-usage --marketplace kjw0615/usage-widget
```

| | 위젯 앱 (exe / .app) | Claude Code 플러그인 |
|---|---|---|
| 보여주는 AI | Claude · Codex · Antigravity (선택) | Claude (5시간·주간) |
| 동작 시점 | PC가 켜져 있으면 항상 | Claude Code 가 켜져 있을 때만 |
| 표시 위치 | 작업표시줄 / Mac 상태표시줄 | Claude Code 상태줄 + `/ai-usage` |
| 설치 | 파일 다운로드 | 명령 한 줄 |

## 릴리스 만들기 (관리자용)
```bash
git tag v1.0.0 && git push origin v1.0.0
```
GitHub Actions 가 Windows exe 와 Mac 앱을 빌드해 Releases 에 올립니다.

## 라이선스
MIT
