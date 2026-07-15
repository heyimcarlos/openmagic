"""Public TestDeployment and independent boot verifier seams."""

from openmagic_evals.harness.deployment import ManagedProcess, ProcessRole, TestDeployment
from openmagic_evals.harness.verifier import BootVerdict, DeploymentVerifier

__all__ = [
    "BootVerdict",
    "DeploymentVerifier",
    "ManagedProcess",
    "ProcessRole",
    "TestDeployment",
]
