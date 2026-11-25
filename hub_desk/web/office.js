// Pixel office floor. All art is original and drawn here with rects; no external assets.
// Office.create(canvas, {onPick, onBoard, maxHeight}) -> {setTeams(teams, selectedKey, focusId), fx(teamId, event), relayout()}
window.Office = (() => {
  'use strict';
  const C = {
    corridor: '#101216', floorA: '#283040', floorB: '#2c3546', wall: '#3a4459', wallTop: '#4b5872', base: '#1c2029',
    pane: '#86b9e4', paneHi: '#b4d6f2', frame: '#d3dbe9', rug: '#3f3a2c', rugEdge: '#57503a',
    woodTop: '#c79260', woodEdge: '#8f6240', woodFront: '#3a2c24', chair: '#3b4150', chairHi: '#4a5163',
    mon: '#15171b', stand: '#565d68', key: '#cfd4dc', ink: '#e6e8ec', plate: '#0e1013',
    pot: '#a65a33', leaf: '#4fae6a', leafHi: '#72c98a', alert: '#ff6b4a', paper: '#f1f2f4', paperLine: '#8d96a5',
  };
  const SKIN = ['#f2c9a0', '#e3ae80', '#bd8055', '#8c5c3c'];
  const HAIR = ['#2a2321', '#5c3b23', '#c9a343', '#7c818c', '#a4482f', '#1d2a3a'];
  const SHIRT = { main: '#f5c04e', builder: '#5fb3f5', reviewer: '#7bd3a0' };
  const CLI = { claude: ['Claude', '#f0a37c'], codex: ['Codex', '#9fd8d0'] };
  const SUB = { w: 42, h: 47 }, MAIN = { w: 60, h: 50 }, WALL = 20, PAD = 6, GAP = 6, TOP = 7;

  const hash = (s) => { let h = 2166136261; for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619); return h >>> 0; };

  function buildRooms(teams) {
    return teams.map((team) => {
      const main = team.agents.find((a) => a.job === 'main' || a.role === 'main') || team.agents[0];
      const subs = team.agents.filter((a) => a !== main);
      const n = subs.length, cols = n === 4 ? 2 : Math.min(3, n), rows = n ? Math.ceil(n / cols) : 0;
      const w = Math.max(MAIN.w, cols * SUB.w) + PAD * 2, h = WALL + MAIN.h + rows * SUB.h + 4;
      const cell = (a, x, y, big) => ({ a, x, y, big, w: big ? MAIN.w : SUB.w, h: big ? MAIN.h : SUB.h, cx: big ? MAIN.w >> 1 : 29,
        key: `${team.id}/${a.role}`, seed: hash(team.id + a.role) });
      const cells = main ? [cell(main, (w - MAIN.w) >> 1, WALL, true)] : [];
      subs.forEach((a, i) => {
        const r = Math.floor(i / cols), inRow = Math.min(cols, n - r * cols);
        cells.push(cell(a, ((w - inRow * SUB.w) >> 1) + (i % cols) * SUB.w, WALL + MAIN.h + r * SUB.h, false));
      });
      return { team, w, h, cells };
    });
  }

  // Flow rooms left to right into rows of at most `avail` native pixels.
  function place(rooms, avail) {
    const rows = []; let row = null;
    for (const r of rooms) {
      if (!row || row.w + GAP + r.w > avail - 8) { row = { w: 0, h: 0, items: [] }; rows.push(row); }
      row.w += (row.items.length ? GAP : 0) + r.w; row.h = Math.max(row.h, r.h); row.items.push(r);
    }
    let y = 4, fits = true;
    for (const rw of rows) {
      let x = (avail - rw.w) >> 1; if (x < 0) fits = false;
      for (const r of rw.items) { r.x = x; r.y = y; x += r.w + GAP; }
      y += rw.h + GAP;
    }
    return { H: Math.max(y - GAP + 4, 40), fits };
  }

  function create(canvas, opt = {}) {
    const ctx = canvas.getContext('2d');
    const reduce = matchMedia('(prefers-reduced-motion: reduce)');
    // opt.still() lets the page override the system setting (Windows' animation effects off = reduced motion)
    const still = () => (opt.still ? opt.still() : reduce.matches);
    let teams = [], shown = [], focus = null, sel = null, rooms = [], m = 2, W = 0, H = 0, sig = '', last = 0;

    const px = (x, y, w, h, col) => { ctx.fillStyle = col; ctx.fillRect(x * m, y * m, w * m, h * m); };

    function relayout() {
      const cssW = canvas.parentElement.clientWidth; if (!cssW) return;
      fxs.length = 0; // seats are rebuilt below; effects in flight would point at the old ones
      const dpr = window.devicePixelRatio || 1, maxH = opt.maxHeight ? opt.maxHeight() : 400;
      const tryPick = (list, strict) => {
        rooms = buildRooms(list);
        for (let s = 4; s >= 1; s--) {
          const mm = Math.max(1, Math.round(s * dpr)), avail = Math.floor(cssW * dpr / mm), L = place(rooms, avail);
          if (L.fits && L.H * mm / dpr <= maxH && (s >= 2 || !strict)) return { mm, avail, H: L.H };
          // Last resort: too tall is tolerated at scale 2 and below rather than shrinking the art to dust.
          if (!strict && (L.fits && s <= 2 || s === 1)) return { mm, avail, H: L.H };
        }
        return null;
      };
      // Show every team when they fit at a readable scale; otherwise only the focused team's room.
      const one = teams.find((t) => t.id === focus);
      shown = teams;
      let pick = tryPick(teams, !!one);
      if (!pick) { shown = [one]; pick = tryPick(shown, false); }
      m = pick.mm; W = pick.avail; H = pick.H; place(rooms, W);
      canvas.width = W * m; canvas.height = H * m;
      canvas.style.width = `${W * m / dpr}px`; canvas.style.height = `${H * m / dpr}px`;
      markWork();
      draw(still() ? 1000 : performance.now());
    }

    function text(str, x, y, size, col, weight = 600, align = 'center') {
      const dpr = window.devicePixelRatio || 1;
      ctx.font = `${weight} ${Math.round(size * dpr)}px "Segoe UI","Malgun Gothic",sans-serif`;
      ctx.textAlign = align; ctx.textBaseline = 'middle'; ctx.fillStyle = col;
      ctx.fillText(str, x * m, y * m);
      return ctx.measureText(str).width / m;
    }


    // ---------- effects: short animations driven by hub events ----------
    // Kept in one small array (oldest dropped past FX_MAX). They only paint over the scene: no layout, no hit areas.
    const FX_MAX = 20, fxs = [];
    const ACT = { request: '#f5c04e', query: '#6cbcf7', refuse: '#ff6b4a', done: '#7bd3a0' }, NEUTRAL = '#e6e8ec';
    // 7x7 pixel icons for the tool families, as [x, y, w, h] rects
    const ICON = {
      edit: [[5, 0, 2, 2], [4, 1, 2, 2], [3, 2, 2, 2], [2, 3, 2, 2], [1, 4, 2, 2], [0, 5, 1, 2], [1, 6, 1, 1]],
      term: [[0, 1, 1, 1], [1, 2, 1, 1], [2, 3, 1, 1], [1, 4, 1, 1], [0, 5, 1, 1], [4, 5, 3, 1]],
      read: [[1, 0, 3, 1], [0, 1, 1, 3], [4, 1, 1, 3], [1, 4, 3, 1], [4, 4, 1, 1], [5, 5, 1, 1], [6, 6, 1, 1]],
      browser: [[0, 0, 7, 1], [0, 1, 1, 6], [6, 1, 1, 6], [0, 6, 7, 1], [0, 2, 7, 1], [2, 4, 3, 1]],
      desktop: [[0, 0, 7, 1], [0, 1, 1, 4], [6, 1, 1, 4], [0, 4, 7, 1], [3, 5, 1, 1], [1, 6, 5, 1]],
      hub: [[0, 1, 7, 1], [0, 2, 1, 4], [6, 2, 1, 4], [0, 5, 7, 1], [1, 2, 1, 1], [2, 3, 1, 1], [3, 4, 1, 1], [4, 3, 1, 1], [5, 2, 1, 1]],
      other: [[0, 3, 1, 1], [3, 3, 1, 1], [6, 3, 1, 1]],
    };
    const ICON_COL = { edit: '#a86a00', term: '#1f2a3a', read: '#2a5fa8', browser: '#1f7a5a', desktop: '#3a4a66', hub: '#b3402a', other: '#565d68' };
    const family = (tool) => (!tool || tool === 'ToolSearch' ? null
      : /^(Edit|Write|MultiEdit|NotebookEdit)$/.test(tool) ? 'edit' : /^(Bash|PowerShell)$/.test(tool) ? 'term'
        : /^(Read|Grep|Glob)$/.test(tool) ? 'read' : tool.startsWith('mcp__playwright__') ? 'browser'
          : tool.startsWith('mcp__agent-desktop__') ? 'desktop' : tool.startsWith('mcp__agent_hub__') ? 'hub' : 'other');

    function addFx(f) {
      f.t0 = performance.now();
      if (fxs.length >= FX_MAX) fxs.shift();
      fxs.push(f);
      // reduced motion: nothing moves, so paint the end state now and once more when it is over
      if (still()) { draw(1000); setTimeout(() => draw(1000), f.dur + 40); }
    }
    const deskX = (r, c) => r.x + c.x + c.cx, deskY = (r, c) => r.y + c.y + TOP + 13;
    function fly(r, from, to, col, toBoard) {
      if (still() && toBoard) return addFx({ type: 'flash', dur: 1200, r });
      addFx({ type: 'fly', dur: still() ? 1200 : 700, col, x0: deskX(r, from), y0: deskY(r, from),
        x1: toBoard ? r.board.x + 9 : deskX(r, to), y1: toBoard ? r.board.y + 5 : deskY(r, to), flash: toBoard ? r : null });
    }
    function bubble(r, c, icon) { // the newest tool replaces the one still showing
      for (let i = fxs.length - 1; i >= 0; i--) if (fxs[i].type === 'bubble' && fxs[i].c === c) fxs.splice(i, 1);
      addFx({ type: 'bubble', dur: 1500, r, c, icon });
    }

    function fx(teamId, e) {
      if (!W) return;
      const r = rooms.find((x) => x.team.id === teamId); if (!r) return; // that room is not on screen: skip
      const cell = (role) => r.cells.find((c) => c.a.role === role);
      const main = r.cells[0], d = e.data || {}, me = cell(e.role), now = performance.now();
      if (e.kind === 'task') {
        const to = cell(d.assignee);
        if (to && main && to !== main && now - (to.reqAt ?? -1e9) >= 1500) { to.reqAt = now; fly(r, main, to, ACT.request); }
      } else if (e.kind === 'message') {
        const to = cell(d.to);
        if (!me || !to || me === to) return;
        if (d.act === 'request' && me === main) { if (now - (to.reqAt ?? -1e9) < 1500) return; to.reqAt = now; } // the task event already flew it
        fly(r, me, to, ACT[d.act] || NEUTRAL);
      } else if (e.kind === 'nudge') { if (me) addFx({ type: 'knock', dur: 700, r, c: me }); }
      else if (e.kind === 'task_done' || e.kind === 'task_failed') { if (me) addFx({ type: 'stamp', dur: 900, r, c: me, ok: e.kind === 'task_done' }); }
      else if (e.kind === 'board_post') { if (me) fly(r, me, null, NEUTRAL, true); }
      else if (e.kind === 'PreToolUse') { const k = family(d.tool); if (me && k) bubble(r, me, k); }
    }

    function paper(x, y, col) { px(x - 4, y - 3, 8, 7, C.mon); px(x - 3, y - 2, 6, 5, col); px(x - 2, y - 1, 4, 1, C.mon); px(x - 2, y + 1, 3, 1, C.mon); }
    function drawFx(now, still) {
      const cssScale = m / (window.devicePixelRatio || 1);
      for (let i = fxs.length - 1; i >= 0; i--) {
        const f = fxs[i], p = (now - f.t0) / f.dur;
        if (p >= 1) { fxs.splice(i, 1); if (f.flash) addFx({ type: 'flash', dur: 800, r: f.flash }); continue; }
        if (f.type === 'fly') {
          const q = still ? 1 : p;
          paper(Math.round(f.x0 + (f.x1 - f.x0) * q), Math.round(f.y0 + (f.y1 - f.y0) * q - Math.sin(Math.PI * q) * 14), f.col);
        } else if (f.type === 'flash') {
          const b = f.r.board;
          if (still || Math.floor(p * 6) % 2 === 0) { px(b.x + 1, b.y + 1, 16, 9, '#3f7a63'); px(b.x + 6, b.y + 2, 5, 7, C.paper); px(b.x + 7, b.y + 4, 3, 1, C.paperLine); px(b.x + 7, b.y + 6, 2, 1, C.paperLine); }
        } else {
          const ox = f.r.x + f.c.x + f.c.cx, oy = f.r.y + f.c.y;
          if (f.type === 'stamp') {
            const g = !still && p < 0.15 ? 1 : 0, bx = ox - 9, by = oy + TOP + 16;
            px(bx - g, by - g, 18 + 2 * g, 9 + 2 * g, f.ok ? '#1d6b43' : '#8c2b1c'); px(bx + 1, by + 1, 16, 7, f.ok ? '#2f9e63' : '#d9482f');
            if (f.ok && cssScale >= 1.8) text('완료', bx + 9, by + 4.5, cssScale >= 2.8 ? 11 : 9.5, '#fff', 700);
            else if (f.ok) { px(bx + 5, by + 4, 2, 2, '#fff'); px(bx + 7, by + 5, 2, 2, '#fff'); px(bx + 9, by + 3, 2, 2, '#fff'); px(bx + 11, by + 2, 2, 1, '#fff'); }
            else for (let k = 0; k < 5; k++) { px(bx + 7 + k, by + 2 + k, 1, 1, '#fff'); px(bx + 11 - k, by + 2 + k, 1, 1, '#fff'); }
          } else if (f.type === 'bubble') {
            if (f.c.a.status === 'waiting_approval') continue; // the "!" bubble owns that spot
            const bx = ox + 5, by = oy - 1 + (!still && p < 0.08 ? 1 : 0), ic = ICON[f.icon], col = ICON_COL[f.icon];
            px(bx, by, 11, 10, C.paper); px(bx + 1, by + 10, 3, 1, C.paper); px(bx + 1, by + 11, 1, 1, C.paper);
            for (let k = 0; k < ic.length; k++) px(bx + 2 + ic[k][0], by + 1 + ic[k][1], ic[k][2], ic[k][3], col);
          } else if (f.type === 'knock' && (still || Math.floor(p * 6) % 2 === 0)) {
            const kx = f.r.x + f.c.x + 1, ky = oy + TOP + 9;
            px(kx, ky + 1, 1, 3, ACT.request); px(kx + 2, ky, 1, 5, ACT.request);
          }
        }
      }
    }

    // Who has a task under way: a paper on that desk and a dashed line to the main. Read from the task list,
    // so it is there after a reload too.
    function markWork() {
      for (const r of rooms) {
        const ts = r.team.tasks;
        for (const c of r.cells) {
          c.working = false;
          if (ts) for (let i = 0; i < ts.length; i++) if (ts[i].status === 'in_progress' && ts[i].assignee === c.a.role) { c.working = true; break; }
        }
      }
    }
    function drawLinks(r, t) {
      const mc = r.cells[0]; if (!mc) return;
      const x0 = r.x + mc.x + mc.cx, y0 = r.y + mc.y + mc.h, shift = Math.floor(t / 180);
      for (let i = 1; i < r.cells.length; i++) {
        const c = r.cells[i]; if (!c.working) continue;
        const x1 = r.x + c.x + c.cx, y1 = r.y + c.y + 5, n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0), 1);
        for (let k = 0; k < n; k++) if ((k + shift) % 4 < 2) px(Math.round(x0 + (x1 - x0) * k / n), Math.round(y0 + (y1 - y0) * k / n), 1, 1, '#b99a4a');
      }
    }

    function drawRoom(r, t) {
      for (let y = 0; y < r.h; y += 8) for (let x = 0; x < r.w; x += 8)
        px(r.x + x, r.y + y, Math.min(8, r.w - x), Math.min(8, r.h - y), ((x + y) / 8) % 2 ? C.floorA : C.floorB);
      px(r.x, r.y, r.w, WALL - 4, C.wall); px(r.x, r.y, r.w, 2, C.wallTop); px(r.x, r.y + WALL - 4, r.w, 2, C.base);
      // window
      const wx = r.x + r.w - 30;
      px(wx, r.y + 4, 18, 9, C.frame); px(wx + 1, r.y + 5, 7, 7, C.pane); px(wx + 10, r.y + 5, 7, 7, C.pane);
      px(wx + 2, r.y + 6, 2, 3, C.paneHi); px(wx + 11, r.y + 6, 2, 3, C.paneHi);
      // plant
      const pxx = r.x + r.w - 9;
      px(pxx, r.y + 12, 5, 5, C.pot); px(pxx + 1, r.y + 6, 3, 6, C.leaf); px(pxx - 1, r.y + 8, 2, 3, C.leaf); px(pxx + 4, r.y + 7, 2, 3, C.leafHi);
      // rug under the main desk
      const mc = r.cells[0];
      if (mc) { px(r.x + mc.x - 2, r.y + WALL + 2, mc.w + 4, mc.h - 14, C.rugEdge); px(r.x + mc.x - 1, r.y + WALL + 3, mc.w + 2, mc.h - 16, C.rug); }
      // board on the wall: a paper and a blinking dot when the team has a post nobody has read yet
      const bx = r.x + r.w - 52, by = r.y + 3;
      r.board = { x: bx, y: by, w: 18, h: 12 };
      px(bx, by, 18, 12, C.woodEdge); px(bx + 1, by + 1, 16, 9, '#1f3a30'); px(bx + 1, by + 10, 16, 1, C.woodTop);
      px(bx + 3, by + 3, 5, 1, '#7fa795'); px(bx + 3, by + 5, 3, 1, '#7fa795'); px(bx + 4, by + 10, 2, 1, C.paper);
      if (r.team.boardNew) {
        px(bx + 10, by + 2, 5, 7, C.paper); px(bx + 11, by + 4, 3, 1, C.paperLine); px(bx + 11, by + 6, 2, 1, C.paperLine); px(bx + 12, by + 1, 1, 2, C.alert);
        if (Math.floor(t / 450) % 2 === 0) px(bx + 15, by - 1, 3, 3, C.alert);
      }
      const dprSize = m / (window.devicePixelRatio || 1);
      text(r.team.name || r.team.id, r.x + 5, r.y + 8.5, dprSize >= 1.8 ? 12 : 10, C.ink, 700, 'left');
    }

    function drawAgent(r, c, t) {
      const a = c.a, ox = r.x + c.x, oy = r.y + c.y, w = c.w;
      const P = (x, y, ww, hh, col) => px(ox + x, oy + y, ww, hh, col);
      const st = (a.alive === false || a.status === 'ended') ? 'ended' : a.status;
      const tt = t + (c.seed % 2400), cx = c.big ? w >> 1 : 29, B = TOP;
      const skin = SKIN[c.seed % SKIN.length], hair = HAIR[(c.seed >> 3) % HAIR.length];
      const shirt = SHIRT[a.job] || SHIRT.builder, frontH = c.big ? 10 : 8;
      const person = (x, y, eyesShut, legs) => {
        P(x, y, 8, 8, skin); P(x, y, 8, 3, hair); P(x, y + 3, 1, 2, hair); P(x + 7, y + 3, 1, 2, hair);
        P(x + 2, y + (eyesShut ? 5 : 4), 1, eyesShut ? 1 : 2, C.mon); P(x + 5, y + (eyesShut ? 5 : 4), 1, eyesShut ? 1 : 2, C.mon);
        if (legs !== undefined) { P(x, y + 8, 8, 6, shirt); P(x + 1, y + 14, 2, legs ? 3 : 2, C.mon); P(x + 5, y + 14, 2, legs ? 2 : 3, C.mon); }
      };
      // chair
      P(cx - 6, B + 3, 12, 13, C.chair); P(cx - 5, B + 4, 10, 11, C.chairHi);
      const seated = st !== 'ended' && st !== 'configuring';
      if (seated) {
        const dy = st === 'idle' ? Math.floor(tt / 1300) % 2 : st === 'busy' ? (Math.floor(tt / 260) % 5 === 0 ? 1 : 0) : 0;
        P(cx - 6, B + 9, 12, 7, shirt);
        person(cx - 4, B + dy, st === 'idle' && tt % 3800 < 180);
      }
      // desk
      P(3, B + 15, w - 6, 4, C.woodTop); P(3, B + 19, w - 6, 1, C.woodEdge); P(3, B + 20, w - 6, frontH - 1, C.woodFront);
      // monitors
      for (const mx of (c.big ? [6, w - 19] : [5])) {
        P(mx + 5, B + 13, 3, 2, C.stand); P(mx + 3, B + 14, 7, 1, C.stand); P(mx, B + 3, 13, 10, C.mon);
        const sx = mx + 1, sy = B + 4;
        if (st === 'ended') P(sx, sy, 11, 8, '#0a0b0d');
        else if (st === 'configuring' || st === 'booting') {
          P(sx, sy, 11, 8, '#16283a'); P(sx + 1, sy + 3, 9, 2, '#0c1722'); P(sx + 1, sy + 3, 1 + Math.floor(tt / 220) % 9, 2, '#6cc0ff');
        } else if (st === 'busy') {
          P(sx, sy, 11, 8, Math.floor(tt / 90) % 7 === 0 ? '#12303a' : '#0f262f');
          for (let i = 0; i < 3; i++) P(sx + 1, sy + 1 + i * 2, 3 + ((Math.floor(tt / 170) + i * 5 + mx) * 7) % 7, 1, i === 2 ? '#f5c04e' : '#7fe0c0');
        } else if (st === 'waiting_approval') {
          P(sx, sy, 11, 8, Math.floor(tt / 450) % 2 ? '#5a2a1c' : '#3a1c14'); P(sx + 5, sy + 1, 1, 4, C.alert); P(sx + 5, sy + 6, 1, 1, C.alert);
        } else { P(sx, sy, 11, 8, '#14252b'); P(sx + 1, sy + 1, 5, 1, '#4f8f86'); if (Math.floor(tt / 700) % 2) P(sx + 1, sy + 3, 1, 1, '#9fd8d0'); }
      }
      // keyboard and hands
      if (seated) {
        P(cx - 5, B + 16, 10, 2, C.key);
        const l = st === 'busy' ? Math.floor(tt / 130) % 2 : 0, r2 = st === 'busy' ? 1 - l : 0;
        P(cx - 4, B + 15 + l, 2, 2, skin); P(cx + 2, B + 15 + r2, 2, 2, skin);
      }
      if (c.working && c !== r.cells[0]) { P(cx + 6, B + 11, 4, 5, C.paper); P(cx + 7, B + 12, 2, 1, C.paperLine); P(cx + 7, B + 14, 2, 1, C.paperLine); } // the task sheet
      // CLI badge on the desk front
      const cli = CLI[a.cli] || [a.cli || '', C.ink], cssScale = m / (window.devicePixelRatio || 1);
      P(5, B + 22, 2, frontH - 5, cli[1]);
      if (cssScale >= 1.8) text(cli[0], ox + 9, oy + B + 20 + frontH / 2 - 0.5, cssScale >= 2.8 ? 11 : 9.5, cli[1], 600, 'left');
      if (a.wt) { // works in its own git copy: a small branch mark at the right end of the desk front
        const fx0 = w - 10, fy0 = B + 21;
        P(fx0, fy0, 1, 5, '#9fd8d0'); P(fx0 + 1, fy0 + 3, 1, 1, '#9fd8d0'); P(fx0 + 2, fy0 + 2, 1, 1, '#9fd8d0'); P(fx0 + 3, fy0, 1, 2, '#9fd8d0'); P(fx0 - 1, fy0, 3, 1, '#9fd8d0');
      }
      // walker: still arriving
      if (st === 'configuring') {
        const ph = Math.min(1, (tt % 3200) / 2400), wxp = Math.round(1 + (cx - 5) * ph), moving = ph < 1;
        person(wxp, B + 11 + (moving ? Math.floor(tt / 160) % 2 : 0), false, moving ? Math.floor(tt / 160) % 2 : 0);
      }
      // unread mail
      if (a.unread > 0) {
        const ex = cx - 15, ey = 1 + (Math.floor(tt / 500) % 2);
        P(ex, ey, 8, 6, C.paper); P(ex, ey, 8, 1, C.paperLine); P(ex + 1, ey + 1, 1, 1, C.paperLine); P(ex + 2, ey + 2, 1, 1, C.paperLine);
        P(ex + 3, ey + 3, 2, 1, C.paperLine); P(ex + 5, ey + 2, 1, 1, C.paperLine); P(ex + 6, ey + 1, 1, 1, C.paperLine); P(ex + 7, ey - 1, 2, 2, C.alert);
      }
      // approval needed: bubble and pulsing frame
      if (st === 'waiting_approval') {
        const bx = cx + 5, by = Math.floor(tt / 280) % 2;
        P(bx, by, 9, 9, C.paper); P(bx + 1, by + 9, 3, 1, C.paper); P(bx + 1, by + 10, 1, 1, C.paper);
        P(bx + 4, by + 1, 2, 5, C.alert); P(bx + 4, by + 7, 2, 1, C.alert);
        if (Math.floor(tt / 500) % 2 === 0) { P(0, 0, w, 1, C.alert); P(0, c.h - 1, w, 1, C.alert); P(0, 0, 1, c.h, C.alert); P(w - 1, 0, 1, c.h, C.alert); }
      }
      // name plate
      const fs = cssScale >= 2.8 ? 12 : cssScale >= 1.8 ? 11 : 8, py = c.h - 5.5;
      ctx.font = `600 ${Math.round(fs * (window.devicePixelRatio || 1))}px "Segoe UI","Malgun Gothic",sans-serif`;
      const label = a.name || a.role; // display name; the id stays in `role`
      const tw = Math.min(w, Math.ceil(ctx.measureText(label).width / m) + 6);
      P((w - tw) >> 1, c.h - 10, tw, 9, C.plate);
      text(label, ox + w / 2, oy + py, fs, st === 'ended' ? '#8b929e' : C.ink);
      if (c.key === sel) { // corner brackets
        for (const [x, y, sx2, sy2] of [[0, 0, 1, 1], [w - 1, 0, -1, 1], [0, c.h - 1, 1, -1], [w - 1, c.h - 1, -1, -1]]) {
          P(sx2 > 0 ? x : x - 4, y, 5, 1, C.ink); P(x, sy2 > 0 ? y : y - 4, 1, 5, C.ink);
        }
      }
    }

    function draw(t) {
      if (!W) return;
      ctx.fillStyle = C.corridor; ctx.fillRect(0, 0, canvas.width, canvas.height);
      for (const r of rooms) { drawRoom(r, t); drawLinks(r, t); for (const c of r.cells) drawAgent(r, c, t); }
      if (fxs.length) drawFx(performance.now(), still());
    }

    function loop(now) {
      requestAnimationFrame(loop);
      // 8 fps is plenty for pixel art, about 30 while an effect is in flight; skip when hidden or when the
      // user asked for less motion.
      if (still() || now - last < (fxs.length ? 30 : 120) || !canvas.offsetParent) return;
      last = now; draw(now);
    }
    requestAnimationFrame(loop);
    reduce.addEventListener('change', () => draw(1000));
    new ResizeObserver(() => relayout()).observe(canvas.parentElement);

    const cellAt = (e) => {
      const b = canvas.getBoundingClientRect(), x = (e.clientX - b.left) / b.width * W, y = (e.clientY - b.top) / b.height * H;
      for (const r of rooms) for (const c of r.cells)
        if (x >= r.x + c.x && x < r.x + c.x + c.w && y >= r.y + c.y && y < r.y + c.y + c.h) return c;
      return null;
    };
    const boardAt = (e) => {
      const b = canvas.getBoundingClientRect(), x = (e.clientX - b.left) / b.width * W, y = (e.clientY - b.top) / b.height * H;
      return rooms.find((r) => r.board && x >= r.board.x && x < r.board.x + r.board.w && y >= r.board.y && y < r.board.y + r.board.h) || null;
    };
    canvas.addEventListener('click', (e) => {
      const c = cellAt(e), r = !c && opt.onBoard && boardAt(e);
      if (c && opt.onPick) opt.onPick(c.key); else if (r) opt.onBoard(r.team.id);
    });
    canvas.addEventListener('mousemove', (e) => { canvas.style.cursor = (opt.onPick && cellAt(e)) || (opt.onBoard && boardAt(e)) ? 'pointer' : 'default'; });

    return {
      relayout, fx,
      setTeams(next, selected, focusId) {
        teams = next; sel = selected; focus = focusId || null;
        const s = focus + JSON.stringify(next.map((t) => [t.id, t.name, t.agents.map((a) => a.role + a.job)]));
        if (s !== sig || !W) { sig = s; relayout(); }
        else { // same seats: only refresh the agent objects, no layout change
          // `shown` still holds the previous call's team objects: take the same teams from the new list,
          // or status and CLI changes never reach the drawing
          const ids = new Set(shown.map((t) => t.id));
          shown = teams.filter((t) => ids.has(t.id));
          const fresh = buildRooms(shown);
          rooms.forEach((r, i) => { r.team = shown[i]; r.cells.forEach((c, j) => { c.a = fresh[i].cells[j].a; }); });
          markWork();
          if (still()) draw(1000);
        }
      },
    };
  }
  return { create };
})();
