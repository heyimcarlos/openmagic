from __future__ import annotations

from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.renewal_policies import (
    RENEWAL_ATTEMPT_RETRY_POLICY,
    RenewalWorkflowPolicy,
)


def test_workflow_recovery_and_step_templates_share_one_finite_attempt_budget() -> None:
    policy = RenewalWorkflowPolicy()

    assert RENEWAL_ATTEMPT_RETRY_POLICY.max_attempts == 3
    assert {template.retry_policy for template in RENEWAL_DEFINITION.step_templates} == {
        RENEWAL_ATTEMPT_RETRY_POLICY
    }
    assert (
        policy.expired_attempt(template_key="gather_renewal_facts", attempt_number=2).action
        == "retry"
    )
    exhausted = policy.expired_attempt(
        template_key="gather_renewal_facts",
        attempt_number=RENEWAL_ATTEMPT_RETRY_POLICY.max_attempts,
    )
    assert exhausted.action == "fail"
    assert exhausted.failure == {"class": "attempt_budget_exhausted"}
