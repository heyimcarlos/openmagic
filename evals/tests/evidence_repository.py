from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
SOURCE_PACKAGES = (
    Path("apps/api/src/openmagic_api"),
    Path("evals/src/openmagic_evals"),
    Path("packages/openmagic-runtime/src/openmagic_runtime"),
    Path("reference-apps/example-insurance/src/example_insurance"),
)


def prepare_clean_evidence_repository(path: Path) -> None:
    path.mkdir()
    shutil.copy2(ROOT / "uv.lock", path / "uv.lock")
    for package in SOURCE_PACKAGES:
        shutil.copytree(
            ROOT / package,
            path / package,
            ignore=shutil.ignore_patterns("__pycache__", ".*"),
        )
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "--all"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test fixture"], cwd=path, check=True)


__all__ = ["prepare_clean_evidence_repository"]
