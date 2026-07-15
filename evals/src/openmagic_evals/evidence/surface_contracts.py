"""Versioned installable-surface contract shared by source and wheel audits."""

RUNTIME_PUBLIC_MODULES = (
    "agents.py",
    "commands.py",
    "delivery.py",
    "evidence.py",
    "execution.py",
    "kernel/control.py",
    "kernel/definitions.py",
    "kernel/inspection.py",
    "kernel/records.py",
    "kernel/work.py",
    "threads.py",
    "workers.py",
)
FORBIDDEN_EXPORTS = {"Connection", "MigrationBundle", "Model", "Repository", "Row", "Session"}
DELETED_IDENTIFIERS = (
    "server" + ".workflows",
    "workflow" + "_jobs",
    "workflow" + "_job_runs",
    "workflow" + "_events",
    "notifi" + "cations",
    "Workflow" + "Job",
    "Interaction" + "Agent",
)
EXPECTED_PRODUCTION_EDGES = (
    "example-insurance -> openmagic-runtime",
    "openmagic-api -> example-insurance",
    "openmagic-api -> openmagic-runtime",
)

__all__ = [
    "DELETED_IDENTIFIERS",
    "EXPECTED_PRODUCTION_EDGES",
    "FORBIDDEN_EXPORTS",
    "RUNTIME_PUBLIC_MODULES",
]
