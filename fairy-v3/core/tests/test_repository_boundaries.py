from __future__ import annotations

import subprocess
import sys
from pathlib import Path

V3_ROOT = Path(__file__).parents[2]


def test_v3_source_has_no_legacy_runtime_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, str(V3_ROOT / "scripts" / "check_boundaries.py"), str(V3_ROOT)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
