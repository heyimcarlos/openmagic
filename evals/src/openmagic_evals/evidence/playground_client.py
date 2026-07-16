"""External observation of the installed playground application."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def invoke_playground(*arguments: str, timeout_seconds: int) -> dict[str, Any]:
    executable = Path(sys.executable).parent / "openmagic-playground"
    if not executable.is_file():
        raise RuntimeError("installed playground entry point is missing")
    completed = subprocess.run(
        [str(executable), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "playground command failed: "
            f"status={completed.returncode} stderr={completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise TypeError("playground command did not return one JSON object")
    return value


__all__ = ["invoke_playground"]
