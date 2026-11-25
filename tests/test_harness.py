"""Harness file and extra skills / plugins. Fake home folders: the real ~/.claude and ~/.codex are not read."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub_desk import harness  # noqa: E402


def skill(root: Path, name: str, desc: str = "does things") -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n", encoding="utf-8")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    skill(h / ".claude" / "skills", "both")
    skill(h / ".codex" / "skills", "both")
    skill(h / ".claude" / "skills", "claude-only", "only Claude has it")
    skill(h / ".codex" / "skills", "codex-only")
    (h / ".claude" / "skills" / "not-a-skill").mkdir()
    for name in ("on-plugin", "off-plugin"):
        (h / "cache" / "m" / name / "1.0" / ".claude-plugin").mkdir(parents=True)
    (h / "cache" / "m" / "off-plugin" / "1.0" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"description": "switched off"}), encoding="utf-8")
    (h / ".claude" / "plugins").mkdir()
    (h / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
        f"{n}@m": [{"installPath": str(h / "cache" / "m" / n / "1.0"), "version": "1.0"}]
        for n in ("on-plugin", "off-plugin")}}), encoding="utf-8")
    (h / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"on-plugin@m": True}}), encoding="utf-8")
    (h / ".codex" / "AGENTS.md").write_text("global rules", encoding="utf-8")
    monkeypatch.setattr(harness, "HOME", h)
    monkeypatch.setattr(harness, "USER_ROOTS", (
        ("claude", h / ".claude" / "skills", "Claude 스킬", ("claude",)),
        ("codex", h / ".codex" / "skills", "Codex 스킬", ("codex",))))
    ws = tmp_path / "ws"
    ws.mkdir()
    return h, ws


def test_scan_splits_base_and_extras(home):
    h, ws = home
    s = harness.scan(str(ws))
    assert s["base"]["claude"] == {"skills": ["both", "claude-only"], "plugins": ["on-plugin"]}
    assert s["base"]["codex"]["skills"] == ["both", "codex-only"]
    extras = {e["name"]: e for e in s["extras"]}
    assert set(extras) == {"claude-only", "codex-only", "off-plugin"}  # 'both' and the enabled plugin are base
    assert extras["claude-only"]["how"] == {"claude": "native", "codex": "mention"}
    assert extras["codex-only"]["how"] == {"claude": "mention", "codex": "native"}
    assert extras["off-plugin"]["how"]["claude"] == "plugin-dir" and extras["off-plugin"]["description"] == "switched off"


def test_harness_prefers_the_workspace_file(home):
    h, ws = home
    assert harness.scan(str(ws))["harness"]["auto"]["scope"] == "global"
    (ws / "AGENTS.md").write_text("project rules", encoding="utf-8")
    auto = harness.scan(str(ws))["harness"]
    assert auto["auto"] == {"path": str(ws / "AGENTS.md"), "scope": "workspace"}
    assert auto["native"] == {"claude": "mention", "codex": "native"}  # Claude Code does not read AGENTS.md


def test_new_items_are_flagged_once(home, tmp_path):
    h, ws = home
    known = tmp_path / "state" / "known.json"
    assert not any(e["new"] for e in harness.scan(str(ws), known)["extras"])  # first scan: all already installed
    skill(h / ".codex" / "skills", "downloaded-later")
    s = harness.scan(str(ws), known)
    assert [e["name"] for e in s["extras"] if e["new"]] == ["downloaded-later"] and s["extras"][0]["new"]
    harness.remember(known, s["extras"])
    assert not any(e["new"] for e in harness.scan(str(ws), known)["extras"])


def test_resolve_and_addon(home):
    h, ws = home
    cfg = harness.resolve({"harness": {"mode": "auto"},
                           "extras": ["skill:codex:codex-only", "plugin:off-plugin@m"]}, str(ws))
    assert cfg["harness"] == str(h / ".codex" / "AGENTS.md")
    text, dirs = harness.addon(cfg, "claude", str(ws))
    assert dirs == [str(h / "cache" / "m" / "off-plugin" / "1.0")]
    assert "codex-only" in text and "SKILL.md" in text and "Read it now" in text  # no ~/.claude/CLAUDE.md here
    text, dirs = harness.addon(cfg, "codex", str(ws))
    assert dirs == [] and "Your CLI loads this file by itself" in text
    assert "off-plugin (plugin folder" in text and "codex-only" not in text  # codex has that skill already

    assert harness.addon(harness.resolve({"harness": {"mode": "off"}}, str(ws)), "claude", str(ws)) == ("", [])


@pytest.mark.parametrize("body", [
    {"harness": {"mode": "path", "path": "Z:/nope/AGENTS.md"}},
    {"harness": {"mode": "weird"}},
    {"extras": ["skill:codex:not-installed"]},
    {"extras": ["skill:codex:codex-only"] * 13},
])
def test_resolve_rejects(home, body):
    with pytest.raises(ValueError):
        harness.resolve(body, str(home[1]))


def test_tools_browser_pc_and_ponytail(tmp_path, monkeypatch):
    import json as j
    from hub_desk import tools
    h = tmp_path / "home"
    (h / ".codex" / "skills" / "ponytail").mkdir(parents=True)
    (h / ".codex" / "skills" / "ponytail" / "SKILL.md").write_text("---\nname: ponytail\n---\n", encoding="utf-8")
    desk = tmp_path / "agent-desktop.exe"
    desk.write_bytes(b"")
    (h / ".claude.json").write_text(j.dumps({"mcpServers": {"playwright": {"type": "stdio", "command": "npx",
                                                                           "args": ["@playwright/mcp@latest"]}}}), encoding="utf-8")
    (h / ".codex" / "config.toml").write_text(
        '[mcp_servers.playwright]\ncommand = "npx"\nargs = ["@playwright/mcp@latest"]\n'
        f'[mcp_servers.agent-desktop]\ncommand = {j.dumps(str(desk))}\nargs = ["mcp"]\n', encoding="utf-8")
    monkeypatch.setattr(tools, "HOME", h)
    assert all(v["ok"] for v in tools.available().values())
    on = tools.resolve({})  # nothing said: everything that is installed is on
    assert on == {"browser": True, "pc": True, "ponytail": True}
    srv = tools.claude_servers(on)
    assert srv["playwright"]["args"] == ["@playwright/mcp@latest", "--isolated"] and srv["agent-desktop"]["args"] == ["mcp"]
    assert tools.codex_overrides(on) == ["-c", 'mcp_servers.playwright.args=["@playwright/mcp@latest", "--isolated"]']
    off = tools.resolve({"tools": {"browser": False, "pc": False, "ponytail": False}})
    assert tools.claude_servers(off) == {} and tools.guide(off, "claude") == ""
    assert tools.codex_overrides(off) == ["-c", "mcp_servers.playwright.enabled=false",
                                          "-c", "mcp_servers.agent-desktop.enabled=false",
                                          "-c", 'mcp_servers.cua_repl={command="none", enabled=false}']
    assert tools.claude_args(on) == ["--no-chrome"]
    assert "ponytail" in tools.guide(on, "codex") and "SKILL.md" in tools.guide(on, "codex")
    desk.unlink()  # registered but the program is gone: not offered
    assert tools.resolve({})["pc"] is False
