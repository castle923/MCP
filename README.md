# hub-desk

Claude Code와 Codex 에이전트를 팀으로 띄우고, 브라우저 대시보드에서 지휘하는 Windows용 로컬 프로그램.

- **팀 구성**: 메인(지휘) 1명 + 서브(구현·검토) 최대 6명, 팀은 최대 6개. 에이전트마다 CLI·모델·추론 강도·이름을 고르고, 확정한 구성으로 실행한다. 실행 중에 팀을 추가하거나 팀·에이전트별로 정지·다시 시작할 수 있다.
- **위임**: 메인이 MCP 도구(`hub_assign`, `hub_wait_all`)로 서브에게 일을 나눠 병렬로 처리한다.
- **대시보드**: 픽셀 사무실(업무 전달·완료·보고·게시 애니메이션), 에이전트별 터미널, 작업 보드, 이벤트, 지시 입력창, 결과를 보여 주는 칠판 게시판.
- **하네스·스킬**: 프로젝트의 `AGENTS.md`를 지침으로 적용하고, 설치된 스킬·플러그인을 골라 붙인다. 브라우저(Playwright)·PC 제어(agent-desktop) 도구, ponytail 기본 적용.
- **git 작업 사본**: 빌더마다 git worktree에서 일하고, 대시보드에서 diff를 본 뒤 합치기(스테이징만)하거나 버린다.
- **기록**: 실행 기록 보기, 이전 대화를 이어서 실행(`claude --resume`, `codex resume`).
- **자원 반환**: 에이전트가 끝나면 그 에이전트가 띄운 프로세스를 모두 끝낸다(Windows Job Object).

## 요구 사항

Windows 10/11, Python 3.11 이상, 그리고 Claude Code CLI 또는 Codex CLI(각자의 계정으로 로그인). 선택: git, Node.js.

## 설치

```powershell
git clone https://github.com/castle923/MCP.git hub-desk
```

`hub-desk` 폴더의 `install.cmd`를 더블클릭한다. 자세한 설정은 [INSTALL.md](INSTALL.md).

## 주의

서버는 `127.0.0.1`에서만 열린다. 이 서버에 접속하면 에이전트 터미널에 명령을 입력할 수 있으므로 외부에 공개하지 않는다. 에이전트는 고른 프로젝트 폴더의 파일을 실제로 고친다.

`agent_hub/`는 별도 도구 agent-hub의 사본을 이 프로그램에 맞게 고친 것이다.
