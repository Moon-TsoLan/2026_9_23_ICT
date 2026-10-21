"""Verify the batch toggle reaches both config and the merge step at startup."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("value,expected", [(None, "True True"), ("0", "False False"), ("1", "True True")])
def test_merge_thinking_process_switch(value, expected):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    env.pop("ICT_MERGE_THINKING", None)
    if value is not None:
        env["ICT_MERGE_THINKING"] = value
    result = subprocess.run([
        sys.executable, "-B", "-c",
        "from ict.config import MERGE_THINKING as config; "
        "from ict.steps.s08_merge import MERGE_THINKING as step; print(config, step)",
    ], cwd=root, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
