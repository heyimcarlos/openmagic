"""Evaluation contracts for comparing OpenMagic runtime behavior."""

from .coordination import (
    RENEWAL_COORDINATION_SCENARIOS,
    CoordinationDiagnostics,
    CoordinationReport,
    CoordinationScenario,
    CoordinationTrial,
    PairedCoordinationEvaluator,
    build_coordination_report,
    write_coordination_report,
)

__all__ = [
    "RENEWAL_COORDINATION_SCENARIOS",
    "CoordinationDiagnostics",
    "CoordinationReport",
    "CoordinationScenario",
    "CoordinationTrial",
    "PairedCoordinationEvaluator",
    "build_coordination_report",
    "write_coordination_report",
]
