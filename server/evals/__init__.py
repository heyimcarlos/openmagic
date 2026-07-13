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
from .recovery import (
    RECOVERY_SCENARIOS,
    RecoveryCaseEvidence,
    RecoveryReport,
    build_recovery_case,
    build_recovery_report,
    write_recovery_report,
)
from .v0_evidence import V0EvidenceLane, V0EvidenceReport, run_v0_evidence

__all__ = [
    "PAIRED_SCENARIO_IDS",
    "RECOVERY_SCENARIOS",
    "RENEWAL_COORDINATION_SCENARIOS",
    "CoordinationDiagnostics",
    "CoordinationReport",
    "CoordinationScenario",
    "CoordinationToolStep",
    "CoordinationTrial",
    "PairedCoordinationEvaluator",
    "RecoveryCaseEvidence",
    "RecoveryReport",
    "V0EvidenceLane",
    "V0EvidenceReport",
    "build_coordination_report",
    "build_recovery_case",
    "build_recovery_report",
    "run_v0_evidence",
    "write_coordination_report",
    "write_recovery_report",
]
