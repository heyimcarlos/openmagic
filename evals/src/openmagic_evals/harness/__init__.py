"""Public TestDeployment and independent boot verifier seams."""

from openmagic_evals.harness.deployment import ManagedProcess, ProcessRole, TestDeployment
from openmagic_evals.harness.local_provider import LocalEmailProvider
from openmagic_evals.harness.renewal_scenario import (
    approve_renewal,
    prepare_renewal_approval,
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
    "ProcessRole",
    "TestDeployment",
    "VerificationScenario",
    "approve_renewal",
    "issue_verification_challenge",
    "prepare_renewal_approval",
    "renewal_context",
    "wait_for_database_fault_window",
    "wait_for_renewal_completion",
]
