from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_renewal_demo_requires_explicit_approved_local_execution() -> None:
    executable = Path(sys.executable).parent / "openmagic-playground"

    completed = subprocess.run(
        [str(executable), "demo-renewal"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "--execute-approved-local-effect" in completed.stderr
