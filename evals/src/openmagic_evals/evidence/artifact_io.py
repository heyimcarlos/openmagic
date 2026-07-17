"""Atomic schema validation and redaction gate for canonical evidence artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from openmagic_evals.evidence.contracts import (
    Artifact,
    canonical_artifact_json,
    parse_artifact,
)
from openmagic_evals.evidence.redaction import audit_redaction


def write_artifact(path: Path, artifact: Artifact) -> None:
    document = canonical_artifact_json(artifact)
    parse_artifact(document)
    audit_redaction(json.loads(document))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(path)


__all__ = ["write_artifact"]
