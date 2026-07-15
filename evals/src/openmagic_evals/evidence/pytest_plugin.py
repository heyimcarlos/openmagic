"""Small pytest result adapter used by the explicit evidence command."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--openmagic-evidence-results", action="store", default=None)


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
