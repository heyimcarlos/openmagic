"""Stable facade over concern-specific private evidence inspectors."""

from openmagic_evals.evidence._inspection_demo import (
    AgentSafetyObservation,
    DemoInspection,
    VerificationDemoObservation,
)
from openmagic_evals.evidence._inspection_durable_chain import (
    DurableChainInspection,
    DurableChainObservation,
)
from openmagic_evals.evidence._inspection_process import (
    AttemptAuthority,
    DeliveryAuthority,
    ProcessInspection,
    QueueState,
)
from openmagic_evals.evidence._inspection_race import RaceInspection, TransactionState


class EvidenceInspection(
    ProcessInspection,
    RaceInspection,
    DemoInspection,
    DurableChainInspection,
):
    """Compose typed observations without mixing their query ownership."""


__all__ = [
    "AgentSafetyObservation",
    "AttemptAuthority",
    "DeliveryAuthority",
    "DurableChainObservation",
    "EvidenceInspection",
    "QueueState",
    "TransactionState",
    "VerificationDemoObservation",
]
