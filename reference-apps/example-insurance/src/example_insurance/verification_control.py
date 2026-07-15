"""Stable application facade for Example Insurance verification policy."""

from __future__ import annotations

from typing import Any

from openmagic_runtime.threads import ThreadStore
from psycopg import Connection

from example_insurance.verification_challenge_lifecycle import VerificationChallengeLifecycle
from example_insurance.verification_codes import VerificationCodes
from example_insurance.verification_commands import (
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityResult,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsResult,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityResult,
    SubmitVerificationCode,
    SubmitVerificationCodeResult,
)
from example_insurance.verification_policy import VerificationPolicy
from example_insurance.verification_protected_delivery import ProtectedRenewalDeliveryControl
from example_insurance.verification_request_control import VerificationRequestControl
from example_insurance.verification_submission_control import VerificationSubmissionControl


class VerificationControl:
    def __init__(
        self,
        *,
        codes: VerificationCodes,
        threads: ThreadStore,
        challenge_ttl_seconds: int = 600,
        session_ttl_seconds: int = 900,
    ) -> None:
        if challenge_ttl_seconds <= 0 or session_ttl_seconds <= 0:
            raise ValueError("Verification expiry durations must be positive")
        policy = VerificationPolicy()
        lifecycle = VerificationChallengeLifecycle()
        deliveries = ProtectedRenewalDeliveryControl()
        self._requests = VerificationRequestControl(
            challenge_ttl_seconds=challenge_ttl_seconds,
            policy=policy,
            lifecycle=lifecycle,
            deliveries=deliveries,
            threads=threads,
        )
        self._submissions = VerificationSubmissionControl(
            codes=codes,
            session_ttl_seconds=session_ttl_seconds,
            policy=policy,
            lifecycle=lifecycle,
            deliveries=deliveries,
        )

    def provision(
        self,
        command: ProvisionVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> ProvisionVerificationAuthorityResult:
        return self._requests.provision(command, connection)

    def request(
        self,
        command: RequestProtectedRenewalDetails,
        connection: Connection[tuple[Any, ...]],
    ) -> RequestProtectedRenewalDetailsResult:
        return self._requests.request(command, connection)

    def revoke(
        self,
        command: RevokeVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> RevokeVerificationAuthorityResult:
        return self._requests.revoke(command, connection)

    def submit(
        self,
        command: SubmitVerificationCode,
        connection: Connection[tuple[Any, ...]],
    ) -> SubmitVerificationCodeResult:
        return self._submissions.submit(command, connection)


__all__ = ["VerificationControl"]
