"""One CLI running in a Windows pseudo console (ConPTY)."""
from __future__ import annotations

import asyncio
import subprocess
import threading

from winpty import PtyProcess

from .job import Job

KEEP_BYTES = 4_000_000  # replayed to a page that (re)opens the terminal


class Pane:
    """A thread reads the process; readers on the event loop follow `chunks` by absolute index."""

    def __init__(self, argv: list[str], cols: int, rows: int, loop: asyncio.AbstractEventLoop,
                 cwd: str | None = None, env: dict[str, str] | None = None):
        self.proc = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
        self.pid = self.proc.pid
        self.job = Job(self.pid)  # right after the spawn: what the process starts later is in the job too
        self.released: tuple[int, int] | None = None  # set once by close()
        self._closing = threading.Lock()
        self.chunks: list[bytes] = []
        self.base = 0   # absolute index of chunks[0]; grows when old output is dropped
        self.size = 0
        self.done = False
        self.changed = asyncio.Event()
        self.loop = loop
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            while True:
                data = self.proc.read(65536)
                if data:
                    self.loop.call_soon_threadsafe(self._feed, data.encode("utf-8", "replace"))
        except (EOFError, OSError):
            pass
        self.close()  # the CLI ended by itself: what it started (MCP servers, dev servers) ends with it
        try:
            self.loop.call_soon_threadsafe(self._feed, None)
        except RuntimeError:  # loop already closed at shutdown
            pass

    def _feed(self, data: bytes | None) -> None:
        if data is None:
            self.done = True
        else:
            self.chunks.append(data)
            self.size += len(data)
            while self.size > KEEP_BYTES and len(self.chunks) > 1:
                self.size -= len(self.chunks.pop(0))
                self.base += 1
        self.changed.set()

    async def stream(self):
        i = 0
        while True:
            i = max(i, self.base)
            while i < self.base + len(self.chunks):
                yield self.chunks[i - self.base]
                i += 1
                i = max(i, self.base)
            if self.done:
                return
            self.changed.clear()
            if i >= self.base + len(self.chunks) and not self.done:
                await self.changed.wait()

    def tail(self, n: int = 4000) -> str:
        """The last characters of output, without escape sequences (to spot a prompt waiting for the person)."""
        import re
        raw = b"".join(self.chunks[-8:])[-n * 3:].decode("utf-8", "replace")
        raw = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", raw)
        return " ".join(re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", " ", raw).split())[-n:]

    @property
    def alive(self) -> bool:
        return not self.done and self.proc.isalive()

    def write(self, text: str) -> None:
        if self.alive:
            self.proc.write(text)

    def say(self, text: str, loop, enter_after: float = 0.6) -> None:
        """Type a message into the CLI as one paste, then press Enter a moment later. Bracketed paste keeps line
        breaks inside the message (a bare newline would submit early), and the pause keeps Codex from taking the
        Enter as part of a fast paste."""
        self.write("\x1b[200~" + text.replace("\r\n", "\n").replace("\x1b", "") + "\x1b[201~")
        loop.call_later(enter_after, self.write, "\r")

    def resize(self, cols: int, rows: int) -> None:
        if self.alive:
            self.proc.setwinsize(max(2, rows), max(2, cols))

    @property
    def procs(self) -> int:
        """Processes this pane holds right now (0 once released)."""
        return self.job.active() if self.job.ok else int(self.proc.isalive())

    def close(self) -> tuple[int, int]:
        """End everything this pane started: the CLI, its MCP servers, and what those started.
        Returns (processes ended, processes still alive). Safe to call twice; the second call returns (0, 0)."""
        with self._closing:
            if self.released is not None:
                return 0, 0
            if self.job.ok:
                self.released = self.job.kill()
            else:  # no job: the parent-id walk, which misses children of a process that has exited
                had = int(self.proc.isalive())
                if had:
                    subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True, timeout=20)
                self.released = (had, int(self.proc.isalive()))
            return self.released
