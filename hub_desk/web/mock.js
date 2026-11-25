// Mock backend, active only with ?mock=1 (add &phase=running to open straight on the dashboard).
// Everything fake lives in this file; app.js uses window.HubMock in place of the network when it exists.
(() => {
  'use strict';
  const q = new URLSearchParams(location.search);
  if (!q.has('mock')) return;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const enc = new TextEncoder();
  const catalog = {
    workspace: 'C:\\Users\\user\\MCP', limits: { max_teams: 6, max_subs: 6 }, jobs: ['builder', 'reviewer'],
    cli: {
      claude: { label: 'Claude Code', efforts: ['low', 'medium', 'high', 'xhigh', 'max'], default_effort: 'medium',
        models: [{ id: 'claude-opus-5-5', label: 'Opus 5.5', verified: false }, { id: 'claude-sonnet-5', label: 'Sonnet 5', verified: true }] },
      codex: { label: 'Codex', efforts: ['low', 'medium', 'high'], default_effort: 'medium',
        models: [{ id: 'gpt-6.1-sol', label: 'Sol 6.1', verified: true }, { id: 'gpt-6.1-terra', label: 'Terra 6.1', verified: true }, { id: 'gpt-6-luna', label: 'Luna 6', verified: true }] },
    },
    defaults: { main: { cli: 'codex', model: 'gpt-6.1-sol', effort: 'medium' },
      builder: { cli: 'claude', model: 'claude-opus-5-5', effort: 'medium' },
      reviewer: { cli: 'claude', model: 'claude-opus-5-5', effort: 'medium' } },
    name_rule: { pattern: '^[\\w-]{1,32}$', reserved: ['main', 'board', 'human', 'broadcast'], hint: '글자·숫자·-·_ 1~32자' },
  };

  const scanData = {
    harness: { auto: { path: 'C:\\Users\\user\\.codex\\AGENTS.md', scope: 'global' }, native: { claude: 'mention', codex: 'native' } },
    base: { claude: { skills: ['qa', 'review', 'investigate', 'browse'], plugins: ['superpowers', 'caveman'] },
      codex: { skills: ['frontend-design', 'graphify', 'markitdown'], plugins: [] } },
    extras: [
      ['skill:codex:free-design-references', 'skill', 'free-design-references', '웹·앱 UI의 신규 디자인이나 화면 재설계 때 무료 공개 참고 자료를 찾아 적용한다', 'Codex 스킬', false, 'mention', 'native'],
      ['skill:codex:ui-color-palettes', 'skill', 'ui-color-palettes', '로컬 3종 팔레트에서 색상 방향 후보를 고른다', 'Codex 스킬', false, 'mention', 'native'],
      ['skill:codex:recurrencia-ui', 'skill', 'recurrencia-ui', '반복 일정·주기 설정 화면의 로컬 소스 참고', 'Codex 스킬', false, 'mention', 'native'],
      ['skill:claude:make-pdf', 'skill', 'make-pdf', 'Markdown 문서를 PDF로 변환', 'Claude 스킬', false, 'native', 'mention'],
      ['skill:claude:design-review', 'skill', 'design-review', '완성된 화면의 시각 품질 점검', 'Claude 스킬', false, 'native', 'mention'],
      ['skill:claude:benchmark', 'skill', 'benchmark', '성능 회귀 측정', 'Claude 스킬', false, 'native', 'mention'],
      ['skill:claude:cso', 'skill', 'cso', '보안 점검 체크리스트', 'Claude 스킬', false, 'native', 'mention'],
      ['skill:claude:retro', 'skill', 'retro', '작업 회고 정리', 'Claude 스킬', false, 'native', 'mention'],
      ['skill:codex:markitdown-batch', 'skill', 'markitdown-batch', '문서·슬라이드·표 자료를 Markdown으로 일괄 추출', 'Codex 스킬 (새로 내려받음)', true, 'mention', 'native'],
      ['skill:claude:test-audit', 'skill', 'test-audit', '테스트 누락 영역 조사', 'Claude 스킬', false, 'native', 'mention'],
      ['skill:claude:spec', 'skill', 'spec', '요구사항을 명세로 정리', 'Claude 스킬', false, 'native', 'mention'],
      ['plugin:gsd-core@gsd-core', 'plugin', 'gsd-core', 'v1.19.0', 'Claude 플러그인 (꺼져 있음)', true, 'plugin-dir', 'mention'],
    ].map(([id, kind, name, description, source, isNew, claude, codex]) => ({ id, kind, name, description, source, new: isNew, how: { claude, codex } })),
    git: { root: 'C:\\Users\\user\\MCP', head: 'a1b2c3d4', branch: 'main', dirty: 2, sub: '' },
    limits: { max_extras: 5 }, // small on purpose so the limit can be seen in mock mode
  };

  // Sample board posts. data: URLs stand in for the server's /board/... links in mock mode only.
  const page = (title, bars) => 'data:text/html;charset=utf-8,' + encodeURIComponent(`<!doctype html><meta charset="utf-8"><title>${title}</title>
<style>body{font:15px "Segoe UI","Malgun Gothic",sans-serif;margin:24px;color:#1c2330}h1{font-size:20px}.b{display:flex;align-items:end;gap:12px;height:180px;border-bottom:2px solid #1c2330}.b i{width:44px;background:#3d7fc4}p{color:#556}</style>
<h1>${title}</h1><div class="b" id="b"></div><p id="s">스크립트가 실행되지 않았습니다.</p>
<script>const v=${JSON.stringify(bars)};document.getElementById('b').innerHTML=v.map(n=>'<i style="height:'+n+'%"></i>').join('');document.getElementById('s').textContent='합계 '+v.reduce((a,b)=>a+b,0)+' (스크립트 실행됨)';<\/script>`);
  const svg = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180" shape-rendering="crispEdges"><rect width="320" height="180" fill="#1b2a3a"/><rect x="40" y="110" width="40" height="40" fill="#f5c04e"/><rect x="110" y="80" width="40" height="70" fill="#5fb3f5"/><rect x="180" y="50" width="40" height="100" fill="#7bd3a0"/><rect x="250" y="95" width="40" height="55" fill="#ff6b4a"/><rect x="30" y="150" width="270" height="4" fill="#e6e8ec"/></svg>');
  const txt = 'data:text/plain;charset=utf-8,' + encodeURIComponent('# 검토 메모\n\n- 입력 검증: 통과\n- 터미널 리사이즈: 재확인 필요\n- 긴 줄은 창 너비에 맞춰 줄바꿈되어야 합니다. ' + '가나다라마바사 '.repeat(30) + '\n');
  const post = (id, role, name, title, kind, file, url, note, ago) => ({ id, ts: Date.now() / 1000 - ago, role, name, title, note, file, kind, url });

  // One scripted round of hub events about every 20 s in the first team, so every office effect can be seen.
  const SCRIPT = (sub, other) => [
    [0, 'task', 'main', { assignee: sub }], [1, 'nudge', sub, { via: 'stdin', messages: 1 }], [2, 'task_in_progress', sub, {}],
    [3, 'PreToolUse', sub, { tool: 'Read' }], [5, 'PreToolUse', sub, { tool: 'Edit' }], [7, 'PreToolUse', sub, { tool: 'Bash' }],
    [8, 'PreToolUse', sub, { tool: 'ToolSearch' }], [9, 'PreToolUse', sub, { tool: 'mcp__playwright__browser_navigate' }],
    [10.5, 'PreToolUse', sub, { tool: 'mcp__agent-desktop__desktop_list_windows' }], [12, 'PreToolUse', sub, { tool: 'mcp__agent_hub__board_post' }],
    [13, 'board_post', sub, { title: '진행 보고' }], [15, 'message', sub, { to: 'main', act: 'done', subject: '끝났습니다' }],
    [16, 'task_done', sub, {}], [17, 'message', 'main', { to: other, act: 'query', subject: '검토 가능?' }],
    [18, 'message', other, { to: 'main', act: 'refuse', subject: '지금은 어렵습니다' }], [19, 'task_failed', other, {}],
  ];
  let round = -1, cursor = 0;
  function playScript(el) {
    const team = st.teams[0], sub = team.agents[1]?.role, other = (team.agents[2] || team.agents[1])?.role;
    if (!sub || el < 3000) return;
    const r = Math.floor((el - 3000) / 20000), at = ((el - 3000) % 20000) / 1000, steps = SCRIPT(sub, other);
    if (r !== round) { round = r; cursor = 0; }
    const id = `demo-${r}`, task = () => team.tasks.find((t) => t.id === id);
    for (; cursor < steps.length && steps[cursor][0] <= at; cursor++) {
      const [, kind, role, data] = steps[cursor];
      if (kind === 'task') { team.tasks = team.tasks.filter((t) => !t.id.startsWith('demo-')); team.tasks.push({ id, assignee: sub, status: 'open', objective: `시연용 작업 ${r + 1}`, updated_at: Date.now() / 1000 }); }
      if (kind === 'task_in_progress' && task()) task().status = 'in_progress';
      if (kind === 'task_done' && task()) task().status = 'done';
      team.events.push({ seq: ++seq, ts: Date.now() / 1000, role, kind, text: kind.startsWith('task') ? `id=${id}` : (data.tool || data.subject || data.title || ''), data: { id, ...data } });
      team.events = team.events.slice(-40);
    }
  }

  // Work copies (git worktrees) of the builders. A role with "conflict" in its name fails to merge.
  const wt = new Map();
  const FAKE_DIFF = ['diff --git a/app/main.py b/app/main.py', 'index 3f2a1c0..9b7d4e2 100644', '--- a/app/main.py', '+++ b/app/main.py',
    '@@ -10,7 +10,9 @@ def total(rows):', '     s = 0', '-    for r in rows: s += r', '+    for r in rows:', '+        s += r.amount',
    '     return s', ' ', '+# <script>alert("이 줄은 글자 그대로 보여야 합니다")</script> & <b>굵게 아님</b>',
    'diff --git a/web/chart.js b/web/chart.js', '--- a/web/chart.js', '+++ b/web/chart.js', '@@ -1,3 +1,3 @@',
    '-const bars = 4;', '+const bars = 5; // ' + '아주 긴 줄은 상자 안에서 가로로 스크롤됩니다 '.repeat(8), ' draw(bars);',
    'diff --git a/img/logo.png b/img/logo.png', 'Binary files a/img/logo.png and b/img/logo.png differ'].join('\n');
  function seedWt(cfg) {
    wt.clear();
    if (cfg.worktrees === false) return;
    for (const team of st.teams) team.agents.filter((a) => a.job === 'builder').forEach((a, n) => {
      const busy = n === 0 || /conflict/.test(a.role);
      wt.set(`${team.id}/${a.role}`, { team: team.id, role: a.role, path: `C:\\Users\\user\\MCP\\state\\worktrees\\MCP-1a2b3c4d\\${team.id}-${a.role}`,
        branch: `hub/${team.id}-${a.role}`, merged: [], resumed: /conflict/.test(a.role), // one copy carries earlier unmerged work
        files: busy ? [{ path: 'app/main.py', add: 3, del: 1 }, { path: 'web/chart.js', add: 1, del: 1 }, { path: 'img/logo.png', add: null, del: null }] : [] });
    });
  }
  const wtAgent = (team, role) => st.teams?.find((t) => t.id === team)?.agents.find((a) => a.role === role);

  // Past runs for the history dialog. The folder of one is gone; one has failures and a post of each kind.
  const RUNS = [
    { id: '20261125T143109-제어_화면_시험', project: '제어 화면 시험', workspace: 'C:\\Users\\user\\MCP', workspace_ok: true, started_at: 1795584669, teams: 2, agents: 5, tasks: { total: 4, done: 4, failed: 0 }, posts: 0, resumable: true },
    { id: '20261124T101500-가을_매출', project: '가을 매출 대시보드', workspace: 'D:\\work\\가을-매출', workspace_ok: true, started_at: 1795482900, teams: 1, agents: 3, tasks: { total: 3, done: 1, failed: 2 }, posts: 4, resumable: true },
    { id: '20261120T090000-옛_폴더', project: '옛 폴더 작업', workspace: 'E:\\지워진\\폴더\\proj', workspace_ok: false, started_at: 1795132800, teams: 1, agents: 2, tasks: { total: 1, done: 1, failed: 0 }, posts: 0, resumable: true },
    { id: '20261118T160000-짧은_시험', project: '짧은 시험', workspace: 'C:\\proj\\shop-web', workspace_ok: true, started_at: 1794985200, teams: 1, agents: 1, tasks: { total: 0, done: 0, failed: 0 }, posts: 0, resumable: false },
  ];
  function runDetailOf(r) {
    const ag = (role, name, job, cli, model, resumable) => ({ role, name, job, cli, model, effort: 'medium', resumable });
    const teams = Array.from({ length: r.teams }, (_, i) => {
      const agents = [ag('main', i ? '총괄' : 'main', 'main', 'codex', 'gpt-6.1-sol', r.resumable), ag('builder', 'builder', 'builder', 'claude', 'claude-opus-5-5', r.resumable),
        ag('reviewer', 'reviewer', 'reviewer', 'claude', 'claude-sonnet-5', false)].slice(0, Math.max(1, Math.round(r.agents / r.teams)));
      const fail = r.tasks.failed > 0 && !i;
      return { id: `team${i + 1}`, name: i ? '검토팀' : '구현팀', agents,
        tasks: !r.tasks.total ? [] : [
          { id: 't1', assignee: 'builder', status: 'done', objective: '집계 함수 고치기', result: '합계를 다시 계산했습니다.\n<b>굵게 아님</b> & <script>alert(1)</script>\n- app/main.py 3줄 수정', updated_at: r.started_at + 600 },
          ...(fail ? [{ id: 't2', assignee: 'builder', status: 'failed', objective: '의존성 설치', result: '네트워크가 막혀 설치하지 못했습니다.', updated_at: r.started_at + 900 },
            { id: 't3', assignee: 'reviewer', status: 'failed', objective: '검토', result: '', updated_at: r.started_at + 1200 }] : []) ],
        posts: !r.posts || i ? [] : [
          { id: 1, ts: r.started_at + 700, role: 'builder', title: '매출 대시보드', note: '', file: 'out/dashboard.html', kind: 'html', url: page('매출 대시보드', [40, 65, 30, 85]) },
          { id: 2, ts: r.started_at + 800, role: 'builder', title: '구조도', note: '', file: 'out/diagram.svg', kind: 'image', url: svg },
          { id: 3, ts: r.started_at + 900, role: 'reviewer', title: '검토 메모', note: '', file: 'notes/review.md', kind: 'text', url: txt },
          { id: 4, ts: r.started_at + 950, role: 'builder', title: '지워진 보고서', note: '', file: 'out/old.html', kind: 'missing', url: null } ] };
    });
    return { id: r.id, project: r.project, workspace: r.workspace, started_at: r.started_at, harness: 'C:\\Users\\user\\.codex\\AGENTS.md', extras: ['gsd-core'],
      tools: { browser: true, pc: false, ponytail: true }, worktrees: true, teams };
  }
  const CYCLE = ['busy', 'idle', 'waiting_approval', 'booting', 'configuring', 'idle', 'busy', 'idle']; // an agent ends only when it is stopped
  const closers = new Map(), sessions = new Map(); let nextTeam = 1;
  let st = { phase: 'setup' }, t0 = 0, seq = 0, grown = false, posted = false;
  const echo = new Map();

  const label = (a) => catalog.cli[a.cli].models.find((m) => m.id === a.model)?.label || a.model;
  const agent = (role, job, a, name) => ({ role, name: name || role, job, cli: a.cli, model: a.model, model_label: label(a), effort: a.effort,
    alive: true, status: 'configuring', ctx: null, unread: 0, breaker: 'healthy', paused: false });

  function build(cfg) {
    t0 = Date.now(); seq = 0; grown = false; posted = false; round = -1; cursor = 0; nextTeam = cfg.teams.length + 1; sessions.clear();
    const hm = cfg.harness?.mode || 'auto';
    st = { phase: 'running', project: cfg.project || '이름 없는 프로젝트', workspace: cfg.workspace, started_at: t0 / 1000,
      harness: hm === 'off' ? null : hm === 'path' ? cfg.harness.path : scanData.harness.auto.path,
      extras: (cfg.extras || []).map((id) => scanData.extras.find((x) => x.id === id)).filter(Boolean).map(({ name, kind }) => ({ name, kind })),
      teams: cfg.teams.map((t, i) => {
      const count = {};
      const agents = [agent('main', 'main', t.main, t.main.name), ...t.subs.map((s) => {
        count[s.job] = (count[s.job] || 0) + 1;
        return agent(s.name || s.job + (count[s.job] > 1 ? count[s.job] : ''), s.job, s); // a sub's name is its id
      })];
      const who = agents[1]?.role || 'main', now = t0 / 1000;
      const posts = i ? [] : [
        post(1, who, who, '매출 대시보드', 'html', 'out/dashboard.html', page('매출 대시보드', [40, 65, 30, 85]), '1차 시안입니다', 900),
        post(2, who, who, '구조도', 'image', 'out/diagram.svg', svg, '', 600),
        post(3, agents[agents.length - 1].role, agents[agents.length - 1].name, '검토 메모', 'text', 'notes/review.md', txt, '마크다운은 글자 그대로 보입니다', 300),
        post(4, who, who, '지워진 보고서', 'missing', 'out/old-report.html', null, '', 120),
      ];
      return { id: `team${i + 1}`, name: t.name || `팀 ${i + 1}`, agents, posts, events: [], tasks: [
        { id: `20261125T1015${i}0-ab12cd34`, assignee: who, status: 'in_progress', objective: '설정 마법사 화면의 입력 검증 추가', updated_at: now },
        { id: `20261125T1016${i}0-9f3e77aa`, assignee: who, status: 'open', objective: '터미널 크기 조절 시 PTY 리사이즈 전달', updated_at: now },
        { id: `20261125T1009${i}0-55aa01bc`, assignee: agents[agents.length - 1].role, status: 'done', objective: '스파이크 코드 검토', updated_at: now - 300 },
        { id: `20261125T1002${i}0-0c0ffee0`, assignee: who, status: 'failed', objective: '의존성 설치 (네트워크 차단으로 실패)', updated_at: now - 600 },
      ] };
    }) };
  }

  function tick() {
    if (st.phase !== 'running') return;
    const el = Date.now() - t0, step = Math.floor(el / 6000);
    let i = 0;
    if (!posted && el > 15000) { // a result arrives a while after the start
      posted = true; const t = st.teams[0], a = t.agents[1] || t.agents[0];
      t.posts.push(post(5, a.role, a.name, '매출 대시보드 v2', 'html', 'out/dashboard.html', page('매출 대시보드 v2', [55, 70, 45, 90, 60]), '막대를 하나 더했습니다', 0));
    }
    for (const team of st.teams) {
      if (!grown && el > 20000 && team === st.teams[0]) { // a main opened one more sub later on
        grown = true; team.agents.push(agent('builder9', 'builder', catalog.defaults.builder));
      }
      for (const a of team.agents) {
        a.status = a.stopped ? 'ended' : CYCLE[(i + step) % CYCLE.length]; a.alive = a.status !== 'ended';
        a.ctx = a.alive ? `${(12 + i * 9 + step * 3) % 96}% (${(40 + i * 21.3).toFixed(1)}k/475k)` : null;
        a.unread = (i + step) % 4 === 1 ? 2 : 0; a.breaker = i === 3 ? 'open' : 'healthy'; a.paused = i === 5;
        a.note = i === 1 && a.status === 'waiting_approval' ? '폴더 신뢰 확인: 터미널에서 직접 답하세요' : null; // the CLI's trust prompt
        i++;
      }
    }
    playScript(el);
  }

  const preset = { project: '가을 매출 대시보드', workspace: catalog.workspace, extras: ['plugin:gsd-core@gsd-core', 'skill:claude:make-pdf'], teams: [
    { main: catalog.defaults.main, subs: ['builder', 'builder', 'reviewer'].map((job) => ({ job, ...catalog.defaults[job] })) },
    { main: { name: '총괄', cli: 'claude', model: 'claude-sonnet-5', effort: 'high' }, subs: ['builder', 'reviewer'].map((job, k) => ({ job, name: k ? '' : 'conflict-builder', ...catalog.defaults.main })) },
  ] };
  if (q.get('phase') === 'running') {
    build(preset); seedWt(preset);
    // events that were already there before the page looked: they must not be replayed as animations
    for (const kind of ['task', 'PreToolUse', 'task_done']) st.teams[0].events.push({ seq: ++seq, ts: t0 / 1000 - 60, role: 'builder', kind, text: '이전 기록', data: { assignee: 'builder', tool: 'Edit' } });
  }

  window.HubMock = {
    catalog: async () => structuredClone(catalog),
    state: async () => { tick(); return structuredClone(st); },
    async start(cfg) {
      await sleep(1500);
      if (/fail/i.test(cfg.workspace)) throw new Error('작업 폴더를 찾을 수 없습니다 (mock 오류)');
      window.__lastStart = structuredClone(cfg); // lets a test read what was sent
      build(cfg); seedWt(cfg);
    },
    async stop() {
      const n = (st.teams || []).reduce((s, t) => s + t.agents.length, 0), all = [...wt.values()], kept = all.filter((w) => w.files.length);
      st = { phase: 'setup' }; echo.clear(); wt.clear();
      return { released: { agents: n, processes: n * 2, left: 0, worktrees: all.length ? { removed: all.length - kept.length, kept: kept.map(({ team, role, path, branch }) => ({ team, role, path, branch })) } : undefined } };
    },
    async changes() {
      await sleep(250);
      if (!wt.size) return { enabled: false, items: [] };
      return structuredClone({ enabled: true, repo: scanData.git, items: [...wt.values()] });
    },
    async diff(team, role) { await sleep(200); return wt.get(`${team}/${role}`)?.files.length ? FAKE_DIFF : ''; },
    async mergeChanges(team, role) {
      await sleep(500);
      const w = wt.get(`${team}/${role}`);
      if (/conflict/.test(role)) throw new Error('프로젝트에 적용하지 못했습니다 (충돌 1개): app/main.py (mock 오류)');
      if (!w?.files.length) return { ok: true, applied: 0, note: '합칠 변경이 없습니다' };
      const n = w.files.length; w.merged.push({ commit: 'c0ffee1', files: n }); w.files = [];
      return { ok: true, applied: n, files: [] };
    },
    async discardChanges(team, role) {
      await sleep(400);
      if (wtAgent(team, role)?.alive) throw new Error('에이전트가 실행 중입니다. 끝난 뒤에 버릴 수 있습니다 (mock 오류)');
      wt.delete(`${team}/${role}`); return { ok: true };
    },
    async addTeam(body) { // a team name containing "fail" is refused, to show the error path
      await sleep(600);
      if (/fail/i.test(body.name)) throw new Error('같은 이름의 팀이 이미 있습니다 (mock 오류)');
      if (st.teams.length >= catalog.limits.max_teams) throw new Error('팀을 더 만들 수 없습니다');
      window.__lastTeam = structuredClone(body);
      const id = `team${nextTeam++}`; // never reuse the number of a team that was stopped
      st.teams.push({ id, name: body.name, posts: [], events: [], tasks: [],
        agents: [agent('main', 'main', body.main, body.main.name), ...body.subs.map((s) => agent(s.name, s.job, s))] });
      return { ok: true, team: id };
    },
    async scan(workspace) { // "scanfail" in the path fails the scan, "noagents" finds no AGENTS.md
      await sleep(400);
      if (/scanfail/i.test(workspace)) throw new Error('작업 폴더를 읽을 수 없습니다 (mock 오류)');
      const d = structuredClone(scanData);
      if (/nogit/i.test(workspace)) d.git = null; // "nogit" in the path: not a repository
      if (/noagents/i.test(workspace)) d.harness.auto = null;
      d.tools = { browser: { ok: true, detail: 'Playwright MCP (mock)' }, pc: { ok: true, detail: 'agent-desktop MCP (mock)' },
        ponytail: { ok: true, detail: 'ponytail (mock)' } };
      return d;
    },
    async runs() {
      await sleep(300);
      const cur = st.phase === 'running' ? [{ id: 'current-run', project: st.project, workspace: st.workspace, workspace_ok: true, started_at: st.started_at, teams: st.teams.length,
        agents: st.teams.reduce((n, t) => n + t.agents.length, 0), tasks: { total: 2, done: 0, failed: 0 }, posts: 0, resumable: true, current: true }] : [];
      return structuredClone({ runs: [...cur, ...RUNS.map((r) => ({ ...r, current: false }))] });
    },
    async runDetail(id) {
      await sleep(250);
      if (id === 'current-run') return structuredClone({ id, project: st.project, workspace: st.workspace, started_at: st.started_at, harness: st.harness, extras: [], tools: null, worktrees: null,
        teams: st.teams.map((t) => ({ id: t.id, name: t.name, agents: t.agents.map((a) => ({ role: a.role, name: a.name, job: a.job, cli: a.cli, model: a.model, effort: a.effort, resumable: true })), tasks: [], posts: [] })) });
      const r = RUNS.find((x) => x.id === id); if (!r) throw new Error('그런 실행 기록이 없습니다');
      return structuredClone(runDetailOf(r));
    },
    async resumeRun(id) {
      await sleep(900);
      if (st.phase === 'running') throw new Error('이미 실행 중입니다. 먼저 정지하세요.');
      const r = RUNS.find((x) => x.id === id); if (!r) throw new Error('그런 실행 기록이 없습니다');
      if (!r.workspace_ok) throw new Error('작업 폴더가 없습니다');
      const d = runDetailOf(r), pick = (a) => ({ name: a.name, cli: a.cli, model: a.model, effort: a.effort });
      const cfg = { project: d.project, workspace: d.workspace, harness: { mode: 'path', path: d.harness }, worktrees: d.worktrees,
        teams: d.teams.map((t) => ({ name: t.name, main: pick(t.agents[0]), subs: t.agents.slice(1).map((a) => ({ job: a.job, ...pick(a), name: a.role })) })) };
      window.__resumed = id; build(cfg); seedWt(cfg);
      return { ok: true };
    },
    async say(team, role, text) {
      await sleep(150);
      const t = st.teams.find((x) => x.id === team), a = t?.agents.find((x) => x.role === role);
      if (!a) throw new Error('그런 에이전트가 없습니다');
      if (!a.alive) throw new Error('에이전트가 끝났습니다. 다시 시작한 뒤에 보내세요 (mock 오류)');
      if (a.note) throw new Error('이 에이전트는 폴더 신뢰 확인을 기다립니다. 터미널에서 먼저 답하세요.');
      window.__lastSay = { team, role, text };
      echo.get(`${team}/${role}`)?.(`\r\n\x1b[33m[지시]\x1b[0m ${text.replace(/\n/g, '\r\n       ')}\r\n> `);
      t.events.push({ seq: ++seq, ts: Date.now() / 1000, role: 'human', kind: 'say', text: '', data: { to: role, subject: text.split('\n')[0].slice(0, 60) } });
      return { ok: true };
    },
    async stopAgent(team, role) {
      await sleep(300);
      const a = st.teams.find((x) => x.id === team)?.agents.find((x) => x.role === role);
      if (!a) throw new Error('그런 에이전트가 없습니다');
      a.stopped = true; closers.get(`${team}/${role}`)?.();
      return { ok: true, released: { agents: 1, processes: 3, left: 0 } };
    },
    async restartAgent(team, role) {
      await sleep(400);
      const a = st.teams.find((x) => x.id === team)?.agents.find((x) => x.role === role);
      if (!a) throw new Error('그런 에이전트가 없습니다');
      closers.get(`${team}/${role}`)?.(); a.stopped = false;
      sessions.set(`${team}/${role}`, (sessions.get(`${team}/${role}`) || 1) + 1);
      return { ok: true };
    },
    async stopTeam(team) {
      await sleep(400);
      const t = st.teams.find((x) => x.id === team);
      if (!t) throw new Error('그런 팀이 없습니다');
      const mine = [...wt.values()].filter((w) => w.team === team), kept = mine.filter((w) => w.files.length);
      mine.filter((w) => !w.files.length).forEach((w) => wt.delete(`${w.team}/${w.role}`));
      t.agents.forEach((a) => closers.get(`${team}/${a.role}`)?.());
      const released = { agents: t.agents.length, processes: t.agents.length * 2, left: 0,
        worktrees: mine.length ? { removed: mine.length - kept.length, kept: kept.map(({ role, path, branch }) => ({ team, role, path, branch })) } : undefined };
      st.teams = st.teams.filter((x) => x !== t);
      if (st.teams.length) return { ok: true, ended_run: false, released };
      st = { phase: 'setup' }; echo.clear(); wt.clear(); // the last team: the whole run is over
      return { ok: true, ended_run: true, released };
    },
    out(team, role, onChunk, signal) {
      return new Promise((resolve) => {
        const w = (s) => onChunk(enc.encode(s)), key = `${team}/${role}`;
        w(`\x1b[1;33m hub-desk mock \x1b[0m ${team}/${role} · 세션 ${sessions.get(key) || 1}\r\n\x1b[2m가짜 터미널입니다. 입력한 글자는 그대로 돌아옵니다.\x1b[0m\r\n\r\n`);
        w('0123456789'.repeat(10) + '\r\n> ');
        let n = 0;
        const id = setInterval(() => w(`\r\n\x1b[36m[${role}]\x1b[0m 작업 로그 ${++n}\r\n> `), 4000);
        echo.set(key, w);
        const end = () => { clearInterval(id); if (closers.get(key) === end) closers.delete(key); resolve(); };
        closers.set(key, end); // the stream ends when the agent is stopped or restarted
        signal?.addEventListener('abort', end);
        if (st.teams?.find((x) => x.id === team)?.agents.find((x) => x.role === role)?.stopped) end();
      });
    },
    send: async (team, role, text) => { echo.get(`${team}/${role}`)?.(text.replace(/\r/g, '\r\n> ')); },
    resize: async () => {},
    async folders() {
      await sleep(300);
      const now = Date.now() / 1000;
      return { folders: [
        { path: 'C:\\proj\\shop-web', name: 'shop-web', sources: ['vscode-open', 'vscode'], ts: now, project: '', ok: true, why: '', git: 'main', harness: 'AGENTS.md', files: 42 },
        { path: 'D:\\work\\가을-매출', name: '가을-매출', sources: ['hub-desk'], ts: now - 3600, project: '가을 매출 분석', ok: true, why: '', git: null, harness: null, files: 7 },
        { path: 'vscode-remote://wsl%2Bubuntu/home/me/robot', name: 'robot', sources: ['vscode'], ts: now - 7200, project: '', ok: false, why: '원격 폴더 (WSL·SSH·컨테이너)' },
      ] };
    },
    async pickFolder() { await sleep(800); return { path: 'C:\\proj\\picked-in-dialog', git: null, harness: null, files: 3 }; },
  };
})();
