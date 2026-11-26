// hub-desk front end: setup wizard, then the running dashboard. No framework, no build step.
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const u = encodeURIComponent;

  // ---------- API ----------
  const H = { 'X-Token': $('meta[name="hub-token"]')?.content || '' };
  async function call(path, opt = {}) {
    const r = await fetch(path, { ...opt, headers: { ...H, ...(opt.headers || {}) } });
    let j = null;
    try { j = await r.json(); } catch { /* empty or non-JSON body */ }
    if (!r.ok) throw new Error(j?.error || `HTTP ${r.status}`);
    return j;
  }
  const postJson = (path, body) => call(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const api = window.HubMock || {
    catalog: () => call('/api/catalog'),
    state: () => call('/api/state'),
    start: (cfg) => postJson('/api/start', cfg),
    stop: () => call('/api/stop', { method: 'POST' }),
    addTeam: (body) => postJson('/api/teams', body),
    changes: () => call('/api/changes'),
    async diff(team, role) {
      const r = await fetch(`/api/changes/${u(team)}/${u(role)}/diff`, { headers: H });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.text();
    },
    mergeChanges: (team, role) => call(`/api/changes/${u(team)}/${u(role)}/merge`, { method: 'POST' }),
    discardChanges: (team, role) => call(`/api/changes/${u(team)}/${u(role)}/discard`, { method: 'POST' }),
    say: (team, role, text) => postJson(`/api/say/${u(team)}/${u(role)}`, { text }),
    stopAgent: (team, role) => call(`/api/agents/${u(team)}/${u(role)}/stop`, { method: 'POST' }),
    restartAgent: (team, role) => call(`/api/agents/${u(team)}/${u(role)}/restart`, { method: 'POST' }),
    stopTeam: (team) => call(`/api/teams/${u(team)}/stop`, { method: 'POST' }),
    runs: () => call('/api/runs'),
    runDetail: (id) => call(`/api/runs/${u(id)}`),
    resumeRun: (id) => call(`/api/runs/${u(id)}/resume`, { method: 'POST' }),
    // A WebSocket: streamed fetches held one of the browser's 6 connections each, and a 7th terminal froze the page.
    // Resolves when the process ends (the server closes with 1000), rejects on any other close.
    out(team, role, onChunk, signal, onOpen) {
      return new Promise((resolve, reject) => {
        const ws = new WebSocket(`ws://${location.host}/api/out/${u(team)}/${u(role)}?t=${u(H['X-Token'])}`);
        ws.binaryType = 'arraybuffer';
        let opened = false;
        const abort = () => ws.close();
        signal.addEventListener('abort', abort, { once: true });
        ws.onmessage = (e) => {
          if (!opened) { opened = true; if (onOpen) onOpen(Number(e.data) || 0); return; } // first message: backlog size
          onChunk(new Uint8Array(e.data));
        };
        ws.onclose = (e) => {
          signal.removeEventListener('abort', abort);
          if (e.code === 1000 && opened && !signal.aborted) resolve(); else reject(new Error(`closed ${e.code}`));
        };
      });
    },
    scan: (workspace) => postJson('/api/scan', { workspace }),
    folders: () => call('/api/folders'),
    pickFolder: (initial) => postJson('/api/folders/pick', { initial }),
    send: (team, role, text) => call(`/api/in/${u(team)}/${u(role)}`, { method: 'POST', body: text }),
    resize: (team, role, cols, rows) => postJson(`/api/resize/${u(team)}/${u(role)}`, { cols, rows }),
  };

  const JOB_KO = { main: '메인', builder: '구현', reviewer: '검토' };
  const STATUS_KO = { configuring: '설정 중', booting: '시작 중', busy: '작업 중', idle: '대기', waiting_approval: '승인 필요', ended: '종료' };
  const statusOf = (a) => (a.alive === false ? 'ended' : a.status);

  // ======================= setup wizard =======================
  const STEPS = ['팀 규모', '에이전트 설정', '하네스·스킬', '확정'];
  const S = { catalog: null, step: 1, confirmed: false, starting: false, error: '', errAt: '', cfg: null, scan: null, filter: '' };
  let preview = null;

  // Make an agent's cli/model/effort valid for the catalog.
  function fixAgent(a) {
    const clis = S.catalog.cli;
    if (!clis[a.cli]) a.cli = Object.keys(clis)[0];
    const c = clis[a.cli];
    if (!c.models.some((m) => m.id === a.model)) a.model = c.models[0]?.id || '';
    if (!c.efforts.includes(a.effort)) a.effort = c.efforts.includes(c.default_effort) ? c.default_effort : c.efforts[0];
    return a;
  }
  const fromDefaults = (kind) => fixAgent({ ...(S.catalog.defaults?.[kind] || {}) });
  const newSub = (job) => ({ job, ...fromDefaults(job) });
  function defaultSubs(n) {
    const jobs = S.catalog.jobs, has = (j) => (jobs.includes(j) ? j : jobs[0]);
    // one sub builds; from two on, one of them reviews
    return Array.from({ length: n }, (_, i) => newSub(n >= 2 && i === n - 1 ? has('reviewer') : has('builder')));
  }
  const newTeam = () => ({ main: { name: 'main', ...fromDefaults('main') }, subs: defaultSubs(2), touched: false });
  const teamLabel = (t, i) => (t.tname || '').trim().slice(0, 20) || `팀 ${i + 1}`; // an empty team name falls back to its number
  // Same naming the server uses: builder, builder2, reviewer, ...
  function roleNames(subs) {
    const n = {};
    return subs.map((s) => { n[s.job] = (n[s.job] || 0) + 1; return s.job + (n[s.job] > 1 ? n[s.job] : ''); });
  }

  // ---------- agent names ----------
  // A sub's name is its id inside the team; the main's id stays "main" and its name is display only.
  const nameRule = () => S.catalog.name_rule || { pattern: '^[\\w-]{1,32}$', reserved: ['main', 'board', 'human', 'broadcast'], hint: '글자·숫자·-·_ 1~32자' };
  let nameRe = null;
  function nameRegex() {
    if (nameRe) return nameRe;
    // The server's \w is Unicode-aware; in JS \w stays ASCII even with the u flag, so spell it out.
    const src = nameRule().pattern.replace(/\[\\w/g, '[\\p{L}\\p{N}_').replace(/\\w/g, '[\\p{L}\\p{N}_]');
    try { nameRe = new RegExp(src, 'u'); } catch { nameRe = /^[\p{L}\p{N}_-]{1,32}$/u; }
    return nameRe;
  }
  // Untouched names keep following the default rule; an edited one stays as typed.
  function subNames(t) { const d = roleNames(t.subs); return t.subs.map((s, i) => (s.nameTouched ? s.name : d[i])); }
  function nameErrors(t) {
    const names = subNames(t), rule = nameRule(), low = names.map((n) => n.toLowerCase()), mn = (t.main.name || '').trim();
    return {
      main: !mn ? '이름을 입력하세요.' : mn.length > 24 ? '24자 이하로 적으세요.' : '',
      subs: names.map((n, i) => ((rule.reserved || []).includes(low[i]) ? `"${n}"은(는) 따로 쓰이는 이름이라 붙일 수 없습니다.`
        : !nameRegex().test(n) ? `이름에는 ${rule.hint || '글자·숫자·-·_'}만 쓸 수 있습니다.`
          : low.indexOf(low[i]) !== low.lastIndexOf(low[i]) ? '같은 팀에 같은 이름이 있습니다.' : '')),
    };
  }
  function firstBadName() { // id of the first invalid name input, or ''
    for (const [ti, t] of S.cfg.teams.entries()) {
      const e = nameErrors(t), i = e.subs.findIndex(Boolean);
      if (e.main) return `a-${ti}-m-name`;
      if (i >= 0) return `a-${ti}-${i}-name`;
    }
    return '';
  }
  function syncNames(ti, t = S.cfg.teams[ti]) { // update the messages in place so typing never loses the caret
    const e = nameErrors(t);
    [['m', e.main], ...e.subs.map((m, i) => [i, m])].forEach(([k, msg]) => {
      const inp = $(`#a-${ti}-${k}-name`), p = $(`#a-${ti}-${k}-err`); if (!inp) return;
      inp.toggleAttribute('aria-invalid', !!msg); if (msg) inp.setAttribute('aria-invalid', 'true');
      p.textContent = msg; p.hidden = !msg;
    });
  }

  // The three edits an agent row can make; the wizard and the add-team dialog both use them.
  function editName(t, ti, el) {
    const v = el.value.trim();
    if (el.dataset.a === 'm') t.main.name = el.value;
    else { const s = t.subs[+el.dataset.a]; s.name = v; s.nameTouched = !!v; t.touched = true; } // emptied: back to the default name
    syncNames(ti, t);
  }
  function nameBlur(t, el) { // an emptied sub name falls back to its default; show that without rebuilding
    if (el.dataset.a !== 'm' && !el.value.trim()) el.value = subNames(t)[+el.dataset.a];
  }
  function editSel(t, el) {
    const a = el.dataset.a === 'm' ? t.main : t.subs[+el.dataset.a], f = el.dataset.field;
    a[f] = el.value; t.touched = true;
    if (f === 'cli') { a.model = ''; a.effort = ''; fixAgent(a); } // reset to that CLI's valid defaults
  }
  function resizeSubs(t, raw) {
    const n = Math.min(S.catalog.limits.max_subs, Math.max(0, raw | 0));
    if (!t.touched) t.subs = defaultSubs(n);
    else { while (t.subs.length < n) t.subs.push(newSub(S.catalog.jobs[0])); t.subs.length = n; }
  }

  const modelLabel = (a) => S.catalog.cli[a.cli]?.models.find((m) => m.id === a.model)?.label || a.model;

  function setCount(key, raw) {
    const L = S.catalog.limits, teams = S.cfg.teams;
    if (key === 'mains') {
      const n = Math.min(L.max_teams, Math.max(1, raw | 0));
      while (teams.length < n) teams.push(newTeam());
      teams.length = n;
    } else resizeSubs(teams[+key.split('-')[1]], raw);
  }

  const stepper = (id, v, min, max, labelId) => `
    <div class="stepper" role="group" aria-labelledby="${labelId}">
      <button type="button" id="${id}-dec" data-act="step" data-k="${id}" data-d="-1" aria-label="줄이기" ${v <= min ? 'disabled' : ''}>−</button>
      <input id="${id}" type="number" inputmode="numeric" min="${min}" max="${max}" value="${v}" data-f="num" data-k="${id}" aria-labelledby="${labelId}">
      <button type="button" id="${id}-inc" data-act="step" data-k="${id}" data-d="1" aria-label="늘리기" ${v >= max ? 'disabled' : ''}>+</button>
    </div>`;

  // An error that belongs to one field is shown under that field; anything else goes above the buttons.
  const fieldErr = (id) => (S.error && S.errAt === id ? `<p class="ferr" id="${id}-err" role="alert">${esc(S.error)}</p>` : '');
  const bad = (id) => (S.error && S.errAt === id ? `aria-invalid="true" aria-describedby="${id}-err"` : '');
  const footer = (html) => `${S.error && !S.errAt ? `<p class="error" role="alert">${esc(S.error)}</p>` : ''}<div class="wiz-foot">${html}</div>`;

  // ---------- existing project folders ----------
  const SRC_KO = { 'vscode-open': 'VS Code에서 열림', vscode: 'VS Code 최근', 'hub-desk': '이전 실행' };
  const F = { list: null, error: '', filter: '', picking: false, opener: null };
  function wsInfo() { // what the chosen folder is, in one line
    const d = S.cfg.wsInfo;
    if (!d || d.path !== S.cfg.workspace) return '모든 에이전트가 이 폴더에서 실행됩니다. 기존 프로젝트 폴더를 고르면 그 안에서 작업합니다.';
    return ['모든 에이전트가 이 폴더에서 실행됩니다.', d.git ? `git ${esc(d.git)}` : '',
      d.harness ? `${esc(d.harness)}가 있어 하네스 '자동'이 이 파일을 씁니다.` : ''].filter(Boolean).join(' · ');
  }
  function useFolder(row) {
    const prevName = (S.cfg.workspace || '').split(/[\\/]/).filter(Boolean).pop() || '';
    S.cfg.workspace = row.path; S.cfg.wsInfo = row;
    // the project name follows the folder until the person types one of their own
    const pj = S.cfg.project.trim();
    if (!pj || S.cfg.projectAuto || pj === prevName) { S.cfg.project = row.project || row.name; S.cfg.projectAuto = true; }
    S.error = ''; S.scan = null; // the harness and skills step looks again
    $('#folder-dlg').close();
  }
  function folderRows() {
    const q = F.filter.trim().toLowerCase();
    const rows = (F.list || []).filter((r) => !q || `${r.name} ${r.path} ${r.project}`.toLowerCase().includes(q));
    const item = (r, i) => `<li class="fd-item ${r.ok ? '' : 'off'}">
      <button type="button" data-fi="${i}" ${r.ok ? '' : 'disabled'} ${r.path === S.cfg.workspace ? 'aria-current="true"' : ''}>
        <span class="fd-name">${esc(r.name)}${r.project && r.project !== r.name ? ` <small>· ${esc(r.project)}</small>` : ''}</span>
        <span class="fd-path">${esc(r.path)}</span>
        <span class="fd-chips">${r.sources.map((s) => `<span class="chip${s === 'vscode-open' ? ' ok' : ''}">${SRC_KO[s] || esc(s)}</span>`).join('')}
          ${r.wsl ? `<span class="chip">WSL ${esc(r.wsl)}</span>` : ''}${r.git ? `<span class="chip">git ${esc(r.git)}</span>` : ''}${r.harness ? `<span class="chip">${esc(r.harness)}</span>` : ''}
          ${r.ok ? '' : `<span class="chip warn">${esc(r.why)} · 지원 안 함</span>`}</span>
      </button></li>`;
    const ok = rows.filter((r) => r.ok), off = rows.filter((r) => !r.ok), idx = (r) => F.list.indexOf(r);
    if (!rows.length) return `<p class="bb-empty">${F.list.length ? '찾는 폴더가 없습니다.' : '최근 폴더 기록이 없습니다. 아래의 찾아보기로 고르세요.'}</p>`;
    return `<ul class="fd-list">${ok.map((r) => item(r, idx(r))).join('')}</ul>
      ${off.length ? `<details class="fd-off"><summary>원격 폴더 ${off.length}개 (지원 안 함)</summary>
        <p class="hint">에이전트가 Windows 프로그램으로 실행되어 이 폴더들에서는 일할 수 없습니다.</p>
        <ul class="fd-list">${off.map((r) => item(r, idx(r))).join('')}</ul></details>` : ''}`;
  }
  function renderFolders() {
    $('#fd-body').innerHTML = F.list === null ? '<p class="bb-empty" role="status">폴더 목록을 읽는 중…</p>'
      : F.error ? `<p class="error" role="alert">${esc(F.error)}</p>` : folderRows();
    $('#fd-browse').disabled = F.picking;
    $('#fd-browse').textContent = F.picking ? 'Windows 폴더 창에서 고르는 중…' : '찾아보기… (Windows 폴더 창)';
  }
  async function openFolders() {
    const dlg = $('#folder-dlg');
    F.opener = document.activeElement; F.list = null; F.error = ''; F.filter = ''; $('#fd-filter').value = '';
    renderFolders(); dlg.showModal(); $('#fd-filter').focus();
    try { F.list = (await api.folders()).folders || []; } catch (e) { F.error = `목록을 읽지 못했습니다: ${e.message}`; F.list = []; }
    if (dlg.open) renderFolders();
  }
  function wireFolders() {
    const dlg = $('#folder-dlg');
    $('#fd-close').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener('close', () => { renderSetup(); (document.getElementById(F.opener?.id) || $('#f-ws'))?.focus(); });
    $('#fd-filter').addEventListener('input', (e) => { F.filter = e.target.value; renderFolders(); });
    $('#fd-body').addEventListener('click', (e) => {
      const b = e.target.closest('[data-fi]'); if (b && !b.disabled) useFolder(F.list[+b.dataset.fi]);
    });
    $('#fd-browse').addEventListener('click', async () => {
      F.picking = true; F.error = ''; renderFolders();
      try {
        const r = await api.pickFolder(S.cfg.workspace);
        if (r?.path) { const name = r.path.split(/[\\/]/).filter(Boolean).pop() || r.path; useFolder({ ...r, name, project: '' }); }
      } catch (e) { F.error = e.message; }
      finally { F.picking = false; if (dlg.open) renderFolders(); }
    });
  }

  function step1() {
    const L = S.catalog.limits, c = S.cfg;
    return `
      <h2 id="wiz-title" tabindex="-1">팀 규모</h2>
      <p class="lede">메인 한 명이 자기 팀을 지휘하는 오케스트레이터입니다. 팀끼리는 서로 독립적으로 일합니다.</p>
      <div class="field">
        <label for="f-project">프로젝트 이름</label>
        <input id="f-project" type="text" value="${esc(c.project)}" maxlength="60" required autocomplete="off" ${bad('f-project')}>
        ${fieldErr('f-project') || '<p class="hint">대시보드 맨 위에 표시됩니다. 60자까지.</p>'}
      </div>
      <div class="field">
        <label for="f-ws">작업 폴더</label>
        <div class="ws-row">
          <input id="f-ws" type="text" value="${esc(c.workspace)}" spellcheck="false" autocomplete="off" ${bad('f-ws')}>
          <button type="button" class="btn" data-act="folders" id="btn-folders">기존 폴더 선택…</button>
        </div>
        ${fieldErr('f-ws') || `<p class="hint" id="ws-info">${wsInfo()}</p>`}
      </div>
      <div class="sizes">
        <div class="size-row head">
          <span id="l-mains" class="label">메인 에이전트 수</span>
          <span class="hint">메인 한 명마다 팀이 하나 생깁니다 (최대 ${L.max_teams}팀)</span>
          ${stepper('mains', c.teams.length, 1, L.max_teams, 'l-mains')}
        </div>
        ${c.teams.map((t, i) => `
        <div class="size-row team">
          <span><label class="sr-only" for="tn-${i}">팀 ${i + 1} 이름</label>
            <input id="tn-${i}" type="text" data-f="tname" data-t="${i}" value="${esc(t.tname || '')}" placeholder="팀 ${i + 1}" maxlength="20" autocomplete="off"></span>
          <span class="hint"><span id="l-subs-${i}" class="label"><span class="sr-only">팀 ${i + 1} </span>서브 에이전트 수</span> · 메인 1명 + 서브 ${t.subs.length}명 (최대 ${L.max_subs}명)</span>
          ${stepper(`subs-${i}`, t.subs.length, 0, L.max_subs, `l-subs-${i}`)}
        </div>`).join('')}
      </div>
      ${footer('<span></span><button type="button" class="btn primary" data-act="next">다음: 에이전트 설정</button>')}`;
  }

  function agentRow(ti, key, a, name, isSub, canApply, err) {
    const id = `a-${ti}-${key}`, c = S.catalog.cli[a.cli];
    const sel = (f, label, opts) => `<label class="sr-only" for="${id}-${f}">${esc(name)} ${label}</label>
      <select id="${id}-${f}" data-f="sel" data-t="${ti}" data-a="${key}" data-field="${f}">${opts}</select>`;
    const opt = (v, text, cur) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(text)}</option>`;
    return `<div class="arow ${isSub ? '' : 'is-main'}">
      <span class="aname"><i class="sw job-${esc(a.job || 'main')}"></i>
        <label class="sr-only" for="${id}-name">${isSub ? `서브 ${key + 1}` : '메인'} 이름</label>
        <input id="${id}-name" type="text" data-f="name" data-t="${ti}" data-a="${key}" value="${esc(name)}" maxlength="${isSub ? 32 : 24}"
          spellcheck="false" autocomplete="off" aria-describedby="${id}-err" ${err ? 'aria-invalid="true"' : ''}></span>
      <span>${isSub ? sel('job', '직무', S.catalog.jobs.map((j) => opt(j, JOB_KO[j] || j, a.job)).join('')) : '<span class="fixed" title="오케스트레이터">오케스트레이터</span>'}</span>
      <span>${sel('cli', 'CLI', Object.entries(S.catalog.cli).map(([k, v]) => opt(k, v.label, a.cli)).join(''))}</span>
      <span>${sel('model', '모델', c.models.map((m) => opt(m.id, m.label + (m.verified === false ? ' (미검증)' : ''), a.model)).join(''))}</span>
      <span>${sel('effort', '추론 강도', c.efforts.map((e) => opt(e, e, a.effort)).join(''))}</span>
      <span>${canApply ? `<button type="button" class="btn small" id="${id}-apply" data-act="applyall" data-t="${ti}" data-a="${key}" aria-label="${esc(name)}의 CLI, 모델, 추론 강도를 이 팀의 다른 서브에 적용">모두에 적용</button>` : ''}</span>
    </div><p class="aerr" id="${id}-err" role="alert" ${err ? '' : 'hidden'}>${esc(err)}</p>`;
  }

  function step2() {
    return `
      <h2 id="wiz-title" tabindex="-1">에이전트 설정</h2>
      <p class="lede">에이전트마다 이름과 실행할 CLI, 모델, 추론 강도를 정합니다. CLI를 바꾸면 모델과 추론 강도는 그 CLI의 기본값으로 돌아갑니다.</p>
      <p class="hint namehint">서브의 이름은 메인이 그 에이전트를 부르는 이름입니다 (${esc(nameRule().hint || '글자·숫자·-·_')}, 팀 안에서 겹치지 않게). 메인의 이름은 화면 표시용입니다.</p>
      ${S.cfg.teams.map((t, ti) => {
        const names = subNames(t), errs = nameErrors(t);
        return `<section class="tcard" aria-labelledby="tc-${ti}">
          <h3 id="tc-${ti}">${esc(teamLabel(t, ti))}</h3>
          <div class="agrid">
            <div class="arow ahead" aria-hidden="true"><span>이름</span><span>직무</span><span>CLI</span><span>모델</span><span>추론 강도</span><span></span></div>
            ${agentRow(ti, 'm', { ...t.main, job: 'main' }, t.main.name, false, false, errs.main)}
            ${t.subs.map((s, i) => agentRow(ti, i, s, names[i], true, t.subs.length > 1, errs.subs[i])).join('')}
          </div>
        </section>`;
      }).join('')}
      ${footer('<button type="button" class="btn" data-act="prev">이전</button><button type="button" class="btn primary" data-act="next">다음: 하네스·스킬</button>')}`;
  }

  // ---------- step 3: harness file and extra skills/plugins ----------
  const HOW_KO = { 'plugin-dir': '플러그인으로 로드', mention: '경로 안내', native: '자동 인식' };
  const NATIVE_KO = { native: '자동으로 읽음', mention: '시작할 때 읽도록 안내' };
  const SCOPE_KO = { workspace: '프로젝트 파일', global: '전역 파일' };
  const cliLabel = (k) => S.catalog.cli[k]?.label || k;
  const perCli = (map, words) => Object.entries(map || {}).map(([k, v]) => `${cliLabel(k)}: ${words[v] || v}`).join(' · ');
  const maxExtras = () => S.scan?.data?.limits?.max_extras ?? Infinity;

  let scanSeq = 0;
  async function loadScan() { // runs on every entry to the step: the workspace may have changed
    const ws = S.cfg.workspace.trim(), seq = ++scanSeq;
    S.scan = { ws, loading: true };
    try {
      const data = await api.scan(ws);
      if (seq !== scanSeq) return;
      data.extras = [...(data.extras || [])].sort((a, b) => !!b.new - !!a.new); // new ones first, otherwise server order
      const ids = new Set(data.extras.map((x) => x.id)), h = S.cfg.harness;
      S.cfg.extras = new Set([...S.cfg.extras].filter((id) => ids.has(id)));
      if (!data.harness?.auto && h.mode === 'auto') { h.mode = 'off'; h.chosen = false; } // forced, not the user's pick
      else if (data.harness?.auto && h.mode === 'off' && !h.chosen) h.mode = 'auto';
      S.scan = { ws, data };
    } catch (e) { if (seq !== scanSeq) return; S.scan = { ws, error: e.message }; }
    if (S.step >= 3 && !$('#setup').hidden) renderSetup(); // step 4 too: its summary names the harness and extras
  }

  function harnessNote() {
    const h = S.cfg.harness, hs = S.scan?.data?.harness;
    if (h.mode === 'off') return '지침 파일 없이 시작합니다.';
    if (h.mode === 'path') return '두 CLI 모두 시작할 때 읽도록 안내';
    return perCli(hs?.native, NATIVE_KO);
  }
  function syncHarness() {
    $('#h-path-wrap').hidden = S.cfg.harness.mode !== 'path';
    $('#h-native').textContent = harnessNote();
  }
  function syncExtras() {
    const n = S.cfg.extras.size, max = maxExtras(), full = n >= max;
    $('#x-count').textContent = `선택 ${n}개${Number.isFinite(max) ? ` / 최대 ${max}개` : ''}`;
    $('#x-limit').hidden = !full;
    for (const cb of document.querySelectorAll('#x-list input')) {
      cb.disabled = full && !cb.checked; cb.closest('li').classList.toggle('locked', cb.disabled);
    }
  }
  function applyFilter() {
    const q = S.filter.trim().toLowerCase(); let shown = 0;
    for (const li of document.querySelectorAll('#x-list li')) { li.hidden = !!q && !li.dataset.q.includes(q); if (!li.hidden) shown++; }
    $('#x-none').hidden = shown > 0 || !document.querySelector('#x-list li');
  }

  function step3() {
    const sc = S.scan || { loading: true }, h = S.cfg.harness, back = '<button type="button" class="btn" data-act="prev">이전</button>';
    const head = `<h2 id="wiz-title" tabindex="-1">하네스·스킬</h2>
      <p class="lede">모든 에이전트가 따를 지침 파일과, 기본 포함 외에 따로 붙일 스킬·플러그인을 고릅니다.</p>`;
    if (sc.loading) return `${head}<div class="progress" role="status"><span class="bar-anim"></span>작업 폴더와 설치된 스킬을 살펴보는 중입니다…</div>
      ${footer(`${back}<button type="button" class="btn primary" disabled>다음: 확정</button>`)}`;
    if (sc.error) return `${head}<p class="error" role="alert">살펴보지 못했습니다: ${esc(sc.error)}</p>
      <p class="hint retry"><button type="button" class="btn" data-act="rescan" id="btn-rescan">다시 시도</button> 건너뛰면 하네스는 자동, 추가 항목 없이 진행합니다.</p>
      ${footer(`${back}<button type="button" class="btn primary" data-act="next">다음: 확정</button>`)}`;
    const d = sc.data, auto = d.harness?.auto;
    const radio = (mode, title, note, off) => `<label class="radio"><input type="radio" name="hmode" id="h-${mode}" value="${mode}" data-f="hmode"
      ${h.mode === mode ? 'checked' : ''} ${off ? 'disabled' : ''}><span><b>${title}</b> <span class="hint">${note}</span></span></label>`;
    const base = Object.entries(d.base || {}).map(([k, v]) => ({ k, skills: v.skills || [], plugins: v.plugins || [] }));
    return `${head}
      <fieldset class="tcard hx">
        <legend>하네스</legend>
        ${radio('auto', '자동', auto ? `${esc(auto.path)} · ${SCOPE_KO[auto.scope] || esc(auto.scope)}` : 'AGENTS.md를 찾지 못했습니다. 직접 지정하거나 사용 안 함만 고를 수 있습니다.', !auto)}
        ${radio('path', '직접 지정', '따를 파일의 경로를 적습니다.')}
        <div id="h-path-wrap" class="indent" ${h.mode === 'path' ? '' : 'hidden'}>
          <label class="sr-only" for="f-hpath">지침 파일 경로</label>
          <input id="f-hpath" type="text" value="${esc(h.path)}" spellcheck="false" autocomplete="off" placeholder="C:\\프로젝트\\AGENTS.md">
        </div>
        ${radio('off', '사용 안 함', '')}
        <p id="h-native" class="hint hnote" role="status">${esc(harnessNote())}</p>
      </fieldset>
      <fieldset class="tcard hx">
        <legend>에이전트 도구·작업 방식</legend>
        ${TOOLS.map(([k, title, warn]) => { const t = d.tools?.[k] || { ok: false, detail: '' }; return `<label class="radio" for="tool-${k}">
          <input type="checkbox" id="tool-${k}" data-f="tool" data-k="${k}" ${t.ok && S.cfg.tools[k] ? 'checked' : ''} ${t.ok ? '' : 'disabled'}>
          <span><b>${title}</b> <span class="hint">${esc(t.detail)}</span>${warn && t.ok && S.cfg.tools[k] ? `<br><span class="hint warn-text">${warn}</span>` : ''}</span></label>`; }).join('')}
        <label class="radio" for="opt-wt">
          <input type="checkbox" id="opt-wt" data-f="wt" ${d.git && S.cfg.worktrees ? 'checked' : ''} ${d.git ? '' : 'disabled'}>
          <span><b>빌더마다 작업 사본(git worktree)</b> <span class="hint">${d.git ? '빌더는 각자 사본에서 일하고, 대시보드에서 합치기를 눌러야 프로젝트에 반영됩니다 (스테이징만, 커밋은 하지 않음).' : (/^[\\/]{2}wsl(\.localhost|\$)[\\/]/i.test(S.cfg.workspace) ? 'WSL 폴더에서는 아직 쓸 수 없습니다 (Windows git이 Linux 쪽 저장소를 다루지 못함)' : '작업 폴더가 git 저장소가 아니라 쓸 수 없습니다')}</span>${
            d.git && d.git.dirty > 0 && S.cfg.worktrees ? `<br><span class="hint warn-text" id="wt-dirty">프로젝트에 커밋되지 않은 변경 ${d.git.dirty}개가 있습니다. 작업 사본은 마지막 커밋(브랜치 ${esc(d.git.branch)})에서 시작하므로 이 변경은 사본에 없습니다.</span>` : ''}</span></label>
      </fieldset>
      <section class="tcard hx" aria-labelledby="x-h">
        <h3 id="x-h">스킬·플러그인</h3>
        <details class="base">
          <summary><b>기본 포함</b> ${base.map((b) => `${esc(cliLabel(b.k))} 스킬 ${b.skills.length}개${b.plugins.length ? ` · 플러그인 ${esc(b.plugins.join(', '))}` : ''}`).join(' / ') || '없음'}</summary>
          ${base.map((b) => `<p><b>${esc(cliLabel(b.k))} 스킬</b> ${esc(b.skills.join(', ')) || '없음'}</p>`).join('')}
        </details>
        <p class="hint">기본 포함 항목은 CLI가 스스로 불러오므로 고를 필요가 없습니다.</p>
        <div class="xhead">
          <h4 id="x-title">추가 적용</h4>
          <label class="sr-only" for="x-filter">추가 항목 찾기</label>
          <input id="x-filter" type="search" value="${esc(S.filter)}" placeholder="이름이나 설명으로 찾기" autocomplete="off">
          <span id="x-count" class="xcount" role="status"></span>
        </div>
        <ul id="x-list" class="xlist" aria-labelledby="x-title">
          ${d.extras.map((x, i) => `<li data-q="${esc(`${x.name} ${x.description || ''} ${x.source || ''}`.toLowerCase())}"><label for="x-${i}">
            <input type="checkbox" id="x-${i}" data-f="extra" data-id="${esc(x.id)}" ${S.cfg.extras.has(x.id) ? 'checked' : ''}>
            <span class="xbody">
              <span class="xtop"><b>${esc(x.name)}</b><span class="chip">${x.kind === 'plugin' ? '플러그인' : '스킬'}</span>${x.new ? '<span class="chip warn">새로 추가됨</span>' : ''}</span>
              <span class="xdesc" title="${esc(x.description)}">${esc(x.description) || '설명 없음'}</span>
              <span class="xhow">${esc(x.source)} · ${esc(perCli(x.how, HOW_KO))}</span>
            </span></label></li>`).join('')}
        </ul>
        ${d.extras.length ? '' : '<p class="hint">추가로 붙일 수 있는 항목이 없습니다.</p>'}
        <p id="x-none" class="hint" hidden>찾는 항목이 없습니다.</p>
        <p id="x-limit" class="hint" hidden>한 번에 ${maxExtras()}개까지만 붙일 수 있어 나머지는 잠겼습니다. 하나를 해제하면 다시 고를 수 있습니다.</p>
      </section>
      ${footer(`${back}<button type="button" class="btn primary" data-act="next">다음: 확정</button>`)}`;
  }

  const TOOLS = [
    ['browser', '브라우저 사용', ''],
    ['pc', 'PC 제어', '실제 화면의 앱을 조작합니다. 여러 에이전트가 같은 화면을 쓰므로, 그동안 PC를 직접 쓰면 서로 방해될 수 있습니다.'],
    ['ponytail', 'ponytail 기본 적용', ''],
  ];
  function worktreeSummary() {
    if (!S.cfg.worktrees) return '사용 안 함';
    const sc = S.scan?.data;
    if (!sc) return '빌더마다 git worktree (작업 폴더가 git 저장소일 때)';
    return sc.git ? `빌더마다 git worktree (브랜치 ${sc.git.branch} 기준)` : '사용 안 함 (git 저장소 아님)';
  }
  const toolsSummary = () => TOOLS.filter(([k]) => S.cfg.tools[k] && (S.scan?.data?.tools?.[k]?.ok ?? true)).map(([, t]) => t).join(', ') || '없음';

  function harnessSummary() {
    const h = S.cfg.harness, auto = S.scan?.data?.harness?.auto;
    if (h.mode === 'off') return '사용 안 함';
    if (h.mode === 'path') return `${h.path.trim()} (직접 지정)`;
    return auto ? `${auto.path} (자동 · ${SCOPE_KO[auto.scope] || auto.scope})` : '자동 (실행할 때 서버가 찾습니다)';
  }
  const chosenExtras = () => (S.scan?.data?.extras || []).filter((x) => S.cfg.extras.has(x.id));

  // After a stop: what was given back, and which work copies are left. Shown above whichever step the
  // wizard returns to, so the report is not lost when that is not the summary.
  function stopNotice() {
    const rel = S.released, wt = rel?.worktrees;
    // work copies with unmerged changes survive a stop: say where they are
    const kept = !wt ? '' : `${wt.removed ? ` 변경 없는 작업 사본 ${wt.removed}개는 정리했습니다.` : ''}${wt.error ? ` <span class="bad-text">작업 사본 정리 오류: ${esc(wt.error)}</span>` : ''}${
      wt.kept?.length ? `<div id="kept-wt"><b class="bad-text">작업 사본이 남아 있습니다 (합치지 않은 변경):</b>
        <ul class="kept">${wt.kept.map((k) => `<li>${esc(k.role)} — ${esc(k.path)} (브랜치 ${esc(k.branch)})</li>`).join('')}</ul>
        같은 팀과 에이전트 이름으로 다시 실행하면 이 사본을 이어서 씁니다. 필요 없으면 git으로 합치거나 폴더를 직접 지우면 됩니다.</div>` : ''}`;
    const back = !S.stopped ? '' : `<div class="notice" role="status" id="stop-notice">
      <b>작업을 정지했습니다.</b>
      ${rel ? ` 자원 반환: 에이전트 ${rel.agents}개, 프로세스 ${rel.processes}개 종료${rel.left ? ` · <span class="bad-text">남은 프로세스 ${rel.left}개</span>` : ' · 남은 프로세스 없음'}.` : ''}${kept}
      설정은 그대로 남아 있습니다. <b>이전</b>이나 위의 단계 이름을 눌러 고친 뒤 다시 확정하면 됩니다.
      <button type="button" class="btn small" data-act="goto" data-step="1">처음 단계로</button></div>`;
    return back;
  }

  function step4() {
    const c = S.cfg, rows = c.teams.map((t, ti) => {
      const names = subNames(t), mn = t.main.name.trim();
      const all = [[mn, '오케스트레이터', t.main, mn !== 'main' ? 'main' : ''], ...t.subs.map((s, i) => [names[i], `${JOB_KO[s.job] || s.job}`, s, ''])];
      return all.map(([name, job, a, id], i) => `<tr>
        ${i === 0 ? `<th scope="rowgroup" rowspan="${all.length}">${esc(teamLabel(t, ti))}</th>` : ''}
        <td class="strong">${esc(name)}${id ? ` <small class="rid">${id}</small>` : ''}</td><td>${esc(job)}</td><td>${esc(S.catalog.cli[a.cli].label)}</td>
        <td>${esc(modelLabel(a))}${S.catalog.cli[a.cli].models.find((m) => m.id === a.model)?.verified === false ? ' <span class="chip warn">미검증</span>' : ''}</td><td>${esc(a.effort)}</td></tr>`).join('');
    }).join('');
    const subs = c.teams.reduce((n, t) => n + t.subs.length, 0);
    let foot;
    if (S.starting) foot = '<div class="progress" role="status"><span class="bar-anim"></span>에이전트를 자리에 앉히는 중입니다…</div>';
    else if (S.confirmed) foot = `<button type="button" class="btn" data-act="edit" id="btn-edit">수정</button>
      <button type="button" class="btn primary" data-act="run" id="btn-run">실행</button>`;
    else foot = `<button type="button" class="btn" data-act="prev">이전</button>
      <span class="foot-right"><button type="button" class="btn primary" data-act="confirm" id="btn-confirm">설정 확정</button>
      <button type="button" class="btn" disabled aria-describedby="run-hint">실행</button></span>`;
    // After a stop: say what was given back, and that the settings can be changed from here.
    return `
      <h2 id="wiz-title" tabindex="-1">확정</h2>
      <p class="lede">${S.confirmed ? '이 구성으로 고정되었습니다. 실행하면 정지할 때까지 이 구성이 유지됩니다.' : '아래 구성이 맞는지 확인한 뒤 확정하세요. 확정해야 실행할 수 있습니다.'}</p>
      <div class="summary ${S.confirmed ? 'locked' : ''}">
        <div class="sum-head">
          <span class="sum-ws"><b id="sum-project">${esc(c.project.trim())}</b> <span class="hint">작업 폴더</span> ${esc(c.workspace)}</span>
          <span class="hint">팀 ${c.teams.length}개 · 메인 ${c.teams.length}명 · 서브 ${subs}명</span>
          ${S.confirmed ? '<span class="chip ok" id="locked-chip">확정됨</span>' : '<span class="chip">확정 전</span>'}
        </div>
        <dl class="sum-more">
          <dt>하네스</dt><dd id="sum-harness">${esc(harnessSummary())}</dd>
          <dt>도구·작업 방식</dt><dd id="sum-tools">${esc(toolsSummary())}</dd>
          <dt>작업 사본</dt><dd id="sum-wt">${esc(worktreeSummary())}</dd>
          <dt>추가 스킬·플러그인</dt><dd id="sum-extras">${chosenExtras().map((x) => esc(x.name)).join(', ') || '없음'}</dd>
        </dl>
        <table>
          <thead><tr><th scope="col">팀</th><th scope="col">이름</th><th scope="col">직무</th><th scope="col">CLI</th><th scope="col">모델</th><th scope="col">추론 강도</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${S.confirmed || S.starting ? '' : '<p id="run-hint" class="hint">설정을 확정하면 실행 버튼이 켜집니다.</p>'}
      ${footer(foot)}`;
  }

  function previewTeams() {
    return S.cfg.teams.map((t, i) => ({ id: `team${i + 1}`, name: teamLabel(t, i), agents: [
      { role: 'main', name: t.main.name.trim() || 'main', job: 'main', cli: t.main.cli, status: 'busy', alive: true, unread: 0 },
      // the seat id stays fixed here so a character does not change looks while its name is being typed
      ...subNames(t).map((name, j) => ({ role: `seat${j}`, name, job: t.subs[j].job, cli: t.subs[j].cli, status: 'idle', alive: true, unread: 0 })),
    ] }));
  }

  function renderSetup(focusId) {
    const keep = focusId || document.activeElement?.id;
    const lock = S.confirmed || S.starting;
    $('#steps').innerHTML = STEPS.map((n, i) => `<li><button type="button" data-act="goto" data-step="${i + 1}"
      ${S.step === i + 1 ? 'aria-current="step"' : ''} ${lock ? 'disabled' : ''}>${n}</button></li>`).join('');
    $('#wiz-body').innerHTML = stopNotice() + [step1, step2, step3, step4][S.step - 1]();
    if ($('#x-list')) { syncExtras(); applyFilter(); }
    if (keep) {
      let el = document.getElementById(keep);
      if (el?.disabled) el = document.getElementById(keep.replace(/-(dec|inc)$/, ''));
      el?.focus();
    }
    const subs = S.cfg.teams.reduce((n, t) => n + t.subs.length, 0);
    $('#preview-count').textContent = `팀 ${S.cfg.teams.length}개 · 에이전트 ${S.cfg.teams.length + subs}명`;
    preview.setTeams(previewTeams(), null);
  }

  function go(step) {
    S.error = ''; S.errAt = '';
    const ws = S.cfg.workspace.trim(), h = S.cfg.harness, pj = S.cfg.project.trim();
    const stay = (at, msg, field) => { S.step = at; S.error = msg; S.errAt = field || ''; renderSetup(field); };
    if (step > 1 && (!pj || pj.length > 60)) return stay(1, pj ? '프로젝트 이름은 60자까지 쓸 수 있습니다.' : '프로젝트 이름을 입력하세요.', 'f-project');
    if (step > 1 && !ws) return stay(1, '작업 폴더 경로를 입력하세요.', 'f-ws');
    if (step > 2 && firstBadName()) { S.step = 2; S.error = '표시된 이름을 고친 뒤 다음으로 넘어가세요.'; return renderSetup(firstBadName()); }
    if (step === 4 && S.step === 3 && S.scan?.data && h.mode === 'path' && !h.path.trim()) { S.error = '지침 파일 경로를 입력하세요.'; return renderSetup('f-hpath'); }
    // the summary needs a finished scan (or a failed one the user chose to skip) for this workspace
    if (step === 4 && !(S.scan && S.scan.ws === ws && !S.scan.loading)) step = 3;
    if (step === 3 && !(S.step === 3 && S.scan?.loading)) loadScan();
    S.step = step; renderSetup('wiz-title');
  }

  const payload = () => ({ project: S.cfg.project.trim(), workspace: S.cfg.workspace.trim(), teams: S.cfg.teams.map((t, ti) => {
    const names = subNames(t);
    return { name: teamLabel(t, ti), main: { name: t.main.name.trim(), cli: t.main.cli, model: t.main.model, effort: t.main.effort },
      subs: t.subs.map((s, i) => ({ name: names[i], job: s.job, cli: s.cli, model: s.model, effort: s.effort })) };
  }),
    harness: S.cfg.harness.mode === 'path' ? { mode: 'path', path: S.cfg.harness.path.trim() } : { mode: S.cfg.harness.mode },
    extras: chosenExtras().map((x) => x.id), tools: { ...S.cfg.tools }, worktrees: !!S.cfg.worktrees });

  async function run() {
    S.starting = true; S.ran = true; S.error = ''; S.stopped = false; S.released = null; renderSetup();
    try {
      await api.start(payload());
      for (const t0 = Date.now(); ;) {
        const st = await api.state();
        if (st.phase === 'running') { S.starting = false; return enterDash(st); }
        if (Date.now() - t0 > 90000) throw new Error('시작 요청 후 90초 동안 실행 상태가 되지 않았습니다.');
        await sleep(700);
      }
    } catch (e) { S.starting = false; S.error = `실행하지 못했습니다: ${e.message}`; renderSetup('btn-run'); }
  }

  function wireSetup() {
    const root = $('#setup');
    root.addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]'); if (!b || b.disabled) return;
      const act = b.dataset.act;
      if (act === 'goto') go(+b.dataset.step);
      else if (act === 'next') go(S.step + 1);
      else if (act === 'prev') go(S.step - 1);
      else if (act === 'step') { setCount(b.dataset.k, +$(`#${b.dataset.k}`).value + +b.dataset.d); renderSetup(); }
      else if (act === 'applyall') {
        const t = S.cfg.teams[+b.dataset.t], src = t.subs[+b.dataset.a];
        t.subs.forEach((s) => { s.cli = src.cli; s.model = src.model; s.effort = src.effort; });
        t.touched = true; renderSetup();
      } else if (act === 'confirm') { S.confirmed = true; S.error = ''; renderSetup('btn-run'); }
      else if (act === 'edit') { S.confirmed = false; go(2); }
      else if (act === 'rescan') { loadScan(); renderSetup('wiz-title'); }
      else if (act === 'run') run();
      else if (act === 'folders') openFolders();
    });
    wireFolders();
    root.addEventListener('input', (e) => {
      const el = e.target;
      if (el.id === 'f-ws') S.cfg.workspace = el.value;
      else if (el.id === 'f-project') { S.cfg.project = el.value; S.cfg.projectAuto = false; }
      else if (el.dataset.f === 'tname') { S.cfg.teams[+el.dataset.t].tname = el.value; preview.setTeams(previewTeams(), null); }
      else if (el.dataset.f === 'name') { editName(S.cfg.teams[+el.dataset.t], +el.dataset.t, el); preview.setTeams(previewTeams(), null); }
      else if (el.id === 'f-hpath') S.cfg.harness.path = el.value;
      else if (el.id === 'x-filter') { S.filter = el.value; applyFilter(); }
    });
    root.addEventListener('change', (e) => {
      const el = e.target;
      if (el.dataset.f === 'name') nameBlur(S.cfg.teams[+el.dataset.t], el);
      else if (el.dataset.f === 'wt') { S.cfg.worktrees = el.checked; renderSetup(el.id); }
      else if (el.dataset.f === 'tool') { S.cfg.tools[el.dataset.k] = el.checked; renderSetup(el.id); }
      else if (el.dataset.f === 'hmode') { S.cfg.harness.mode = el.value; S.cfg.harness.chosen = true; S.error = ''; syncHarness(); }
      else if (el.dataset.f === 'extra') {
        // the limit is enforced here as well as by disabling the other boxes
        if (el.checked && S.cfg.extras.size >= maxExtras()) el.checked = false;
        else S.cfg.extras[el.checked ? 'add' : 'delete'](el.dataset.id);
        syncExtras();
      }
      else if (el.dataset.f === 'num') { setCount(el.dataset.k, +el.value); renderSetup(); }
      else if (el.dataset.f === 'sel') { editSel(S.cfg.teams[+el.dataset.t], el); renderSetup(); }
    });
  }

  function enterSetup() {
    $('#dash').hidden = true; $('#bar-run').hidden = true; $('#setup').hidden = false;
    $('#bar-phase').textContent = '팀 구성'; document.body.classList.remove('running');
    if (!preview) { preview = Office.create($('#preview'), { maxHeight: () => 520 }); wireSetup(); }
    renderSetup();
  }

  // ======================= dashboard =======================
  const D = { state: null, sel: null, teamId: null, view: 'single', terms: new Map(), timer: null, office: null, sig: {}, busy: false, wired: false, evSeq: new Map(), wantTeam: null };
  const keyOf = (team, a) => `${team.id}/${a.role}`;
  const allAgents = () => D.state.teams.flatMap((t) => t.agents.map((a) => ({ t, a, key: keyOf(t, a) })));
  const find = (key) => allAgents().find((x) => x.key === key);
  const nm = (a) => a.name || a.role; // what a person reads; `role` stays the id for every API path and key
  const nameIn = (team, role) => { const a = team.agents.find((x) => x.role === role); return a ? nm(a) : role; };
  const hhmmss = (ts) => { const d = new Date(ts * 1000); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':'); };

  // ---------- terminals ----------
  let cellRatio = 0; // monospace advance width per px of font size
  function ratio() {
    if (!cellRatio) {
      const c = document.createElement('canvas').getContext('2d');
      c.font = '100px "Cascadia Mono", Consolas, monospace'; cellRatio = c.measureText('W').width / 100 || 0.6;
    }
    return cellRatio;
  }

  async function flush(t) { // keys go out in order: one request at a time, later keys join the next one
    if (t.sending || !t.pending) return;
    t.sending = true; const data = t.pending; t.pending = '';
    try { await api.send(t.team, t.role, data); setConn(''); } catch { setConn('입력 전송 실패'); }
    finally { t.sending = false; if (t.pending) flush(t); }
  }

  async function pump(t) {
    const gen = t.gen = (t.gen || 0) + 1; // a restart starts a new reader; the old one must then stay quiet
    while (!t.dead && t.gen === gen) {
      try {
        // Old output is replayed first. xterm answers the terminal queries in it (colours, cursor position),
        // and those answers would be typed into the CLI as input: drop what the terminal emits until the
        // replay has been drawn.
        let left = 0;
        await api.out(t.team, t.role, (chunk) => {
          if (t.gen !== gen) return;
          if (!t.replay) { t.term.write(chunk); return; }
          t.term.write(chunk, () => { left -= chunk.length; if (left <= 0) t.replay = false; });
        }, t.abort.signal, (backlog) => { if (t.gen === gen) { left = backlog; t.replay = backlog > 0; } });
        if (t.gen === gen) { t.ended = true; t.cell.classList.add('ended'); } // clean end: the process exited
        return;
      } catch {
        if (t.dead || t.gen !== gen) return;
        await sleep(2000);
        if (!t.dead && t.gen === gen) t.term.reset(); // the stream replays from the start on reconnect
      }
    }
  }

  function ensureTerm(key) {
    let t = D.terms.get(key); if (t) return t;
    const { t: team, a } = find(key);
    const cell = document.createElement('section');
    cell.className = 'term-cell'; cell.dataset.key = key;
    cell.innerHTML = `<button type="button" class="cell-title" data-key="${esc(key)}"></button><div class="term-host"></div>`;
    $('#terms').append(cell);
    const term = new Terminal({ fontFamily: '"Cascadia Mono", Consolas, monospace', fontSize: 13, cursorBlink: true, scrollback: 5000,
      theme: { background: '#0c0e11', foreground: '#d9dde4', cursor: '#f5c04e', selectionBackground: '#3b4458' } });
    const fit = new FitAddon.FitAddon(); term.loadAddon(fit);
    t = { key, team: team.id, role: a.role, cell, host: cell.lastChild, term, fit, opened: false, pending: '', sending: false, abort: new AbortController() };
    term.onData((d) => { if (t.replay) return; t.pending += d; flush(t); });
    term.onResize(({ cols, rows }) => {
      clearTimeout(t.rz); t.rz = setTimeout(() => api.resize(t.team, t.role, cols, rows).catch(() => {}), 80);
    });
    // The TUI swallows Tab, so give keyboard users a way back out to the agent list.
    term.attachCustomKeyEventHandler((e) => {
      if (e.type === 'keydown' && e.ctrlKey && e.altKey && e.code === 'KeyA') { $('#agents .agent[aria-pressed="true"]')?.focus(); return false; }
      return true;
    });
    D.terms.set(key, t);
    return t;
  }
  function dropTerm(t) { t.dead = true; t.abort.abort(); t.term.dispose(); t.cell.remove(); D.terms.delete(t.key); }
  function restream(t) { // the agent has a new process: clear the old screen and read the new stream from its start
    t.abort.abort(); t.abort = new AbortController(); t.ended = false; t.replay = false; t.cell.classList.remove('ended');
    if (t.opened) { t.term.reset(); pump(t); } else t.gen = (t.gen || 0) + 1;
  }

  function fitTerm(t) {
    if (!t.host.offsetParent || !t.host.clientWidth) return;
    if (!t.opened) {
      t.term.open(t.host); t.opened = true; pump(t);
      t.fit.fit(); api.resize(t.team, t.role, t.term.cols, t.term.rows).catch(() => {});
    }
    // single view: the largest font from 14 down to 11 that still gives about 100 columns
    const fs = D.view === 'single' ? Math.max(11, Math.min(14, Math.floor((t.host.clientWidth - 20) / (100 * ratio())))) : 12;
    if (t.term.options.fontSize !== fs) t.term.options.fontSize = fs;
    t.fit.fit();
  }
  const fitVisible = () => D.terms.forEach(fitTerm);

  function layoutTerms(focus) {
    const box = $('#terms'), single = D.view === 'single';
    if (single) ensureTerm(D.sel); else allAgents().forEach((x) => ensureTerm(x.key));
    titles();
    box.className = `terms ${single ? 'single' : 'grid'}`;
    const n = D.terms.size;
    box.style.setProperty('--cols', single ? 1 : n <= 1 ? 1 : n <= 4 ? 2 : 3);
    D.terms.forEach((t) => { t.cell.hidden = single && t.key !== D.sel; t.cell.classList.toggle('sel', t.key === D.sel); });
    fitVisible();
    if (focus) D.terms.get(D.sel)?.term.focus();
    $('#view-single').setAttribute('aria-pressed', single); $('#view-grid').setAttribute('aria-pressed', !single);
  }

  function select(key, focus) {
    const x = find(key); if (!x) return;
    D.sel = key; D.teamId = x.t.id;
    render(); layoutTerms(focus);
  }

  // ---------- rendering ----------
  function setConn(msg) { const el = $('#bar-conn'); if (el.textContent !== msg) el.textContent = msg; }
  // "지우기" hides finished tasks and old events from this dashboard only (the board keeps them, and the run history
  // still shows every task). Remembered per run in this browser.
  const clearKey = (teamId) => `hub-desk.cleared.${D.state?.started_at || 0}.${teamId}`;
  function cleared(teamId) {
    try { return { tasks: [], events: 0, ...JSON.parse(localStorage.getItem(clearKey(teamId)) || '{}') }; }
    catch { return { tasks: [], events: 0 }; }
  }
  function clearItems(teamId, what) {
    const team = D.state?.teams.find((t) => t.id === teamId); if (!team) return;
    const c = cleared(teamId);
    if (what === 'events') c.events = Math.max(0, ...team.events.map((e) => e.seq));
    else c.tasks = [...new Set([...c.tasks, ...team.tasks.filter((t) => t.status === what).map((t) => t.id)])];
    try { localStorage.setItem(clearKey(teamId), JSON.stringify(c)); } catch { /* private mode: cleared for this page only */ }
    D.sig = {}; render();
  }
  function keepScroll(id, fn) { // a rebuild must not jump a scrolled list back to the top
    const pos = [...$(id).querySelectorAll('ul'), $(id)].map((el) => el.scrollTop);
    fn();
    [...$(id).querySelectorAll('ul'), $(id)].forEach((el, i) => { if (pos[i]) el.scrollTop = pos[i]; });
  }

  function put(id, html) { // only touch the DOM when something changed, and keep keyboard focus across the rebuild
    if (D.sig[id] === html) return;
    const el = $(id), ae = document.activeElement, k = el.contains(ae) && (ae.dataset.key || ae.dataset.team);
    D.sig[id] = html; el.innerHTML = html;
    if (k) [...el.querySelectorAll('button')].find((x) => (x.dataset.key || x.dataset.team) === k)?.focus();
  }

  function render() {
    const st = D.state, team = st.teams.find((t) => t.id === D.teamId) || st.teams[0];
    const every = allAgents(), subs = every.filter((x) => x.a.job !== 'main').length, mains = every.length - subs;
    $('#bar-ws').textContent = st.workspace; $('#bar-ws').title = st.workspace;
    const pj = st.project || '', pEl = $('#bar-project');
    pEl.hidden = !pj; pEl.textContent = pj; pEl.title = pj;
    document.title = pj ? `${pj} · hub-desk` : 'hub-desk';
    $('#bar-lock-text').textContent = `설정 고정됨 · 팀 ${st.teams.length}개 · 메인 ${mains} · 서브 ${subs}`;
    const hEl = $('#bar-harness'), xEl = $('#bar-extras'), xs = st.extras || [];
    hEl.hidden = st.harness === undefined; // older backend: no field, no chip
    hEl.textContent = st.harness ? `하네스 ${st.harness.split(/[\\/]/).pop()}` : '하네스 없음'; hEl.title = st.harness || '지침 파일 없이 실행 중';
    xEl.hidden = !xs.length; xEl.textContent = `추가 스킬 ${xs.length}`;
    xEl.title = xs.map((x) => `${x.name} (${x.kind === 'plugin' ? '플러그인' : '스킬'})`).join('\n');
    const wtKeys = new Set(cgItems().map(cgKey));
    for (const x of every) x.a.wt = wtKeys.has(x.key); // a fork mark on that desk in the office
    $('#bar-lock').title = st.teams.map((t) => `${t.name}: ${t.agents.map((a) => `${nm(a)}(${a.model_label}, ${a.effort})`).join(', ')}`).join('\n');
    const waiting = every.filter((x) => statusOf(x.a) === 'waiting_approval'), al = $('#bar-alert');
    al.hidden = !waiting.length;
    if (waiting.length) { al.textContent = `승인 필요 ${waiting.length}건: ${waiting[0].t.name} ${nm(waiting[0].a)}${waiting[0].a.note ? ` — ${waiting[0].a.note}` : ''}`; al.title = al.textContent; al.dataset.key = waiting[0].key; }

    D.office.setTeams(st.teams, D.sel, team.id);
    $('#office').setAttribute('aria-label', `사무실 평면도. ${every.map((x) => `${x.t.name} ${nm(x.a)} ${STATUS_KO[statusOf(x.a)] || ''}`).join(', ')}`);

    const maxTeams = S.catalog?.limits?.max_teams ?? 6, full = st.teams.length >= maxTeams, why = $('#addteam-why');
    $('#btn-addteam').disabled = full; why.hidden = !full; why.textContent = `최대 ${maxTeams}팀까지 만들 수 있습니다`;
    const tabs = $('#team-tabs'); tabs.hidden = st.teams.length < 2;
    put('#team-tabs', st.teams.map((t) => `<button type="button" data-team="${esc(t.id)}" aria-pressed="${t.id === team.id}">${esc(t.name)}${
      t.agents.some((a) => statusOf(a) === 'waiting_approval') ? ' <span class="pip" title="승인 필요"></span><span class="sr-only">승인 필요</span>' : ''}</button>`).join(''));

    put('#agents', team.agents.map((a) => {
      const key = keyOf(team, a), s = statusOf(a);
      return `<li><button type="button" class="agent st-${s}" data-key="${esc(key)}" aria-pressed="${key === D.sel}">
        <i class="sw job-${esc(a.job)}"></i>
        <span class="who"><b>${esc(nm(a))}</b>${nm(a) !== a.role ? `<small class="rid">${esc(a.role)}</small>` : ''}<small>${esc(S.catalog?.cli[a.cli]?.label || a.cli)} · ${esc(a.model_label || a.model)} · ${esc(a.effort)}</small></span>
        <span class="meta">
          <span class="chip st-${s}">${STATUS_KO[s] || esc(s)}</span>
          ${a.note ? `<span class="anote">${esc(a.note)}</span>` : ''}
          ${a.paused ? '<span class="chip">일시정지</span>' : ''}
          ${a.breaker && a.breaker !== 'healthy' ? `<span class="chip bad">차단기 ${esc(a.breaker)}</span>` : ''}
          ${a.unread > 0 ? `<span class="chip mail">미확인 ${a.unread}</span>` : ''}
          <small class="ctx">컨텍스트 ${a.ctx ? esc(a.ctx) : '–'}${a.procs != null ? ` · 프로세스 ${a.procs}` : ''}</small>
        </span></button>
        <span class="actl">
          <button type="button" class="btn" data-ctl="stop" data-key="stop:${esc(key)}" aria-label="${esc(nm(a))} 정지" ${s === 'ended' ? 'disabled' : ''}>정지</button>
          <button type="button" class="btn" data-ctl="restart" data-key="restart:${esc(key)}" aria-label="${esc(nm(a))} 다시 시작">다시 시작</button>
        </span></li>`;
    }).join(''));

    const cols = [['open', '대기'], ['in_progress', '진행'], ['done', '완료'], ['failed', '실패']];
    const hidden = cleared(team.id);
    keepScroll('#tasks', () => put('#tasks', cols.map(([k, label]) => {
      const all = team.tasks.filter((t) => t.status === k), list = all.filter((t) => !hidden.tasks.includes(t.id)).reverse();
      const clearable = (k === 'done' || k === 'failed') && list.length;
      return `<div class="col col-${k}"><h3><span>${label} <span class="n">${list.length}</span></span>${clearable
        ? `<button type="button" class="clr" data-clear="${k}" data-team="${esc(team.id)}" aria-label="${label} 목록 지우기">지우기</button>` : ''}</h3>${
        list.length ? `<ul>${list.map((t) => `<li title="${esc(t.id)}"><span class="obj">${esc(t.objective)}</span><small>${esc(nameIn(team, t.assignee))} · ${hhmmss(t.updated_at)}</small></li>`).join('')}</ul>`
          : `<p class="empty">${all.length ? '지운 항목만 있습니다' : '없음'}</p>`}</div>`;
    }).join('')));

    const evs = team.events.filter((e) => e.seq > hidden.events);
    $('#events-clear').hidden = !evs.length; $('#events-clear').dataset.team = team.id;
    keepScroll('#events', () => put('#events', evs.length ? evs.slice(-40).reverse().map((e) =>
      (e.kind === 'say' ? `<li><time>${hhmmss(e.ts)}</time><b>나</b><span>→ ${esc(nameIn(team, e.data?.to || ''))}: ${esc(e.data?.subject ?? e.text ?? '')}</span></li>`
        : `<li><time>${hhmmss(e.ts)}</time><b>${esc(nameIn(team, e.role))}</b><span>${esc(e.kind)} ${esc(e.text)}</span></li>`)).join('')
      : `<li class="empty">${team.events.length ? '지운 뒤 새 이벤트가 없습니다.' : '아직 이벤트가 없습니다.'}</li>`));

    const cur = find(D.sel);
    if (cur) {
      const s = statusOf(cur.a);
      $('#term-title').textContent = D.view === 'single' ? `${cur.t.name} · ${nm(cur.a)}` : '모든 터미널';
      const chip = $('#term-status'); chip.hidden = D.view !== 'single'; chip.className = `chip st-${s}`; chip.textContent = (STATUS_KO[s] || s) + (cur.a.note ? ` · ${cur.a.note}` : '');
    }
    titles();
    renderComposer();
  }

  function titles() {
    D.terms.forEach((t) => {
      const x = find(t.key); if (!x) return;
      const s = statusOf(x.a), title = `${x.t.name} · ${nm(x.a)} · ${STATUS_KO[s] || s}${x.a.note ? ` · ${x.a.note}` : ''}`, b = t.cell.firstChild;
      if (b.textContent !== title) b.textContent = title;
      t.cell.classList.toggle('wait', s === 'waiting_approval');
      if (t.sawEnded && s !== 'ended') restream(t); // it was ended and runs again (restarted here or by its main)
      t.sawEnded = s === 'ended';
    });
  }


  // ======================= board overlay =======================
  // Agents post results; the server lists them per team. Keys are team id + ":" + post id.
  const B = { open: false, sel: null, shown: null, team: null, known: new Set(), unseen: new Set(), primed: false, opener: null, seq: 0, toastT: 0 };
  const KIND_KO = { html: '화면', markdown: '문서', image: '이미지', text: '텍스트', missing: '파일 없음' };
  const TEXT_CAP = 200000;
  const BOARD_EMPTY = '아직 게시물이 없습니다. 에이전트가 결과를 게시하면 여기에 표시됩니다.';
  const allPosts = () => (D.state?.teams || []).flatMap((t) => (t.posts || []).map((p) => ({ t, p, key: `${t.id}:${p.id}` })));
  // The user is busy somewhere a pop-up would take the keyboard away from.
  const busyElsewhere = () => !!document.activeElement?.closest?.('.term-cell') || !!document.querySelector('dialog[open]:not(#board)');

  function boardBadge() {
    const posts = allPosts(), btn = $('#bar-board');
    btn.querySelector('.n').textContent = posts.length;
    btn.classList.toggle('has-new', B.unseen.size > 0);
    btn.setAttribute('aria-label', `게시판, 게시물 ${posts.length}개${B.unseen.size ? `, 새 게시물 ${B.unseen.size}개` : ''}`);
    for (const t of D.state.teams) t.boardNew = posts.some((x) => x.t === t && B.unseen.has(x.key)); // lights the board on the office wall
  }

  function boardSync() { // on every poll, before the dashboard renders
    const fresh = allPosts().filter((x) => !B.known.has(x.key));
    fresh.forEach((x) => B.known.add(x.key));
    if (!B.primed) B.primed = true; // whatever was already there when this page first looked is old news
    else if (fresh.length) {
      fresh.forEach((x) => B.unseen.add(x.key));
      const last = fresh[fresh.length - 1];
      if (B.open) $('#bb-new').hidden = false;
      else if (busyElsewhere()) toast(last); // never pull focus out of a terminal
      else openBoard(last.key);
    }
    boardBadge();
    if (B.open) renderBoard();
  }

  function toast(x) {
    const b = $('#toast-open'), btn = $('#bar-board');
    b.textContent = `새 게시물: ${x.p.title || x.p.file} — 열기`; b.dataset.key = x.key; $('#toast').hidden = false;
    btn.classList.remove('flash'); void btn.offsetWidth; btn.classList.add('flash');
    clearTimeout(B.toastT); B.toastT = setTimeout(() => { $('#toast').hidden = true; }, 15000);
  }

  function openBoard(key, teamId) {
    const dlg = $('#board');
    B.team = teamId || null;
    const scope = allPosts().filter((x) => !B.team || x.t.id === B.team);
    B.sel = key || (scope.filter((x) => B.unseen.has(x.key)).pop() || scope[scope.length - 1])?.key || null;
    $('#toast').hidden = true; $('#bb-new').hidden = true;
    if (!B.open) { B.opener = document.activeElement; B.open = true; dlg.showModal(); }
    renderBoard();
    ($('#bb-posts [aria-current="true"]') || $('#bb-close')).focus();
  }

  function renderBoard() {
    const st = D.state, posts = allPosts();
    if (!posts.some((x) => x.key === B.sel)) B.sel = null;
    if (B.sel) B.unseen.delete(B.sel); // the selected one is being read
    boardBadge();
    $('#bb-teams').hidden = st.teams.length < 2;
    put('#bb-teams', [{ id: '*', name: '전체' }, ...st.teams].map((t) =>
      `<button type="button" data-team="${esc(t.id)}" aria-pressed="${(B.team || '*') === t.id}">${esc(t.name)}</button>`).join(''));
    put('#bb-posts', !posts.length ? `<p class="bb-empty">${BOARD_EMPTY}</p>` : st.teams.filter((t) => !B.team || t.id === B.team).map((t) => {
      const list = posts.filter((x) => x.t === t).reverse(); // newest first
      return `<h3>${esc(t.name)} <span class="n">${list.length}</span></h3>${list.length ? `<ul>${list.map((x) => `<li>
        <button type="button" class="bb-item" data-key="${esc(x.key)}" aria-current="${x.key === B.sel}">
          <span class="bb-t">${B.unseen.has(x.key) ? '<span class="pip"></span><span class="sr-only">새 게시물 </span>' : ''}${esc(x.p.title || x.p.file)}</span>
          <span class="bb-s"><span class="chip">${KIND_KO[x.p.kind] || esc(x.p.kind)}</span>${esc(x.p.name || x.p.role)} · ${hhmmss(x.p.ts)}</span>
        </button></li>`).join('')}</ul>` : '<p class="bb-none">게시물 없음</p>'}`;
    }).join(''));
    showPost(false);
  }

  function showPost(force) { // the viewer is rebuilt only when the selection changes, never on a plain poll
    const x = allPosts().find((y) => y.key === B.sel), sig = x ? `${x.key}|${x.p.kind}|${x.p.url}` : '-';
    if (!force && sig === B.shown) return;
    B.shown = sig;
    const meta = $('#bb-meta'), stage = $('#bb-stage'), seq = ++B.seq;
    stage.textContent = ''; stage.className = 'bb-stage';
    if (!x) { meta.innerHTML = ''; stage.innerHTML = `<p class="bb-empty">${allPosts().length ? '왼쪽에서 게시물을 고르세요.' : BOARD_EMPTY}</p>`; return; }
    const p = x.p, title = p.title || p.file;
    meta.innerHTML = `<div class="bb-mt"><h3>${esc(title)}</h3>${['html', 'markdown', 'text'].includes(p.kind) && p.url ? '<button type="button" class="btn small" id="bb-reload">새로고침</button>' : ''}</div>
      ${p.note ? `<p class="bb-note">${esc(p.note)}</p>` : ''}
      <p class="bb-by">${esc(x.t.name)} · ${esc(p.name || p.role)} · ${esc(p.file)} · ${hhmmss(p.ts)}</p>`;
    fillStage(stage, p, () => seq === B.seq);
  }

  // Draws one post (html, image, text or missing) into a stage box. `live` tells whether the box still
  // shows this post when a fetch comes back. The board and the run history both use it.
  function fillStage(stage, p, live) {
    const title = p.title || p.file;
    if (p.kind === 'html' && p.url) {
      const f = document.createElement('iframe');
      f.setAttribute('sandbox', 'allow-scripts'); // scripts only: no same-origin, no top navigation, no forms, no popups
      f.setAttribute('referrerpolicy', 'no-referrer');
      f.title = title; stage.append(f);
      // The page goes in as srcdoc, not src: some embedders (the Claude desktop preview pane) block a frame
      // from navigating, and a blank board is worse than one extra fetch. <base> keeps the page's own
      // relative css/js/images loading from where the file lives.
      fetch(p.url).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }).then((html) => {
        if (!live()) return;
        const base = p.url.startsWith('/') ? `<base href="${esc(new URL(p.url, location.href).href)}">` : '';
        f.srcdoc = base + html;
      }).catch((e) => { if (live()) { f.remove(); stage.innerHTML = `<p class="bb-empty">파일을 불러오지 못했습니다: ${esc(e.message)}</p>`; } });
    } else if (p.kind === 'markdown' && p.url) {
      const f = document.createElement('iframe');
      f.setAttribute('sandbox', ''); // a rendered document needs no scripts at all
      f.setAttribute('referrerpolicy', 'no-referrer');
      f.title = title; stage.append(f);
      fetch(p.url).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }).then((md) => {
        if (!live()) return;
        const base = p.url.startsWith('/') ? `<base href="${esc(new URL(p.url, location.href).href)}">` : '';
        f.srcdoc = `<!doctype html><meta charset="utf-8">${base}<style>${Md.MD_CSS}</style>${Md.mdToHtml(md.slice(0, TEXT_CAP))}`;
      }).catch((e) => { if (live()) { f.remove(); stage.innerHTML = `<p class="bb-empty">파일을 불러오지 못했습니다: ${esc(e.message)}</p>`; } });
    } else if (p.kind === 'image' && p.url) {
      const img = document.createElement('img'); img.alt = title; img.src = p.url; stage.classList.add('dark'); stage.append(img);
    } else if (p.kind === 'text' && p.url) {
      const pre = document.createElement('pre'); pre.tabIndex = 0; pre.textContent = '불러오는 중…'; stage.append(pre);
      fetch(p.url).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }).then((txt) => {
        if (!live()) return;
        pre.textContent = txt.slice(0, TEXT_CAP);
        if (txt.length > TEXT_CAP) {
          const n = document.createElement('p'); n.className = 'bb-cut';
          n.textContent = `앞부분 ${TEXT_CAP.toLocaleString()}자만 표시했습니다 (전체 ${txt.length.toLocaleString()}자).`; stage.prepend(n);
        }
      }).catch((e) => { if (live()) pre.textContent = `파일을 불러오지 못했습니다: ${e.message}`; });
    } else {
      stage.innerHTML = `<p class="bb-empty">${p.kind === 'missing' || !p.url ? '이 파일은 볼 수 없습니다. 지워졌거나 프로젝트 폴더 밖에 있습니다.' : '이 형식은 여기서 볼 수 없습니다.'}<br>${esc(p.file)}</p>`;
    }
  }

  function wireBoard() {
    const dlg = $('#board');
    $('#bar-board').addEventListener('click', () => openBoard());
    $('#bb-close').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); }); // the backdrop
    dlg.addEventListener('close', () => {
      B.open = false; B.shown = null; $('#bb-stage').textContent = ''; // drop the iframe so a posted page stops running
      const o = B.opener; B.opener = null; if (o && o.isConnected) o.focus();
    });
    $('#bb-posts').addEventListener('click', (e) => { const b = e.target.closest('.bb-item'); if (b) { B.sel = b.dataset.key; renderBoard(); } });
    $('#bb-teams').addEventListener('click', (e) => {
      const b = e.target.closest('[data-team]'); if (!b) return;
      B.team = b.dataset.team === '*' ? null : b.dataset.team;
      const scope = allPosts().filter((x) => !B.team || x.t.id === B.team);
      if (!scope.some((x) => x.key === B.sel)) B.sel = scope[scope.length - 1]?.key || null;
      renderBoard();
    });
    $('#bb-big').addEventListener('click', (e) => { e.currentTarget.setAttribute('aria-pressed', dlg.classList.toggle('big')); });
    $('#bb-meta').addEventListener('click', (e) => { if (e.target.id === 'bb-reload') showPost(true); });
    $('#toast-open').addEventListener('click', (e) => openBoard(e.currentTarget.dataset.key));
    $('#toast-close').addEventListener('click', () => { $('#toast').hidden = true; });
  }

  // ---------- add a team while the run is going ----------
  const T = { team: null, defName: '', error: '', busy: false, opener: null };
  function renderTeamDlg(focusId) {
    const t = T.team, L = S.catalog.limits, names = subNames(t), errs = nameErrors(t), keep = focusId || document.activeElement?.id;
    $('#td-body').innerHTML = `
      <div class="field">
        <label for="td-name">팀 이름</label>
        <input id="td-name" type="text" maxlength="20" value="${esc(t.tname || '')}" placeholder="${esc(T.defName)}" autocomplete="off">
      </div>
      <div class="sizes"><div class="size-row head">
        <span id="l-td-subs" class="label">서브 에이전트 수</span>
        <span class="hint">메인 1명 + 서브 ${t.subs.length}명 (최대 ${L.max_subs}명)</span>
        ${stepper('td-subs', t.subs.length, 0, L.max_subs, 'l-td-subs')}
      </div></div>
      <div class="agrid td-grid">
        <div class="arow ahead" aria-hidden="true"><span>이름</span><span>직무</span><span>CLI</span><span>모델</span><span>추론 강도</span><span></span></div>
        ${agentRow('x', 'm', { ...t.main, job: 'main' }, t.main.name, false, false, errs.main)}
        ${t.subs.map((s, i) => agentRow('x', i, s, names[i], true, false, errs.subs[i])).join('')}
      </div>`;
    const e = $('#td-err'); e.hidden = !T.error; e.textContent = T.error;
    $('#td-submit').disabled = T.busy; $('#td-submit').textContent = T.busy ? '팀을 만드는 중…' : '팀 추가';
    if (keep) {
      let el = document.getElementById(keep);
      if (el?.disabled) el = document.getElementById(keep.replace(/-(dec|inc)$/, ''));
      el?.focus();
    }
  }
  function openTeamDlg() {
    T.team = newTeam(); T.defName = `팀 ${D.state.teams.length + 1}`; T.error = ''; T.busy = false; T.opener = document.activeElement;
    renderTeamDlg(); $('#team-dlg').showModal(); $('#td-name').focus();
  }
  async function submitTeam() {
    const t = T.team, errs = nameErrors(t), bi = errs.subs.findIndex(Boolean);
    if (errs.main || bi >= 0) { T.error = '표시된 이름을 고친 뒤 다시 누르세요.'; return renderTeamDlg(errs.main ? 'a-x-m-name' : `a-x-${bi}-name`); }
    const names = subNames(t), pick = (a) => ({ cli: a.cli, model: a.model, effort: a.effort });
    const body = { name: (t.tname || '').trim().slice(0, 20) || T.defName, main: { name: t.main.name.trim(), ...pick(t.main) },
      subs: t.subs.map((s, i) => ({ name: names[i], job: s.job, ...pick(s) })) };
    T.busy = true; T.error = ''; renderTeamDlg();
    try {
      const r = await api.addTeam(body);
      T.busy = false; D.wantTeam = r?.team || null; // its tab is selected as soon as the state shows it
      $('#team-dlg').close(); poll();
    } catch (e) { T.busy = false; T.error = `팀을 추가하지 못했습니다: ${e.message}`; renderTeamDlg('td-submit'); }
  }
  function wireTeamDlg() {
    const dlg = $('#team-dlg');
    $('#btn-addteam').addEventListener('click', openTeamDlg);
    dlg.addEventListener('close', () => { const o = T.opener; T.opener = null; if (o && o.isConnected) o.focus(); });
    dlg.addEventListener('click', (e) => {
      if (e.target === dlg || e.target.closest('#td-close, #td-cancel')) return dlg.close();
      const b = e.target.closest('[data-act="step"]');
      if (b && !b.disabled) { resizeSubs(T.team, +$('#td-subs').value + +b.dataset.d); renderTeamDlg(); }
      else if (e.target.closest('#td-submit')) submitTeam();
    });
    dlg.addEventListener('input', (e) => {
      const el = e.target;
      if (el.id === 'td-name') T.team.tname = el.value;
      else if (el.dataset.f === 'name') editName(T.team, 'x', el);
    });
    dlg.addEventListener('change', (e) => {
      const el = e.target;
      if (el.dataset.f === 'name') nameBlur(T.team, el);
      else if (el.dataset.f === 'num') { resizeSubs(T.team, +el.value); renderTeamDlg(); }
      else if (el.dataset.f === 'sel') { editSel(T.team, el); renderTeamDlg(); }
    });
  }

  // Hub events drive the office animations. Each one plays once; what is already there on the first look
  // (a fresh page, a reload, a team seen for the first time) is not replayed.
  function fxSync() {
    for (const t of D.state.teams) {
      const evs = t.events || [], top = evs.length ? evs[evs.length - 1].seq : 0, last = D.evSeq.get(t.id);
      if (last !== undefined) evs.filter((e) => e.seq > last).slice(-12).forEach((e) => { D.office.fx(t.id, e); if (e.kind === 'task_done') loadChanges(false); }); // finished work: the count may have changed
      D.evSeq.set(t.id, Math.max(last || 0, top));
    }
  }

  // ======================= changes: the builders' work copies =======================
  // Each builder works in its own git worktree; nothing reaches the project until the person merges it here.
  // The list makes the server run git, so it is loaded on demand and at most every 10 s, never per state poll.
  const C = { data: null, open: false, sel: null, diffFor: '', diff: null, msg: '', bad: false, busy: false, at: 0, timer: 0, opener: null };
  const DIFF_CAP = 400000;
  const cgKey = (it) => `${it.team}/${it.role}`;
  const cgItems = () => (C.data?.enabled ? C.data.items || [] : []);
  const cgWho = (it) => { const t = D.state?.teams.find((x) => x.id === it.team), a = t?.agents.find((x) => x.role === it.role); return { t, a, name: a ? nm(a) : it.role }; };
  const cgSum = (it) => (it.files || []).reduce((s, f) => { s.a += f.add || 0; s.d += f.del || 0; return s; }, { a: 0, d: 0 });

  function changesBadge() {
    const b = $('#bar-changes'), n = cgItems().filter((it) => it.files?.length).length;
    b.hidden = !C.data?.enabled; b.querySelector('.n').textContent = n;
    b.setAttribute('aria-label', `변경 사항, 합치지 않은 작업 사본 ${n}개`);
  }
  async function loadChanges(byUser) {
    if (!byUser && Date.now() - C.at < 9500) return;
    C.at = Date.now();
    try { C.data = await api.changes(); }
    catch (e) { if (C.open) { C.msg = `변경 목록을 읽지 못했습니다: ${e.message}`; C.bad = true; } }
    if (!D.state) return; // stopped meanwhile
    changesBadge(); if (C.open) renderChanges();
  }

  function diffHtml(text) { // plain text from the server: every line is escaped, colour comes from its first characters
    return text.slice(0, DIFF_CAP).split('\n').map((l) => {
      const c = /^(diff --git|\+\+\+|---|index )/.test(l) ? 'f' : l.startsWith('@@') ? 'h' : l[0] === '+' ? 'a' : l[0] === '-' ? 'd' : '';
      return `<span class="dl ${c}">${esc(l) || ' '}</span>`;
    }).join('') + (text.length > DIFF_CAP ? `<span class="dl h">… (${(text.length - DIFF_CAP).toLocaleString()}자 생략)</span>` : '');
  }

  function renderChanges() {
    const items = cgItems(), repo = C.data?.repo;
    if (!items.some((it) => cgKey(it) === C.sel)) { const first = items.find((it) => it.files?.length) || items[0]; C.sel = first ? cgKey(first) : null; }
    $('#cg-repo').textContent = repo ? `저장소 ${repo.root} · 브랜치 ${repo.branch} · 합치면 프로젝트에 스테이징만 됩니다` : '';
    const teams = [...new Set(items.map((it) => it.team))];
    put('#cg-list', !items.length ? '<p class="cg-none">작업 사본이 없습니다. 빌더가 있는 팀을 git 저장소에서 실행하면 여기에 나타납니다.</p>' : teams.map((tid) => {
      const tn = D.state.teams.find((t) => t.id === tid)?.name || tid;
      return `<h3>${esc(tn)}</h3><ul>${items.filter((it) => it.team === tid).map((it) => {
        const s = cgSum(it), n = it.files?.length || 0, k = cgKey(it);
        return `<li><button type="button" class="cg-item" data-key="${esc(k)}" aria-current="${k === C.sel}">
          <b>${esc(cgWho(it).name)}</b><small>${esc(it.branch || '')}</small>
          <span class="cg-meta">${it.error ? '<span class="chip bad">읽기 오류</span>' : n ? `파일 ${n} · <span class="add">+${s.a}</span> <span class="del">−${s.d}</span>` : '<span class="hint">변경 없음</span>'}
            ${it.merged?.length ? `<span class="chip ok">합침 ${it.merged.length}회</span>` : ''}
            ${it.resumed ? '<span class="chip warn" title="이전 실행에서 합치지 않은 변경이 이 사본에 그대로 있습니다">이전 작업 이어짐</span>' : ''}</span>
        </button></li>`;
      }).join('')}</ul>`;
    }).join(''));

    const it = items.find((x) => cgKey(x) === C.sel), msg = $('#cg-msg');
    msg.hidden = !C.msg; msg.textContent = C.msg; msg.className = C.bad ? 'bad' : ''; msg.setAttribute('role', C.bad ? 'alert' : 'status');
    if (!it) { put('#cg-top', ''); put('#cg-files', ''); put('#cg-diff', ''); return; }
    const who = cgWho(it), n = it.files?.length || 0, alive = !!who.a && statusOf(who.a) !== 'ended';
    put('#cg-top', `<h3>${esc(who.t?.name || it.team)} · ${esc(who.name)}</h3>
      <span class="td-gap"></span>
      <button type="button" class="btn small" data-key="cg-refresh" ${C.busy ? 'disabled' : ''}>새로고침</button>
      <button type="button" class="btn small primary" data-key="cg-merge" ${C.busy || !n ? 'disabled' : ''}>합치기</button>
      <button type="button" class="btn small danger" data-key="cg-discard" ${C.busy || alive ? 'disabled' : ''} aria-describedby="cg-why">버리기</button>
      <p class="cg-path">사본 위치 ${esc(it.path || '')} <button type="button" class="btn small" data-key="cg-copy">경로 복사</button></p>
      <p class="hint" id="cg-why">${alive ? '에이전트가 실행 중이라 버릴 수 없습니다. 에이전트가 끝난 뒤에 버릴 수 있습니다.' : '버리면 사본과 합치지 않은 변경이 지워집니다.'}</p>`);
    put('#cg-files', it.error ? `<li class="bad-text">${esc(it.error)}</li>` : n ? it.files.map((f) =>
      `<li><span class="p">${esc(f.path)}</span>${f.add == null ? '<span class="hint">바이너리</span>' : `<span class="add">+${f.add}</span><span class="del">−${f.del}</span>`}</li>`).join('')
      : '<li class="hint">합치지 않은 변경이 없습니다.</li>');
    const sig = `${C.sel}|${JSON.stringify(it.files || [])}`;
    if (sig !== C.diffFor) { // fetch the diff only when this copy's file list changed
      C.diffFor = sig; C.diff = null;
      if (n) api.diff(it.team, it.role).then((t) => { if (C.diffFor === sig) { C.diff = t; if (C.open) renderChanges(); } })
        .catch((e) => { if (C.diffFor === sig) { C.diff = `변경 내용을 읽지 못했습니다: ${e.message}`; if (C.open) renderChanges(); } });
      else C.diff = '';
    }
    put('#cg-diff', C.diff === null ? '<span class="dl">변경 내용을 읽는 중…</span>' : diffHtml(C.diff));
  }

  function confirmDlg(title, text, okLabel, danger) {
    const d = $('#cg-confirm'), ok = $('#cgc-ok');
    $('#cgc-title').textContent = title; $('#cgc-text').textContent = text;
    ok.textContent = okLabel; ok.className = `btn ${danger ? 'danger' : 'primary'}`;
    d.returnValue = ''; d.showModal();
    return new Promise((res) => d.addEventListener('close', () => res(d.returnValue === 'ok'), { once: true }));
  }
  async function cgAct(kind) {
    const it = cgItems().find((x) => cgKey(x) === C.sel); if (!it || C.busy) return;
    const n = it.files?.length || 0, name = cgWho(it).name;
    const yes = kind === 'merge'
      ? await confirmDlg('프로젝트에 합치기', `변경 ${n}개 파일을 프로젝트에 스테이징합니다. 커밋은 하지 않습니다.`, '합치기')
      : await confirmDlg('작업 사본 버리기', `${name}의 작업 사본과 아직 합치지 않은 변경 ${n}개 파일을 지웁니다. 지운 뒤에는 되돌릴 수 없습니다.`, '버리기', true);
    if (!yes) return;
    C.busy = true; C.bad = false; C.msg = kind === 'merge' ? '합치는 중…' : '버리는 중…'; renderChanges();
    try {
      const r = await api[kind === 'merge' ? 'mergeChanges' : 'discardChanges'](it.team, it.role);
      C.msg = kind !== 'merge' ? `${name}의 작업 사본을 버렸습니다.`
        : r?.applied ? `파일 ${r.applied}개를 프로젝트에 스테이징했습니다. 커밋은 직접 하면 됩니다.` : (r?.note || '합칠 변경이 없습니다');
    } catch (e) { C.msg = e.message; C.bad = true; }
    C.busy = false; C.diffFor = '';
    await loadChanges(true);
  }
  function openChanges() {
    const dlg = $('#changes');
    C.opener = document.activeElement; C.open = true; C.msg = ''; C.bad = false; C.diffFor = '';
    renderChanges(); dlg.showModal();
    ($('#cg-list [aria-current="true"]') || $('#cg-close')).focus();
    loadChanges(true);
    C.timer = setInterval(() => loadChanges(false), 10000);
  }
  function wireChanges() {
    const dlg = $('#changes');
    $('#bar-changes').addEventListener('click', openChanges);
    $('#cg-close').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener('close', () => { C.open = false; clearInterval(C.timer); const o = C.opener; C.opener = null; if (o && o.isConnected) o.focus(); });
    $('#cg-list').addEventListener('click', (e) => { const b = e.target.closest('.cg-item'); if (b) { C.sel = b.dataset.key; C.msg = ''; renderChanges(); } });
    $('#cg-top').addEventListener('click', (e) => {
      const k = e.target.closest('button')?.dataset.key;
      if (k === 'cg-refresh') { C.diffFor = ''; C.msg = ''; loadChanges(true); }
      else if (k === 'cg-merge') cgAct('merge');
      else if (k === 'cg-discard') cgAct('discard');
      else if (k === 'cg-copy') {
        const it = cgItems().find((x) => cgKey(x) === C.sel);
        navigator.clipboard?.writeText(it?.path || '').then(() => { C.msg = '경로를 복사했습니다.'; C.bad = false; renderChanges(); }, () => {});
      }
    });
  }

  // ======================= say, and stop / restart of one agent or one team =======================
  function note(msg) { // a short report that goes away by itself; it never takes the focus
    const el = $('#note'); el.textContent = msg; el.hidden = false;
    clearTimeout(note.t); note.t = setTimeout(() => { el.hidden = true; }, 8000);
  }

  async function agentCtl(kind, key) {
    const x = find(key); if (!x) return;
    const name = nm(x.a), alive = statusOf(x.a) !== 'ended';
    if (kind === 'stop') {
      if (!await confirmDlg('에이전트 정지', `${name}을(를) 끝내고 띄운 프로세스를 모두 돌려받습니다. 대화 내용은 이어지지 않습니다.`, '정지', true)) return;
    } else if (alive && !await confirmDlg('에이전트 다시 시작', `${name}을(를) 닫고 같은 설정으로 새로 띄웁니다. 지금까지의 대화 내용은 이어지지 않습니다.`, '다시 시작')) return;
    try {
      if (kind === 'stop') {
        const rel = (await api.stopAgent(x.t.id, x.a.role))?.released || {};
        note(`${name} 정지 · 프로세스 ${rel.processes ?? 0}개 반납${rel.left ? ` · 남은 프로세스 ${rel.left}개` : ''}`);
      } else {
        await api.restartAgent(x.t.id, x.a.role);
        // A running agent may never show as ended in between, so its terminal is switched to the new stream here;
        // an ended one is switched when the state shows it alive again.
        const t = D.terms.get(key); if (t && alive) { restream(t); t.sawEnded = false; }
        note(`${name}을(를) 다시 시작했습니다. 새 대화로 시작합니다.`);
      }
    } catch (e) { note(`${name}: ${kind === 'stop' ? '정지' : '다시 시작'}하지 못했습니다 — ${e.message}`); }
    await poll();
    // the stop button is disabled once the agent has ended: keep the keyboard on the row's other control
    if (!document.activeElement || document.activeElement === document.body) [...document.querySelectorAll('#agents [data-ctl="restart"]')].find((b) => b.dataset.key === `restart:${key}`)?.focus();
  }

  async function stopTeam() {
    const st = D.state, team = st.teams.find((t) => t.id === D.teamId) || st.teams[0], last = st.teams.length === 1;
    const copies = cgItems().filter((it) => it.team === team.id && it.files?.length).length;
    const text = `${team.name}의 에이전트 ${team.agents.length}명을 모두 끝내고 띄운 프로세스를 돌려받습니다.`
      + (copies ? ` 합치지 않은 변경이 있는 작업 사본 ${copies}개는 지우지 않고 남깁니다.` : C.data?.enabled ? ' 변경이 없는 작업 사본은 정리하고, 합치지 않은 변경이 있는 사본은 남깁니다.' : '')
      + (last ? ' 마지막 팀이라 실행 전체가 정지되고 팀 구성 화면으로 돌아갑니다.' : '');
    if (!await confirmDlg('팀 정지', text, '팀 정지', true)) return;
    try {
      const r = await api.stopTeam(team.id), rel = r?.released || {}, kept = rel.worktrees?.kept?.length || 0;
      if (r?.ended_run) { S.released = r.released || null; S.stopped = true; return leaveDash(); } // same as the whole-run stop
      [...D.terms.values()].filter((t) => t.team === team.id).forEach(dropTerm);
      if (find(D.sel)?.t.id === team.id) D.sel = null; // the next state picks an agent of a team that is left
      D.teamId = st.teams.find((t) => t.id !== team.id)?.id || null;
      note(`${team.name} 정지 · 에이전트 ${rel.agents ?? team.agents.length}개, 프로세스 ${rel.processes ?? 0}개 반납`
        + (rel.left ? ` · 남은 프로세스 ${rel.left}개` : '') + (kept ? ` · 합치지 않은 작업 사본 ${kept}개는 남겼습니다 (변경 창에서 확인)` : ''));
      await poll(); loadChanges(true);
    } catch (e) { note(`팀을 정지하지 못했습니다 — ${e.message}`); }
  }

  // ---------- composer: type an instruction to one agent ----------
  const Y = { to: null, lastSel: null, hist: [], hi: -1, busy: false, msg: '', bad: false };
  function sayTarget() {
    let x = find(Y.to);
    if (!x) { // default: the selected agent, else the current team's main
      const team = D.state.teams.find((t) => t.id === D.teamId) || D.state.teams[0];
      x = find(D.sel) || allAgents().find((y) => y.t === team); Y.to = x?.key || null;
    }
    return x;
  }
  function renderComposer() {
    if (D.sel !== Y.lastSel) { Y.lastSel = D.sel; Y.to = D.sel; } // choosing an agent aims the box at it; switching views does not
    const x = sayTarget(), sel = $('#say-to');
    const html = D.state.teams.map((t) => `<optgroup label="${esc(t.name)}">${t.agents.map((a) =>
      `<option value="${esc(keyOf(t, a))}">${esc(nm(a))}${statusOf(a) === 'ended' ? ' (종료)' : ''}</option>`).join('')}</optgroup>`).join('');
    if (D.sig.sayTo !== html) { D.sig.sayTo = html; sel.innerHTML = html; }
    if (x && sel.value !== x.key) sel.value = x.key;
    const why = !x ? '받을 에이전트가 없습니다.'
      : statusOf(x.a) === 'ended' ? `${nm(x.a)}은(는) 끝난 에이전트입니다. 다시 시작한 뒤에 보낼 수 있습니다.`
        : x.a.note && statusOf(x.a) === 'waiting_approval' ? `${nm(x.a)}이(가) 폴더 신뢰 확인을 기다립니다. 터미널에서 먼저 답하세요.` : '';
    $('#say-send').disabled = !!why || Y.busy;
    const p = $('#say-status'), text = why || Y.msg;
    if (p.textContent !== text) p.textContent = text;
    p.className = `say-status${why || Y.bad ? ' bad' : ''}`;
  }
  function growSay() { const b = $('#say-text'); b.style.height = 'auto'; b.style.height = `${Math.min(b.scrollHeight + 2, 140)}px`; }
  async function say() {
    const box = $('#say-text'), text = box.value, x = sayTarget();
    if (!text.trim() || !x || $('#say-send').disabled) return;
    Y.busy = true; Y.bad = false; Y.msg = '보내는 중…'; renderComposer();
    try {
      await api.say(x.t.id, x.a.role, text);
      if (Y.hist[Y.hist.length - 1] !== text) Y.hist.push(text);
      if (Y.hist.length > 50) Y.hist.shift();
      Y.hi = -1; box.value = ''; growSay();
      Y.msg = `${nm(x.a)}에게 보냈습니다 (${hhmmss(Date.now() / 1000)})`;
    } catch (e) { Y.msg = `보내지 못했습니다: ${e.message}`; Y.bad = true; }
    Y.busy = false; if (D.state) renderComposer();
    box.focus(); poll();
  }
  function wireControls() {
    const box = $('#say-text');
    $('#composer').addEventListener('submit', (e) => { e.preventDefault(); say(); });
    $('#say-to').addEventListener('change', (e) => { Y.to = e.target.value; Y.msg = ''; renderComposer(); });
    box.addEventListener('input', growSay);
    box.addEventListener('keydown', (e) => {
      if (e.isComposing || e.keyCode === 229) return; // Korean input: that Enter only finishes the syllable
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); say(); }
      else if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && Y.hist.length && (!box.value || (Y.hi >= 0 && box.value === Y.hist[Y.hi]))) {
        e.preventDefault(); // an empty box (or an untouched recalled line) walks the history of this session
        Y.hi = e.key === 'ArrowUp' ? (Y.hi < 0 ? Y.hist.length - 1 : Math.max(0, Y.hi - 1)) : (Y.hi < 0 ? -1 : Y.hi + 1);
        if (Y.hi >= Y.hist.length) Y.hi = -1;
        box.value = Y.hi < 0 ? '' : Y.hist[Y.hi]; growSay();
      }
    });
    $('#btn-stopteam').addEventListener('click', stopTeam);
  }

  // ======================= run history, and resuming a run =======================
  const R = { list: null, error: '', filter: '', sel: null, detail: null, dErr: '', post: null, shown: '', seq: 0, busy: false, msg: '', bad: false, opener: null };
  const TASK_KO = { open: '대기', in_progress: '진행', done: '완료', failed: '실패' };
  const when = (ts) => { const d = new Date(ts * 1000); return `${d.getMonth() + 1}월 ${d.getDate()}일 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
  const shortPath = (p) => { const parts = String(p || '').split(/[\\/]/).filter(Boolean); return parts.length > 2 ? `…\\${parts.slice(-2).join('\\')}` : String(p || ''); };
  function setHtml(sel, html) { // rebuild only on change, and keep the keyboard where it was
    const el = $(sel); if (el.__h === html) return;
    const ae = document.activeElement, k = el.contains(ae) && (ae.dataset.run || ae.dataset.post || ae.id);
    el.__h = html; el.innerHTML = html;
    if (k) (document.getElementById(k) || [...el.querySelectorAll('button')].find((b) => (b.dataset.run || b.dataset.post) === k))?.focus();
  }
  const runMain = (t) => t.agents.find((a) => a.job === 'main' || a.role === 'main') || t.agents[0];

  function renderRuns() {
    const q = R.filter.trim().toLowerCase();
    const rows = (R.list || []).filter((r) => !q || `${r.project} ${r.workspace}`.toLowerCase().includes(q));
    setHtml('#rn-list', R.list === null ? '<p class="cg-none" role="status">실행 기록을 읽는 중…</p>'
      : R.error ? `<p class="error" role="alert">${esc(R.error)}</p>`
        : !rows.length ? `<p class="cg-none">${R.list.length ? '찾는 기록이 없습니다.' : '아직 실행 기록이 없습니다.'}</p>`
          : `<ul>${rows.map((r) => `<li><button type="button" class="cg-item" data-run="${esc(r.id)}" aria-current="${r.id === R.sel}">
            <b>${esc(r.project || r.id)}</b><small title="${esc(r.workspace)}">${when(r.started_at)} · ${esc(shortPath(r.workspace))}</small>
            <span class="cg-meta"><span class="chip">팀 ${r.teams} · 에이전트 ${r.agents}</span><span class="chip">작업 ${r.tasks?.done ?? 0}/${r.tasks?.total ?? 0}</span>${
              r.tasks?.failed ? `<span class="chip bad">실패 ${r.tasks.failed}</span>` : ''}${r.posts ? `<span class="chip">게시 ${r.posts}</span>` : ''}${
              r.resumable ? '<span class="chip ok">이어서 가능</span>' : ''}${r.workspace_ok === false ? '<span class="chip warn">폴더 없음</span>' : ''}${
              r.current ? '<span class="chip st-busy">지금 실행 중</span>' : ''}</span>
          </button></li>`).join('')}</ul>`);
    renderRunDetail();
  }

  function renderRunDetail() {
    const row = (R.list || []).find((r) => r.id === R.sel), d = R.detail?.id === R.sel ? R.detail : null;
    const msg = $('#rn-msg'); msg.hidden = !R.msg; msg.textContent = R.msg; msg.className = `cg-msg${R.bad ? ' bad' : ''}`; msg.setAttribute('role', R.bad ? 'alert' : 'status');
    if (!row || !d) {
      setHtml('#rn-detail', !row ? '<p class="cg-none">왼쪽에서 실행 기록을 고르세요.</p>' : R.dErr ? `<p class="error" role="alert">기록을 읽지 못했습니다: ${esc(R.dErr)}</p>` : '<p class="cg-none" role="status">기록을 읽는 중…</p>');
      setHtml('#rn-posts', ''); $('#rn-postbox').hidden = true; R.shown = ''; return;
    }
    const agents = d.teams.flatMap((t) => t.agents), cont = agents.filter((a) => a.resumable).length, running = !!D.state;
    const why = running ? '실행 중에는 이어서 실행할 수 없습니다' : row.current ? '지금 실행 중인 기록입니다' : row.workspace_ok === false ? '작업 폴더가 없어 이어서 실행할 수 없습니다' : '';
    const tools = d.tools ? TOOLS.filter(([k]) => d.tools[k]).map(([, t]) => t).join(', ') || '없음' : null;
    setHtml('#rn-detail', `
      <div class="cg-top"><h3>${esc(d.project || d.id)}</h3><span class="td-gap"></span>
        <button type="button" class="btn small" id="rn-load" ${running || R.busy ? 'disabled' : ''}>설정만 불러오기</button>
        <button type="button" class="btn small primary" id="rn-resume" ${why || R.busy ? 'disabled' : ''} aria-describedby="rn-why">이어서 실행</button>
        <p class="cg-path">${when(d.started_at)} · ${esc(d.workspace)}</p>
        <p class="hint" id="rn-why">${why || `이어서 실행하면 에이전트 ${cont}명은 이전 대화를 이어 가고 ${agents.length - cont}명은 새로 시작합니다.`}${running ? ' 설정 불러오기도 정지한 뒤에 할 수 있습니다.' : ''}</p></div>
      <p class="rn-chips"><span class="chip" title="${esc(d.harness || '')}">하네스 ${d.harness ? esc(d.harness.split(/[\\/]/).pop()) : '없음'}</span>
        <span class="chip" title="${esc((d.extras || []).join(', '))}">추가 스킬 ${(d.extras || []).length}</span>
        ${tools === null ? '' : `<span class="chip">도구 ${esc(tools)}</span>`}
        ${d.worktrees == null ? '' : `<span class="chip">작업 사본 ${d.worktrees ? '사용' : '안 씀'}</span>`}</p>
      ${d.teams.map((t) => `<section class="rn-team"><h4>${esc(t.name || t.id)}</h4>
        <table class="rn-table"><thead><tr><th scope="col">이름</th><th scope="col">직무</th><th scope="col">CLI</th><th scope="col">모델</th><th scope="col">추론 강도</th><th scope="col">대화</th></tr></thead>
          <tbody>${t.agents.map((a) => `<tr><td class="strong">${esc(a.name || a.role)}</td><td>${esc(JOB_KO[a.job] || a.job)}</td><td>${esc(cliLabel(a.cli))}</td>
            <td>${esc(S.catalog.cli[a.cli]?.models.find((m) => m.id === a.model)?.label || a.model)}</td><td>${esc(a.effort)}</td>
            <td>${a.resumable ? '<span class="chip ok">이어짐</span>' : '<span class="chip">새로 시작</span>'}</td></tr>`).join('')}</tbody></table>
        ${(t.tasks || []).length ? `<ul class="rn-tasks">${t.tasks.map((k) => {
          const who = t.agents.find((a) => a.role === k.assignee), head = `<span class="chip ${k.status === 'failed' ? 'bad' : k.status === 'done' ? 'ok' : ''}">${TASK_KO[k.status] || esc(k.status)}</span> <b>${esc(who ? who.name || who.role : k.assignee)}</b> ${esc(k.objective)}`;
          return k.result ? `<li><details><summary>${head}</summary><pre class="rn-result">${esc(k.result)}</pre></details></li>` : `<li class="plain">${head}</li>`;
        }).join('')}</ul>` : '<p class="hint">작업 기록이 없습니다.</p>'}</section>`).join('')}`);

    const posts = d.teams.flatMap((t) => (t.posts || []).map((p) => ({ t, p, key: `${t.id}:${p.id}` })));
    if (!posts.some((x) => x.key === R.post)) R.post = posts[posts.length - 1]?.key || null;
    setHtml('#rn-posts', posts.length ? `<h4>게시물 ${posts.length}</h4><div class="rn-postlist">${posts.map((x) =>
      `<button type="button" class="btn small" data-post="${esc(x.key)}" aria-pressed="${x.key === R.post}">${esc(x.p.title || x.p.file)} <span class="chip">${KIND_KO[x.p.kind] || esc(x.p.kind)}</span></button>`).join('')}</div>` : '');
    const cur = posts.find((x) => x.key === R.post), sig = cur ? `${d.id}|${cur.key}` : '';
    $('#rn-postbox').hidden = !cur;
    if (sig !== R.shown) { // the viewer is rebuilt only when another post is chosen
      R.shown = sig; const stage = $('#rn-stage'), seq = ++R.seq;
      stage.textContent = ''; stage.className = 'bb-stage rn-stage';
      if (cur) fillStage(stage, cur.p, () => seq === R.seq);
    }
  }

  function selectRun(id) {
    R.sel = id; R.detail = null; R.dErr = ''; R.post = null; R.msg = ''; R.bad = false;
    renderRuns();
    if (id) api.runDetail(id).then((d) => { if (R.sel === id) { R.detail = d; renderRunDetail(); } })
      .catch((e) => { if (R.sel === id) { R.dErr = e.message; renderRunDetail(); } });
  }
  async function openRuns() {
    const dlg = $('#runs');
    R.opener = document.activeElement; R.list = null; R.error = ''; R.filter = ''; R.sel = null; R.detail = null; R.msg = ''; $('#rn-filter').value = '';
    renderRuns(); dlg.showModal(); $('#rn-filter').focus();
    try { R.list = (await api.runs()).runs || []; } catch (e) { R.error = `실행 기록을 읽지 못했습니다: ${e.message}`; R.list = []; }
    if (dlg.open) selectRun(R.list[0]?.id || null);
  }

  // Fill the wizard from a past run. Extras are not in the record, so the current choice of extras stays.
  function loadRunSettings(d) {
    const pick = (a) => fixAgent({ cli: a.cli, model: a.model, effort: a.effort });
    S.cfg = { ...S.cfg, project: d.project || '', workspace: d.workspace || S.cfg.workspace, projectAuto: false, wsInfo: null,
      teams: d.teams.map((t) => { const main = runMain(t); return { touched: true, tname: t.name || '',
        main: { name: main?.name || 'main', ...pick(main || {}) },
        subs: t.agents.filter((a) => a !== main).map((a) => ({ job: S.catalog.jobs.includes(a.job) ? a.job : S.catalog.jobs[0], name: a.role, nameTouched: true, ...pick(a) })) }; }),
      harness: d.harness ? { mode: 'path', path: d.harness, chosen: true } : { mode: 'off', path: '', chosen: true },
      tools: d.tools ? { ...S.cfg.tools, ...d.tools } : S.cfg.tools,
      worktrees: d.worktrees ?? S.cfg.worktrees };
    S.scan = null; S.confirmed = false; S.error = ''; S.errAt = '';
  }
  async function resumeRun() {
    const d = R.detail; if (!d || R.busy) return;
    const agents = d.teams.flatMap((t) => t.agents), cont = agents.filter((a) => a.resumable).length;
    if (!await confirmDlg('이어서 실행', `"${d.project}"을(를) 같은 설정으로 새 실행으로 시작합니다. 저장된 대화가 있는 에이전트 ${cont}명은 이전 대화를 이어 가고, ${agents.length - cont}명은 새로 시작합니다. 작업 보드는 비운 채로 시작합니다.`, '이어서 실행')) return;
    R.busy = true; R.bad = false; R.msg = '에이전트를 다시 자리에 앉히는 중입니다…'; renderRunDetail();
    try {
      await api.resumeRun(d.id);
      for (const t0 = Date.now(); ;) {
        const st = await api.state();
        if (st.phase === 'running') {
          loadRunSettings(d); S.ran = true; S.stopped = false; S.released = null; // a later stop comes back to these settings
          R.busy = false; R.msg = ''; R.opener = null; $('#runs').close();
          return enterDash(st);
        }
        if (Date.now() - t0 > 90000) throw new Error('시작 요청 후 90초 동안 실행 상태가 되지 않았습니다.');
        await sleep(700);
      }
    } catch (e) { R.busy = false; R.bad = true; R.msg = `이어서 실행하지 못했습니다: ${e.message}`; renderRunDetail(); }
  }
  function wireRuns() {
    const dlg = $('#runs');
    $('#bar-runs').addEventListener('click', openRuns);
    $('#rn-close').addEventListener('click', () => dlg.close());
    dlg.addEventListener('click', (e) => {
      if (e.target === dlg) return dlg.close();
      const run = e.target.closest('[data-run]'), post = e.target.closest('[data-post]');
      if (run) selectRun(run.dataset.run);
      else if (post) { R.post = post.dataset.post; renderRunDetail(); }
      else if (e.target.closest('#rn-resume')) resumeRun();
      else if (e.target.closest('#rn-load') && R.detail) {
        loadRunSettings(R.detail); S.step = 1; S.stopped = false; R.opener = null; dlg.close();
        renderSetup('wiz-title'); // open on the first step so everything can be edited before confirming
      }
    });
    dlg.addEventListener('close', () => { R.shown = ''; $('#rn-stage').textContent = ''; const o = R.opener; R.opener = null; if (o && o.isConnected) o.focus(); });
    $('#rn-filter').addEventListener('input', (e) => { R.filter = e.target.value; renderRuns(); });
  }

  function hint() {
    const cell = document.activeElement?.closest?.('.term-cell'), x = cell && find(cell.dataset.key);
    $('#term-hint').textContent = x ? `입력 중: ${x.t.name} ${nm(x.a)} (Ctrl+Alt+A로 목록 이동)` : '터미널을 클릭하면 키 입력이 전달됩니다';
    $('#term-hint').classList.toggle('on', !!x);
  }

  function applyState(st) {
    if (st.phase !== 'running') return leaveDash();
    D.state = st;
    for (const t of [...D.terms.values()]) if (!find(t.key)) dropTerm(t); // its agent or team is gone
    if (!find(D.sel)) { const every = allAgents(); D.sel = (every.find((x) => statusOf(x.a) === 'waiting_approval') || every[0])?.key; D.teamId = find(D.sel)?.t.id; }
    if (D.wantTeam && st.teams.some((t) => t.id === D.wantTeam)) { D.teamId = D.wantTeam; D.wantTeam = null; }
    boardSync();
    render();
    fxSync();
    // agents can join later; never rebuild existing terminals on a poll
    if (D.view === 'grid' && allAgents().some((x) => !D.terms.has(x.key))) layoutTerms(false);
    else if (!D.terms.has(D.sel)) layoutTerms(false);
  }

  async function poll() {
    if (D.busy) return; D.busy = true;
    try { const st = await api.state(); setConn(''); if (D.timer) applyState(st); }
    catch { setConn('서버 응답 없음'); }
    finally { D.busy = false; }
  }

  function leaveDash() {
    clearInterval(D.timer); D.timer = null;
    D.terms.forEach((t) => { t.dead = true; t.abort.abort(); t.term.dispose(); t.cell.remove(); });
    D.terms.clear(); D.sig = {}; D.sel = null; D.state = null; D.evSeq.clear(); D.wantTeam = null;
    Y.to = null; Y.lastSel = null; Y.msg = ''; $('#note').hidden = true;
    if ($('#team-dlg').open) $('#team-dlg').close();
    if ($('#changes').open) $('#changes').close();
    C.data = null; C.sel = null; C.at = 0; $('#bar-changes').hidden = true;
    if (B.open) $('#board').close();
    B.known.clear(); B.unseen.clear(); B.primed = false; B.sel = null; $('#toast').hidden = true;
    $('#bar-project').hidden = true; document.title = 'hub-desk';
    S.confirmed = false; S.starting = false; S.step = S.ran ? 4 : 1; // only show the summary if it is the one that ran
    enterSetup();
    // A page opened while a run was going has no scan yet: without it the summary cannot name the restored
    // harness and extras, and a new start would send none of them.
    if (S.step === 4 && !S.scan?.data) loadScan();
  }

  // Office animation: 'on' (default: the person asked for it) or 'system' (follow prefers-reduced-motion).
  const motion = () => { try { return localStorage.getItem('hub-desk.motion') || 'on'; } catch { return 'on'; } };
  function syncMotion() {
    const b = $('#btn-motion'); if (!b) return;
    const on = motion() === 'on';
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on ? '애니메이션 켬' : '애니메이션: 시스템 설정';
    b.title = on ? '시스템의 동작 줄이기 설정과 상관없이 움직임을 보여 줍니다. 누르면 시스템 설정을 따릅니다.'
      : '시스템에서 애니메이션 효과를 끄면 움직임 없이 표시합니다. 누르면 항상 켭니다.';
  }

  function wireDash() {
    D.wired = true;
    wireBoard(); wireTeamDlg(); wireChanges(); wireControls();
    $('#btn-motion').addEventListener('click', () => {
      try { localStorage.setItem('hub-desk.motion', motion() === 'on' ? 'system' : 'on'); } catch { /* private mode: stays as is */ }
      syncMotion();
    });
    syncMotion();
    D.office = Office.create($('#office'), { still: () => motion() === 'system' && matchMedia('(prefers-reduced-motion: reduce)').matches, onPick: (key) => select(key, true), onBoard: (teamId) => openBoard(null, teamId), maxHeight: () => Math.max(220, window.innerHeight * 0.42) });
    if (window.HubMock) window.__office = D.office; // mock mode only: lets a test fire an effect by hand
    $('#tasks').addEventListener('click', (e) => { const b = e.target.closest('[data-clear]'); if (b) clearItems(b.dataset.team, b.dataset.clear); });
    $('#events-clear').addEventListener('click', (e) => clearItems(e.currentTarget.dataset.team, 'events'));
    $('#agents').addEventListener('click', (e) => {
      const c = e.target.closest('[data-ctl]'), b = e.target.closest('.agent');
      if (c) agentCtl(c.dataset.ctl, c.dataset.key.slice(c.dataset.ctl.length + 1)); // the controls sit beside the row, not inside it
      else if (b) select(b.dataset.key, true);
    });
    $('#team-tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-team]'); if (b) { D.teamId = b.dataset.team; render(); } });
    $('#bar-alert').addEventListener('click', (e) => select(e.currentTarget.dataset.key, true));
    $('#view-single').addEventListener('click', () => { D.view = 'single'; render(); layoutTerms(true); });
    $('#view-grid').addEventListener('click', () => { D.view = 'grid'; render(); layoutTerms(false); });
    const box = $('#terms');
    box.addEventListener('mousedown', (e) => { // clicking a terminal selects it; xterm takes the focus itself
      const cell = e.target.closest('.term-cell');
      if (cell && cell.dataset.key !== D.sel) { D.sel = cell.dataset.key; D.teamId = find(D.sel)?.t.id; render(); D.terms.forEach((t) => t.cell.classList.toggle('sel', t.key === D.sel)); }
    });
    box.addEventListener('click', (e) => { const b = e.target.closest('.cell-title'); if (b) D.terms.get(b.dataset.key)?.term.focus(); });
    box.addEventListener('focusin', hint); box.addEventListener('focusout', () => setTimeout(hint));
    let rz; new ResizeObserver(() => { clearTimeout(rz); rz = setTimeout(fitVisible, 60); }).observe(box);
    const dlg = $('#stop-dialog');
    $('#btn-stop').addEventListener('click', () => { dlg.returnValue = ''; dlg.showModal(); });
    dlg.addEventListener('close', async () => {
      if (dlg.returnValue !== 'stop') return;
      try { S.released = (await api.stop())?.released || null; S.stopped = true; leaveDash(); } catch (e) { setConn(`정지 실패: ${e.message}`); }
    });
  }

  function enterDash(st) {
    $('#setup').hidden = true; $('#dash').hidden = false; $('#bar-run').hidden = false;
    $('#bar-phase').textContent = '실행 중'; document.body.classList.add('running');
    if (!D.wired) wireDash();
    D.view = 'single'; D.timer = setInterval(poll, 1000);
    applyState(st); hint();
    loadChanges(true); // once per run: tells whether work copies are in use at all
  }

  // ======================= boot =======================
  async function boot() {
    const el = $('#boot');
    try {
      S.catalog = await api.catalog();
      S.cfg = { project: S.catalog.project || '', workspace: S.catalog.workspace || '', teams: [newTeam()], harness: { mode: 'auto', path: '' }, extras: new Set(), tools: { browser: true, pc: true, ponytail: true }, worktrees: true };
      // The last started choice comes back, so a stop or a reload leads to the same settings, ready to edit.
      const last = S.catalog.last;
      if (last?.teams?.length) {
        const agent = (a) => fixAgent({ cli: a.cli, model: a.model, effort: a.effort });
        S.cfg = {
          project: last.project || '', workspace: last.workspace || S.cfg.workspace,
          teams: last.teams.map((t) => ({ touched: true, tname: t.name || '',
            main: { name: t.main.name || 'main', ...agent(t.main) },
            subs: (t.subs || []).map((s) => ({ job: S.catalog.jobs.includes(s.job) ? s.job : S.catalog.jobs[0], name: s.name || '', nameTouched: !!s.name, ...agent(s) })) })),
          harness: { mode: last.harness?.mode || 'auto', path: last.harness?.path || '', chosen: true },
          extras: new Set(last.extras || []),
          tools: { browser: true, pc: true, ponytail: true, ...(last.tools || {}) },
          worktrees: last.worktrees ?? true,
        };
        S.ran = true;
      }
      const st = await api.state();
      el.hidden = true;
      wireRuns();
      if (st.phase === 'running') enterDash(st); else enterSetup();
    } catch (e) {
      el.innerHTML = `서버에 연결하지 못했습니다: ${esc(e.message)} <button type="button" class="btn" id="retry">다시 시도</button>`;
      $('#retry').addEventListener('click', () => { el.textContent = '불러오는 중…'; boot(); });
    }
  }
  boot();
})();
