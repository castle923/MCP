"""Check that models start and answer: one team per model, ask each main for 'pong', report, stop.

Needs hub-desk running with no team started. At most 3 models per run (one team each).
    .venv\\Scripts\\python.exe tools\\verify_models.py codex:gpt-6.2-sol claude:claude-fable-5-2
Each model spends one short turn of the subscription.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

U = f"http://127.0.0.1:{os.environ.get('HUB_DESK_PORT', '8765')}"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WORKSPACE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sandbox")


def call(path, data=None, token="", raw=False):
    body = data if isinstance(data, (bytes, type(None))) else json.dumps(data).encode()
    req = urllib.request.Request(U + path, data=body, headers={"X-Token": token, "Content-Type": "application/json"})
    try:
        with NO_PROXY.open(req, timeout=60) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        out = e.read()
    return out if raw else (json.loads(out) if out else {})


def screen(team, token):
    """Terminal output so far, escape sequences removed. The stream stays open, so stop at a short timeout."""
    req = urllib.request.Request(f"{U}/api/out/{team}/main", headers={"X-Token": token})
    buf = b""
    try:
        with NO_PROXY.open(req, timeout=3) as r:
            while chunk := r.read1(65536):
                buf += chunk
    except OSError:
        pass
    t = re.sub("\x1b\\][^\x07\x1b]*(\x07|\x1b\\\\)", "", buf.decode("utf-8", "replace"))
    return re.sub("\x1b\\[[0-9;?]*[a-zA-Z]", " ", t)


def main(args):
    models = [a.split(":", 1) for a in args]
    token = re.search(r'name="hub-token" content="([^"]+)"', call("/web/index.html", raw=True).decode()).group(1)
    os.makedirs(WORKSPACE, exist_ok=True)
    r = call("/api/start", {"project": "모델 확인", "workspace": WORKSPACE, "harness": {"mode": "off"},
                            "teams": [{"main": {"cli": c, "model": m, "effort": "low"}} for c, m in models]}, token)
    if not r.get("ok"):
        print("start failed:", r)
        return 1
    ok = True
    try:
        time.sleep(25)
        for i in range(len(models)):
            call(f"/api/in/team{i + 1}/main", b"Reply with only the word pong. Do not use any tools.", token)
        time.sleep(1.5)  # codex takes Enter right after fast input as a pasted newline
        for i in range(len(models)):
            call(f"/api/in/team{i + 1}/main", b"\r", token)
        time.sleep(35)
        state = call("/api/state", token=token)
        for i, (cli, model) in enumerate(models):
            team = state["teams"][i]
            a, t = team["agents"][0], screen(team["id"], token)
            answered = bool(re.search(r"[•●]\s*pong", t, re.I))
            banner = sorted(set(re.findall(r"GPT-[\w.]+(?:[- ][A-Z]\w+)?|(?:Fable|Opus|Sonnet|Haiku) [\d.]+", t)))
            ok &= answered and a["alive"]
            print(f"{cli}:{model} alive={a['alive']} status={a['status']} ctx={a['ctx']} "
                  f"answered_pong={answered} banner={banner}")
            if not answered:
                print("   last output:", " ".join(t.split())[-400:])
    finally:
        print("stop:", call("/api/stop", b"", token))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
