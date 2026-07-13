"""Public paired coordination evaluation interface."""

from .coordination_contracts import (
    PAIRED_SCENARIO_IDS,
    RENEWAL_COORDINATION_SCENARIOS,
    CoordinationDiagnostics,
    CoordinationReport,
    CoordinationScenario,
    CoordinationToolStep,
    CoordinationTrial,
)
from .coordination_report import build_coordination_report, write_coordination_report
from .coordination_runner import PairedCoordinationEvaluator

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
