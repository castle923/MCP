"""Markdown board posts are drawn as a document: check the renderer in web/md.js with Node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MD_JS = Path(__file__).resolve().parents[1] / "hub_desk" / "web" / "md.js"


def render(src: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    code = f"process.stdout.write(require({json.dumps(str(MD_JS))}).mdToHtml({json.dumps(src)}))"
    return subprocess.run([node, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def test_blocks_and_inline():
    html = render("# 제목\n\n- **굵게** `a<b`\n- [x] 끝\n\n1. 하나\n2. 둘\n\n> 인용\n\n```py\nx < 1\n```\n\n"
                  "| 이름 | 값 |\n|---|--:|\n| a | 1 |\n\n[링크](https://e.com) 문단\n둘째 줄")
    for part in ("<h1>제목</h1>", "<strong>굵게</strong>", "<code>a&lt;b</code>", "checked", "<ol><li>하나</li><li>둘</li></ol>",
                 "<blockquote><p>인용</p></blockquote>", '<pre><code class="lang-py">x &lt; 1</code></pre>',
                 '<th style="text-align:right">값</th>', "<td>a</td>", '<a href="https://e.com">링크</a>', "문단<br>둘째 줄"):
        assert part in html, part


def test_source_html_and_bad_links_are_inert():
    html = render('<script>alert(1)</script>\n\n[x](javascript:alert(1)) <img src=x onerror=alert(1)>\n\n> <b>q</b>')
    assert "<script>" not in html and "<img src=x" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and 'href="#"' in html
