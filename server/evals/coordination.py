"""Public paired coordination evaluation interface."""

from .coordination_contracts import (
    RENEWAL_COORDINATION_SCENARIOS,
    CoordinationDiagnostics,
    CoordinationReport,
    CoordinationScenario,
    CoordinationTrial,
)
from .coordination_report import build_coordination_report, write_coordination_report
from .coordination_runner import PairedCoordinationEvaluator

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
