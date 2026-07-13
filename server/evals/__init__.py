"""Evaluation contracts for comparing OpenMagic runtime behavior."""

from .coordination import (
    PAIRED_SCENARIO_IDS,
    RENEWAL_COORDINATION_SCENARIOS,
    CoordinationDiagnostics,
    CoordinationReport,
    CoordinationScenario,
    CoordinationToolStep,
    CoordinationTrial,
    PairedCoordinationEvaluator,
    build_coordination_report,
    write_coordination_report,
)

__all__ = [
    "PAIRED_SCENARIO_IDS",
    "RENEWAL_COORDINATION_SCENARIOS",
    "CoordinationDiagnostics",
    "CoordinationReport",
    "CoordinationScenario",
    "CoordinationToolStep",
    "CoordinationTrial",
    "PairedCoordinationEvaluator",
    "build_coordination_report",
    "write_coordination_report",
]
