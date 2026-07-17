"""Private evidence helpers around public installed package seams."""

from openmagic_playground import ManagedProcess, PlaygroundDeployment, ProcessRole

from openmagic_evals.harness.local_provider import LocalEmailProvider
from openmagic_evals.harness.renewal_scenario import (
    approve_renewal,
    prepare_renewal_approval,
    prepare_synthetic_renewal_start,
    renewal_context,
    wait_for_database_fault_window,
    wait_for_renewal_completion,
)
from openmagic_evals.harness.verification_scenario import (
    VerificationScenario,
    issue_verification_challenge,
)
from openmagic_evals.harness.verifier import BootVerdict, DeploymentVerifier

__all__ = [
    "BootVerdict",
    "DeploymentVerifier",
    "LocalEmailProvider",
    "ManagedProcess",
    "PlaygroundDeployment",
    "ProcessRole",
    "VerificationScenario",
    "approve_renewal",
    "issue_verification_challenge",
    "prepare_renewal_approval",
    "prepare_synthetic_renewal_start",
    "renewal_context",
    "wait_for_database_fault_window",
    "wait_for_renewal_completion",
]
