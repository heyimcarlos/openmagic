"""Qualified Application Policy for protected renewal Commands."""

from __future__ import annotations

from uuid import UUID

from openmagic_runtime.delivery import DeliveryRetryPolicy
from openmagic_runtime.kernel.definitions import RetryPolicy

from example_insurance.verification_authority_records import AuthoritySnapshot
from example_insurance.verification_commands import ProtectedOutcome, VerificationPurpose

VERIFICATION_ATTEMPT_RETRY_POLICY = RetryPolicy((0, 0))
MAX_FAILED_CODE_ATTEMPTS = 5
VERIFICATION_DELIVERY_RETRY_POLICY = DeliveryRetryPolicy(
    version=1,
    max_attempts=3,
    delays_seconds=(0, 1),
    lease_seconds=1,
    retryable_failure_classes=("transient_rendering", "transient_database"),
    terminal_failure_classes=("invalid_content", "policy_rejected"),
)


class VerificationPolicy:
    purpose: VerificationPurpose = "renewal.read_approved_details"

    def authorize(
        self,
        authority: AuthoritySnapshot,
        *,
        party_id: UUID,
        thread_id: UUID,
        purpose: str,
    ) -> ProtectedOutcome:
        if purpose != self.purpose:
            return "wrong_purpose"
        if authority.thread_id != thread_id:
            return "wrong_thread"
        if authority.authorized_actor_id != str(party_id) or not authority.party_exists:
            return "wrong_party"
        if authority.lifecycle != "active":
            return "workflow_closed"
        if not authority.identifier_current_and_verified:
            return "identifier_revoked"
        if authority.workflow_authority_revoked or not authority.active_broker_authority:
            return "authority_revoked"
        if not authority.exact_approval_grant:
            return "approval_required"
        return "authorized"


__all__ = [
    "MAX_FAILED_CODE_ATTEMPTS",
    "VERIFICATION_ATTEMPT_RETRY_POLICY",
    "VERIFICATION_DELIVERY_RETRY_POLICY",
    "VerificationPolicy",
]
