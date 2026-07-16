"""Small pytest result adapter used by the explicit evidence command."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from openmagic_evals.evidence.postgres_provenance import record_postgres_deployments

_RECORDER_ATTRIBUTE = "_openmagic_postgres_recorder"


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--openmagic-evidence-results", action="store", default=None)
    parser.addoption("--openmagic-postgres-directory", action="store", default=None)


def pytest_configure(config: Any) -> None:
    directory = config.getoption("--openmagic-postgres-directory")
    if directory is None:
        return
    recorder = record_postgres_deployments(Path(directory))
    recorder.__enter__()
    setattr(config, _RECORDER_ATTRIBUTE, recorder)


def pytest_unconfigure(config: Any) -> None:
    recorder: AbstractContextManager[None] | None = getattr(
        config,
        _RECORDER_ATTRIBUTE,
        None,
    )
    if recorder is not None:
        recorder.__exit__(None, None, None)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    output = session.config.getoption("--openmagic-evidence-results")
    if output is None:
        return
    reports: dict[str, dict[str, object]] = {}
    terminal = session.config.pluginmanager.getplugin("terminalreporter")
    for outcome in ("passed", "failed", "skipped", "error"):
        for report in terminal.stats.get(outcome, ()):
            if getattr(report, "when", "call") not in {"call", "setup"}:
                continue
            current = reports.get(report.nodeid)
            status = "infrastructure_error" if outcome == "error" else outcome
            candidate: dict[str, object] = {
                "status": status,
                "duration_seconds": round(float(getattr(report, "duration", 0.0)), 6),
                "detail_digest": "sha256:"
                + hashlib.sha256(str(getattr(report, "longrepr", "")).encode()).hexdigest(),
            }
            if current is None or current["status"] in {"passed", "skipped"}:
                reports[report.nodeid] = candidate
    document = {
        "exit_status": int(exitstatus),
        "tests": {node: reports[node] for node in sorted(reports)},
    }
    Path(output).write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "pytest_addoption",
    "pytest_configure",
    "pytest_sessionfinish",
    "pytest_unconfigure",
]
