"""Owned executable resolution and complete non-secret child environment."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CONFIGURED_EXECUTABLES = frozenset(
    {
        "docker",
        "git",
        "openmagic-api",
        "openmagic-delivery-worker",
        "openmagic-evidence",
        "openmagic-local-email-provider",
        "openmagic-playground",
        "openmagic-workflow-worker",
        "python",
    }
)
FIXED_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": os.defpath,
    "PYTHONNOUSERSITE": "1",
}
_INSTALLED_HELPERS = CONFIGURED_EXECUTABLES.difference({"docker", "git", "python"})


def fixed_execution_environment() -> dict[str, str]:
    """Return a fresh complete environment with no ambient Git configuration."""

    return dict(FIXED_ENVIRONMENT)


def fixed_executable_path(name: str) -> Path:
    """Resolve one configured executable without consulting ambient PATH."""

    if name == "python":
        path = Path(sys.executable)
    elif name in _INSTALLED_HELPERS:
        path = Path(sys.executable).parent / name
    elif name in {"docker", "git"}:
        resolved = shutil.which(name, path=FIXED_ENVIRONMENT["PATH"])
        if resolved is None:
            raise RuntimeError(f"configured evidence executable is unavailable: {name}")
        path = Path(resolved)
    else:
        raise ValueError(f"unknown configured evidence executable: {name}")
    if not path.is_file():
        raise RuntimeError(f"configured evidence executable is unavailable: {name}")
    return path.absolute()


__all__ = [
    "CONFIGURED_EXECUTABLES",
    "FIXED_ENVIRONMENT",
    "fixed_executable_path",
    "fixed_execution_environment",
]
