from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_channel_tabs_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    repo_root = Path(__file__).resolve().parents[2]
    test_file = repo_root / "frontend" / "tests" / "channel_tabs.test.js"
    result = subprocess.run(
        [node, "--test", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
