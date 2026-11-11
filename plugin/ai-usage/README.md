# ai-usage (Claude Code 플러그인)

Claude · Codex · Gemini 의 남은 사용량을 Claude Code 상태줄에 `🟢C 71% 🟢X 46%` 처럼 보여주고,
`/ai-usage` 명령으로 5시간·주간·일일 남은 %와 갱신 시각을 자세히 보여줍니다.

- 1분마다 갱신, 서버 조회는 2~3분 간격으로만 보내 요청 제한(429)을 피합니다.
- 오류가 나면 마지막 정상값을 `~` 표시와 함께 유지합니다.
- 그 PC에 로그인된 Claude Code / Codex / Gemini CLI 의 로그인 정보를 읽기만 합니다(저장·갱신·외부 전송 없음, 각 서비스 공식 서버로만 요청).
- Claude Code 가 켜져 있을 때만 동작합니다. 항상 떠 있는 작업표시줄·상태표시줄 위젯은 저장소 루트의 위젯 앱을 쓰세요.
- 비공식 API 를 사용하므로 서비스 쪽 변경으로 동작이 바뀔 수 있습니다.

설정 `providers`: 표시할 AI 를 쉼표로 지정 (기본 `Claude,Codex`, 예: `Claude,Codex,Gemini`).
