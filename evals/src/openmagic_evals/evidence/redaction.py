"""Fail-closed audit for canonical evidence artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(^|_)(api_key|authorization|credential|password|prompt|raw_content|raw_message|secret|token|verification_code)($|_)",
    re.IGNORECASE,
)
_SENSITIVE_VALUES = (
    re.compile(r"postgres(?:ql)?://[^\s:/]+:[^\s@]+@", re.IGNORECASE),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_-]{12,}\b"),
)
_PYTEST_NODE_ID = re.compile(r"^(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py::test_[A-Za-z0-9_]+$")
_PYTEST_RESULT_FIELDS = {"detail_digest", "duration_seconds", "status"}


class RedactionViolation(ValueError):
    pass


@dataclass(frozen=True)
class RedactionAudit:
    passed: bool
    visited_values: int


def audit_redaction(value: object) -> RedactionAudit:
    visited = 0

    def visit(item: Any, path: str) -> None:
        nonlocal visited
        visited += 1
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}"
                is_test_result = (
                    path.endswith(".test_results")
                    and _PYTEST_NODE_ID.fullmatch(key_text) is not None
                    and isinstance(child, dict)
                    and set(child) == _PYTEST_RESULT_FIELDS
                )
                if _SENSITIVE_KEYS.search(key_text) and not is_test_result:
                    raise RedactionViolation(f"sensitive field is forbidden at {child_path}")
                visit(child, child_path)
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
            return
        if isinstance(item, str):
            for pattern in _SENSITIVE_VALUES:
                if pattern.search(item):
                    raise RedactionViolation(f"secret-like value is forbidden at {path}")

    visit(value, "$artifact")
    return RedactionAudit(passed=True, visited_values=visited)


__all__ = ["RedactionAudit", "RedactionViolation", "audit_redaction"]
