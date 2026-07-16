"""Historically verifiable freeze-before-corpus methodology for held-out Agent cases."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from openmagic_evals.evidence._sealed_agent_corpus import HELD_OUT_CASES
from openmagic_evals.evidence.core_models import canonical_digest

HELD_OUT_SEALED_PATH = "evals/src/openmagic_evals/evidence/_sealed_agent_corpus.py"
TUNING_LOCKED_ROOTS = (
    "packages/openmagic-runtime/src/openmagic_runtime",
    "reference-apps/example-insurance/src/example_insurance",
    "apps/api/src/openmagic_api",
    "apps/playground/src/openmagic_playground",
    "evals/src/openmagic_evals/evidence",
    "evals/src/openmagic_evals/harness",
    "packages/openmagic-runtime/pyproject.toml",
    "reference-apps/example-insurance/pyproject.toml",
    "apps/api/pyproject.toml",
    "apps/playground/pyproject.toml",
    "evals/pyproject.toml",
    "uv.lock",
)
_SNAPSHOT_EXCLUSIONS = frozenset({HELD_OUT_SEALED_PATH})


class HeldOutSeal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_version: str
    corpus_digest: str
    corpus_blob: str
    runner_frozen_at_commit: str
    corpus_introduced_at_commit: str
    locked_source_digest: str


_SEAL = HeldOutSeal.model_validate_json(
    files("openmagic_evals").joinpath("heldout_seal.json").read_text(encoding="utf-8")
)
HELD_OUT_CORPUS_VERSION = _SEAL.corpus_version
HELD_OUT_CORPUS_DIGEST = _SEAL.corpus_digest
HELD_OUT_SEALED_BLOB = _SEAL.corpus_blob
RUNNER_FROZEN_AT_COMMIT = _SEAL.runner_frozen_at_commit
HELD_OUT_SEALED_AT_COMMIT = _SEAL.corpus_introduced_at_commit
TUNING_LOCKED_PATHS = TUNING_LOCKED_ROOTS
TUNING_LOCKED_BLOBS = {"source_snapshot": _SEAL.locked_source_digest}
TUNING_LOCKED_SOURCE_DIGEST = _SEAL.locked_source_digest


def _git(repository_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )


def _source_snapshot(repository_root: Path, commit: str) -> str:
    listing = _git(repository_root, "ls-tree", "-r", commit, "--", *TUNING_LOCKED_ROOTS)
    if listing.returncode != 0:
        raise RuntimeError("Unable to inspect the frozen Agent implementation")
    entries = tuple(
        line
        for line in listing.stdout.splitlines()
        if line.rsplit("\t", maxsplit=1)[-1] not in _SNAPSHOT_EXCLUSIONS
    )
    return "sha256:" + hashlib.sha256("\n".join(entries).encode()).hexdigest()


def verify_held_out_seal(repository_root: Path) -> None:
    """Prove the runner froze first and the later corpus-only commit remains untouched."""

    repository_root = repository_root.resolve()
    actual_digest = canonical_digest([asdict(case) for case in HELD_OUT_CASES])
    current_blob = _git(repository_root, "hash-object", HELD_OUT_SEALED_PATH)
    frozen_blob = _git(
        repository_root,
        "rev-parse",
        f"{RUNNER_FROZEN_AT_COMMIT}:{HELD_OUT_SEALED_PATH}",
    )
    introduced_blob = _git(
        repository_root,
        "rev-parse",
        f"{HELD_OUT_SEALED_AT_COMMIT}:{HELD_OUT_SEALED_PATH}",
    )
    introduction_paths = _git(
        repository_root,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        HELD_OUT_SEALED_AT_COMMIT,
    )
    frozen_before_corpus = _git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        RUNNER_FROZEN_AT_COMMIT,
        HELD_OUT_SEALED_AT_COMMIT,
    )
    corpus_before_head = _git(
        repository_root,
        "merge-base",
        "--is-ancestor",
        HELD_OUT_SEALED_AT_COMMIT,
        "HEAD",
    )
    clean_runner = _git(repository_root, "diff", "--quiet", "HEAD", "--", *TUNING_LOCKED_ROOTS)
    valid = (
        actual_digest == HELD_OUT_CORPUS_DIGEST
        and current_blob.returncode == 0
        and current_blob.stdout.strip() == HELD_OUT_SEALED_BLOB
        and frozen_blob.returncode == 0
        and frozen_blob.stdout.strip() != HELD_OUT_SEALED_BLOB
        and introduced_blob.returncode == 0
        and introduced_blob.stdout.strip() == HELD_OUT_SEALED_BLOB
        and introduction_paths.returncode == 0
        and tuple(introduction_paths.stdout.splitlines()) == (HELD_OUT_SEALED_PATH,)
        and frozen_before_corpus.returncode == 0
        and corpus_before_head.returncode == 0
        and clean_runner.returncode == 0
        and _source_snapshot(repository_root, RUNNER_FROZEN_AT_COMMIT) == _SEAL.locked_source_digest
        and _source_snapshot(repository_root, "HEAD") == _SEAL.locked_source_digest
    )
    if not valid:
        raise RuntimeError("held-out Agent corpus does not satisfy freeze-before-exposure")


__all__ = [
    "HELD_OUT_CASES",
    "HELD_OUT_CORPUS_DIGEST",
    "HELD_OUT_CORPUS_VERSION",
    "HELD_OUT_SEALED_AT_COMMIT",
    "HELD_OUT_SEALED_BLOB",
    "HELD_OUT_SEALED_PATH",
    "RUNNER_FROZEN_AT_COMMIT",
    "TUNING_LOCKED_BLOBS",
    "TUNING_LOCKED_PATHS",
    "TUNING_LOCKED_ROOTS",
    "TUNING_LOCKED_SOURCE_DIGEST",
    "verify_held_out_seal",
]
