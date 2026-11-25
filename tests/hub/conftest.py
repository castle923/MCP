import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONTRACT = "OBJECTIVE: x\nOUTPUT: y\nTOOLS: z\nBOUNDARIES: w"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HUB_HOME", str(tmp_path))
    from agent_hub.db import Board
    b = Board()
    yield b
    b.close()
