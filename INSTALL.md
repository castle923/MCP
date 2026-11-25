# hub-desk 설치 안내 (다른 PC)

hub-desk는 Claude Code와 Codex 에이전트를 팀으로 띄우고, 브라우저 대시보드에서 지휘하는 Windows용 로컬 프로그램이다. 이 PC 안에서만 접속된다(`127.0.0.1`).

## 1. 먼저 설치할 것

| 항목 | 필요 여부 | 설치 |
|---|---|---|
| Windows 10/11 | 필수 | Mac·Linux에서는 동작하지 않는다 |
| Python 3.11 이상 | 필수 | https://www.python.org/downloads/ — 설치 화면에서 "py launcher"와 "Add python.exe to PATH"를 켠다. 3.15.2로 확인했다 |
| Claude Code CLI | Claude 에이전트를 쓸 때 | https://docs.claude.com/en/docs/claude-code |
| Codex CLI | Codex 에이전트를 쓸 때 | https://github.com/openai/codex |
| git | 빌더 작업 사본을 쓸 때 | https://git-scm.com/download/win |
| Node.js | 브라우저 도구(Playwright)를 쓸 때 | https://nodejs.org/ |

Claude Code와 Codex 중 하나는 있어야 한다.

## 2. 설치

1. `hub-desk-<날짜>.zip`을 원하는 곳에 푼다. 예: `C:\hub-desk`. 경로에 한글이 있어도 된다.
2. 풀린 폴더의 `install.cmd`를 더블클릭한다.
   - 가상환경(`.venv`)을 만들고 패키지를 설치한다(인터넷 필요).
   - 자체 테스트를 실행한다.
   - 이 PC에 무엇이 있는지 보여 준다.
   - 바탕화면에 `hub-desk` 아이콘을 만든다.
3. 설치 프로그램은 그 폴더 안과 바탕화면 바로가기 하나만 만든다. 시스템 설정이나 다른 프로그램은 건드리지 않는다.

Windows가 "알 수 없는 게시자" 경고를 띄우면, 받은 파일이 맞는지 확인한 뒤 실행한다.

## 3. 설치 후 직접 해야 하는 설정

1. **CLI 로그인**: 명령 창에서 `claude`와 `codex`를 한 번씩 실행해 **이 PC 사용자의 계정**으로 로그인한다. hub-desk는 API 키를 저장하지 않고 각 CLI의 로그인을 그대로 쓴다. 사용량은 그 계정의 구독에서 나간다.
2. **폴더 신뢰 확인**: 처음 여는 폴더마다 각 CLI가 "이 폴더를 신뢰합니까?"를 묻는다. 대시보드에 "승인 필요 · 폴더 신뢰 확인"이 뜨면 그 에이전트 터미널을 눌러 직접 답한다. Claude는 기본 선택이 "No, exit"이니 확인하고 고른다. 빌더 작업 사본은 경로가 고정이라 폴더마다 한 번만 묻는다.
3. **하네스(선택)**: 에이전트가 따를 지침 파일이다. 프로젝트 폴더에 `AGENTS.md`를 두면 그 파일을 쓴다. 없으면 `%USERPROFILE%\.codex\AGENTS.md`를 쓰고, 그것도 없으면 "사용 안 함"이 된다.
4. **브라우저 도구(선택)**: Node.js가 있고 Claude Code 또는 Codex에 Playwright MCP가 등록되어 있으면 켤 수 있다. 등록 예:

   ```
   claude mcp add -s user playwright -- npx @playwright/mcp@latest
   ```

5. **PC 제어(선택)**: Codex 설정(`%USERPROFILE%\.codex\config.toml`)에 agent-desktop MCP가 있으면 켤 수 있다. 없으면 설정 화면에 "사용 불가"로 나온다.
6. **ponytail·스킬·플러그인(선택)**: 각 CLI에 설치된 것을 그대로 쓴다. 없으면 그 항목만 빠진다.
7. **애니메이션**: Windows의 "애니메이션 효과"가 꺼져 있으면 사무실 효과가 멈춘 그림으로 나온다. 대시보드의 "애니메이션 켬" 버튼으로 켠다.

## 4. 실행과 종료

- 바탕화면의 `hub-desk` 아이콘을 더블클릭한다. 브라우저에 `http://127.0.0.1:8765`가 열린다. 이미 켜져 있으면 브라우저만 다시 연다.
- 작업 표시줄의 "hub-desk - close this window…" 창이 서버다. 그 창을 닫으면 hub-desk와 에이전트가 모두 끝난다. 일반적으로는 대시보드의 "정지"를 먼저 누른다.
- 포트를 바꾸려면 `HUB_DESK_PORT` 환경 변수를 쓴다.

## 5. 주의

- 이 서버에 접속할 수 있으면 터미널에 명령을 입력할 수 있다. **외부에서 접속되게 바꾸지 않는다**(방화벽 개방, 터널, 포트 포워딩 금지).
- 다른 PC로 보낼 때는 `state\` 폴더를 넣지 않는다. 실행 기록, 세션 ID, 폴더 경로가 들어 있다. `tools\make_package.py`로 만든 zip에는 들어가지 않는다.
- 기존 프로젝트 폴더를 고르면 에이전트가 그 폴더의 파일을 실제로 고친다. git으로 관리되는 폴더나 복사본에서 시작하는 것이 안전하다.

## 6. 문제가 생기면

| 증상 | 확인 |
|---|---|
| `install.cmd`가 Python을 못 찾음 | Python 3.11 이상 설치, "Add python.exe to PATH" 켜기 |
| 패키지 설치 실패 | 인터넷 연결, 회사 프록시. 그 Python 버전용 `pywinpty`가 없으면 Python 3.12~3.15로 설치 |
| 에이전트가 시작하지 않음 | 그 CLI가 설치·로그인되어 있는지, 대시보드에 "폴더 신뢰 확인"이 떠 있는지 |
| 포트 8765를 이미 사용 중 | `set HUB_DESK_PORT=8766` 후 `hub-desk.cmd` 실행 |
| 재설치 | 폴더의 `.venv`를 지우고 `install.cmd`를 다시 실행 |
