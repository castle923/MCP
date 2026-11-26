// Markdown to HTML for board posts. Small on purpose: headings, lists, quotes, code, tables, links, emphasis.
// Every character of the source is escaped before any tag is added, and the result is shown in a frame
// that runs no scripts, so a post can not reach the dashboard.
(() => {
  'use strict';
  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = (u) => (/^(https?:|mailto:|#|\.{0,2}\/|[\w.-]+(\/|$|#|\?))/i.test(u) && !/^\s*(javascript|data|vbscript):/i.test(u) ? u : '#');

  function inline(s) { // s is already escaped
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, a, u) => `<img alt="${a}" src="${safeUrl(u)}">`)
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) => `<a href="${safeUrl(u)}">${t}</a>`)
      .replace(/\*\*(.+?)\*\*|__(.+?)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
      .replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?!\w)/g, '$1<em>$2</em>')
      .replace(/~~(.+?)~~/g, '<del>$1</del>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
  }

  const cells = (row) => row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());

  function mdToHtml(src) {
    const lines = esc(String(src ?? '')).replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      let m;
      if ((m = l.match(/^\s*(```|~~~)\s*([\w+-]*)/))) { // fenced code
        const fence = m[1], body = [];
        for (i++; i < lines.length && !lines[i].trimStart().startsWith(fence); i++) body.push(lines[i]);
        out.push(`<pre><code${m[2] ? ` class="lang-${m[2]}"` : ''}>${body.join('\n')}</code></pre>`); i++;
      } else if ((m = l.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/))) {
        out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++;
      } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) {
        out.push('<hr>'); i++;
      } else if (/^\s*&gt;/.test(l)) {
        const body = [];
        for (; i < lines.length && /^\s*&gt;/.test(lines[i]); i++) body.push(lines[i].replace(/^\s*&gt;\s?/, ''));
        out.push(`<blockquote>${mdToHtml.inner(body)}</blockquote>`);
      } else if (l.includes('|') && i + 1 < lines.length && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        const head = cells(l), aligns = cells(lines[i + 1]).map((c) => (c.endsWith(':') ? (c.startsWith(':') ? 'center' : 'right') : ''));
        const td = (tag, c, k) => `<${tag}${aligns[k] ? ` style="text-align:${aligns[k]}"` : ''}>${inline(c)}</${tag}>`;
        const rows = [];
        for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) rows.push(`<tr>${cells(lines[i]).map((c, k) => td('td', c, k)).join('')}</tr>`);
        out.push(`<table><thead><tr>${head.map((c, k) => td('th', c, k)).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`);
      } else if ((m = l.match(/^(\s*)([-*+]|\d+[.)])\s+/))) {
        const ordered = /\d/.test(m[2]), items = [];
        for (; i < lines.length; i++) {
          const it = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (it && /\d/.test(it[2]) === ordered && it[1].length <= m[1].length + 1) items.push([it[3]]);
          else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) items[items.length - 1].push(lines[i].replace(/^\s{2,4}/, ''));
          else break;
        }
        out.push(`<${ordered ? 'ol' : 'ul'}>${items.map(([first, ...rest]) => {
          const box = first.match(/^\[([ xX])\]\s+(.*)$/);
          const head = box ? `<input type="checkbox" disabled${box[1] !== ' ' ? ' checked' : ''}> ${inline(box[2])}` : inline(first);
          return `<li>${head}${rest.length ? mdToHtml.inner(rest) : ''}</li>`;
        }).join('')}</${ordered ? 'ol' : 'ul'}>`);
      } else if (!l.trim()) {
        i++;
      } else {
        const body = [];
        for (; i < lines.length && lines[i].trim() && !/^(\s*(```|~~~|&gt;)|#{1,6}\s|\s*([-*+]|\d+[.)])\s+)/.test(lines[i]); i++) body.push(lines[i]);
        if (!body.length) body.push(lines[i++]);
        out.push(`<p>${body.map((b) => inline(b.trim())).join('<br>')}</p>`);
      }
    }
    return out.join('\n');
  }
  // nested blocks: their lines are escaped already, so undo that once before the recursive call escapes again
  mdToHtml.inner = (ls) => mdToHtml(ls.join('\n').replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[e])));

  const MD_CSS = `body{margin:0;padding:22px 26px;font:15px/1.7 "Segoe UI","Malgun Gothic",sans-serif;color:#1c2330;background:#fff;overflow-wrap:anywhere}
h1,h2,h3,h4{line-height:1.3;margin:1.2em 0 .5em}h1{font-size:1.6em;border-bottom:2px solid #e3e0d6;padding-bottom:.3em}h2{font-size:1.3em;border-bottom:1px solid #e3e0d6;padding-bottom:.25em}
h1:first-child,h2:first-child,h3:first-child{margin-top:0}p,ul,ol,blockquote,table,pre{margin:0 0 1em}ul,ol{padding-left:1.6em}li>ul,li>ol,li>p{margin:.2em 0}
code{font:13px "Cascadia Mono",Consolas,monospace;background:#f1efe8;padding:.1em .35em;border-radius:3px}pre{background:#1c2330;color:#e8e6df;padding:14px 16px;overflow:auto;border-radius:4px}pre code{background:none;padding:0;color:inherit}
blockquote{border-left:4px solid #c9c3b3;margin-left:0;padding:.2em 1em;color:#4a5568}table{border-collapse:collapse;display:block;overflow:auto}th,td{border:1px solid #d8d4c8;padding:6px 10px}th{background:#f4f1e8}
img{max-width:100%}hr{border:0;border-top:1px solid #d8d4c8;margin:1.5em 0}a{color:#1d5fbf}`;

  const api = { mdToHtml, MD_CSS };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else window.Md = api;
})();
