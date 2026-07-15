"""Installed Example Insurance renewal Workflow Control Plane."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import psycopg
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentExecutionInput,
)
from openmagic_runtime.commands import (
    CommandDispatcher,
    CommandReceipt,
    CommandUnavailable,
    StateConflict,
)
from openmagic_runtime.delivery import (
    ClaimDelivery,
    ClaimedDelivery,
    DeliveryAcknowledgement,
    DeliveryWork,
    claim_delivery_once,
)
from openmagic_runtime.execution import (
    DeterministicExecutor,
    Executor,
    FreshAgentExecutor,
)
from openmagic_runtime.kernel.control import KernelControl, StartInstance
from openmagic_runtime.kernel.definitions import DefinitionCatalog
from openmagic_runtime.kernel.work import (
    ClaimedAttempt,
)
from openmagic_runtime.threads import ThreadAccess
from psycopg import Connection

from example_insurance.application_registry import application_command_dispatcher
from example_insurance.renewal_attempt_control import RenewalAttemptControl
from example_insurance.renewal_commands import (
    AcceptRenewalEffectObservation,
    AcceptRenewalEffectObservationInput,
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ApproveRenewalDraftResult,
    AuthorizeRenewalEmailDispatch,
    AuthorizeRenewalEmailDispatchInput,
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    CancelRenewalOutreachResult,
    RenewalEffectObservation,
    RequestRenewalRevision,
    RequestRenewalRevisionInput,
    RequestRenewalRevisionResult,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityInput,
    RevokeRenewalAuthorityResult,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
    StartRenewalOutreachResult,
    WorkflowAttemptResult,
)
from example_insurance.renewal_definition import RENEWAL_DEFINITION
from example_insurance.renewal_effect_control import RenewalEffectControl
from example_insurance.renewal_effect_types import (
    ExternalEffectPermit,
    RenewalApprovalPresentation,
    RenewalEmailEffect,
)
from example_insurance.renewal_effects import (
    AuthorizedEmailEffectExecutor,
    EmailProviderClient,
    EmailReconciliationExecutor,
)
from example_insurance.renewal_evidence import RenewalEvidenceProjector
from example_insurance.renewal_facts import RenewalFacts, RenewalFactSource
from example_insurance.renewal_lifecycle import RenewalLifecycleControl
from example_insurance.renewal_records import CommandEventLineage
from example_insurance.renewal_registry import (
    RenewalCommandHandlers,
)
from example_insurance.renewal_review_control import RenewalReviewControl
from example_insurance.renewal_workflow_records import (
    record_workflow,
    workflow_exists,
)
from example_insurance.verification_attempt_control import VerificationAttemptControl
from example_insurance.verification_codes import VerificationCodes
from example_insurance.verification_commands import (
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    ProvisionVerificationAuthorityResult,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsInput,
    RequestProtectedRenewalDetailsResult,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityInput,
    RevokeVerificationAuthorityResult,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
    SubmitVerificationCodeResult,
    VerificationAuthorityTarget,
)
from example_insurance.verification_control import VerificationControl
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from example_insurance.verification_registry import VerificationCommandHandlers
from example_insurance.workflow_worker_control import WorkflowWorkerControl


@dataclass(frozen=True)
class RenewalDraftCandidate:
    subject: str
    body: str


def _draft_agent_factory() -> Callable[[AgentExecutionInput], RenewalDraftCandidate]:
    def run(execution: AgentExecutionInput) -> RenewalDraftCandidate:
        run_input = execution.run_input
        if run_input.configuration != AgentConfiguration(
            agent_key="example_insurance.renewal_draft",
            agent_version=1,
            instruction_key="example_insurance.renewal_draft.en_ca.v1",
        ):
            raise ValueError("Persisted Agent configuration is unsupported")
        if run_input.task.task_type != "renewal.draft" or run_input.task.task_version != 1:
            raise ValueError("Persisted Agent task is unsupported")
        if run_input.audience_context != AgentAudience("workflow_role", "broker"):
            raise ValueError("Persisted Agent audience is unsupported")
        if run_input.locale != "en-CA":
            raise ValueError("Persisted Agent locale is unsupported")
        value = run_input.task.input
        expected_fields = {
            "expiring_premium_cents",
            "policy_number",
            "policyholder_name",
            "policyholder_email",
            "renewal_date",
            "revision_instruction",
            "thread_id",
            "workflow_id",
        }
        if (
            value.schema_key != "example_insurance.renewal_draft.input"
            or value.schema_version != 1
            or {field.name for field in value.fields} != expected_fields
        ):
            raise ValueError("Persisted Agent task input is unsupported")
        premium = int(value.value("expiring_premium_cents")) / 100
        context_note = ""
        if execution.thread_context.messages:
            context_note = " Prior Thread context: " + execution.thread_context.messages[-1].content
        revision_note = ""
        if value.value("revision_instruction"):
            revision_note = f" Requested revision: {value.value('revision_instruction')}"
        return RenewalDraftCandidate(
            subject=f"Renewal review for policy {value.value('policy_number')}",
            body=(
                f"Hello {value.value('policyholder_name')}, your policy renews on "
                f"{value.value('renewal_date')}. The expiring premium is CAD {premium:,.2f}. "
                f"Please review this draft before any renewal email is sent."
                f"{revision_note}{context_note}"
            ),
        )

    return run


class ExampleInsurance:
    def __init__(
        self,
        *,
        database_url: str,
        email_provider_url: str | None = None,
        verification_code_secret: bytes | None = None,
        challenge_ttl_seconds: int = 600,
        session_ttl_seconds: int = 900,
    ) -> None:
        self._database_url = database_url
        self._review_control = RenewalReviewControl()
        self._lifecycle_control = RenewalLifecycleControl()
        self._effect_control = RenewalEffectControl()
        self._attempt_control = RenewalAttemptControl(effect_control=self._effect_control)
        self._verification_control = (
            VerificationControl(
                codes=VerificationCodes(verification_code_secret),
                challenge_ttl_seconds=challenge_ttl_seconds,
                session_ttl_seconds=session_ttl_seconds,
            )
            if verification_code_secret is not None
            else None
        )
        self._dispatcher: CommandDispatcher = application_command_dispatcher(
            database_url=database_url,
            renewal_handlers=RenewalCommandHandlers(
                start=self._handle_start,
                approve=self._handle_approval,
                revise=self._handle_revision,
                revoke=self._handle_revocation,
                cancel=self._handle_cancellation,
                authorize_dispatch=self._handle_dispatch_authorization,
                accept_effect_observation=self._handle_effect_observation,
            ),
            verification_handlers=VerificationCommandHandlers(
                provision=self._handle_verification_provision,
                request=self._handle_protected_request,
                revoke=self._handle_verification_revocation,
                submit=self._handle_verification_submission,
            ),
        )
        self._renewal_facts = RenewalFactSource(database_url=database_url)
        executors: dict[str, Executor] = {
            "example_insurance.renewal_facts.v1": DeterministicExecutor(self._renewal_facts.gather),
            "example_insurance.renewal_draft_agent.v1": FreshAgentExecutor(
                _draft_agent_factory,
                result_class=RenewalDraftCandidate,
                encoder=lambda candidate: {
                    "subject": candidate.subject,
                    "body": candidate.body,
                },
                timeout_seconds=5,
            ),
        }
        verification_attempts = None
        if self._verification_control is not None:
            executors["example_insurance.verification_delivery.v1"] = DeterministicExecutor(
                lambda value: {"challenge_id": str(value["challenge_id"])}
            )
            verification_attempts = VerificationAttemptControl(
                codes=self._verification_control.codes
            )
        if email_provider_url is not None:
            executors.update(
                {
                    "example_insurance.email_provider.v1": AuthorizedEmailEffectExecutor(
                        database_url=database_url,
                        client=EmailProviderClient(provider_url=email_provider_url),
                    ),
                    "example_insurance.email_reconciliation.v1": EmailReconciliationExecutor(
                        provider_url=email_provider_url
                    ),
                }
            )
        self._workers = WorkflowWorkerControl(
            database_url=database_url,
            dispatcher=self._dispatcher,
            executors=executors,
            attempts=self._attempt_control,
            verification_attempts=verification_attempts,
        )

    def prepare(self) -> None:
        DefinitionCatalog(database_url=self._database_url).register(RENEWAL_DEFINITION)
        DefinitionCatalog(database_url=self._database_url).register(VERIFICATION_DEFINITION)

    def replace_renewal_facts(self, facts: RenewalFacts) -> None:
        self._renewal_facts.replace(facts)

    def start_renewal_outreach(
        self, command: StartRenewalOutreach
    ) -> CommandReceipt[StartRenewalOutreachResult]:
        return self._dispatcher.execute(
            command_type="renewal.start_outreach",
            schema_version=1,
            command=command,
        )

    def provision_verification_authority(
        self, command: ProvisionVerificationAuthority
    ) -> CommandReceipt[ProvisionVerificationAuthorityResult]:
        return self._dispatcher.execute(
            command_type="verification.provision_authority",
            schema_version=1,
            command=command,
        )

    def request_protected_renewal_details(
        self, command: RequestProtectedRenewalDetails
    ) -> CommandReceipt[RequestProtectedRenewalDetailsResult]:
        return self._dispatcher.execute(
            command_type="renewal.read_approved_details",
            schema_version=1,
            command=command,
        )

    def revoke_verification_authority(
        self, command: RevokeVerificationAuthority
    ) -> CommandReceipt[RevokeVerificationAuthorityResult]:
        return self._dispatcher.execute(
            command_type="verification.revoke_authority",
            schema_version=1,
            command=command,
        )

    def submit_verification_code(
        self, command: SubmitVerificationCode
    ) -> CommandReceipt[SubmitVerificationCodeResult]:
        return self._dispatcher.execute(
            command_type="verification.submit_code",
            schema_version=1,
            command=command,
        )

    def renewal_approval_presentation(self, workflow_id: UUID) -> RenewalApprovalPresentation:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return self._review_control.presentation(connection, workflow_id)

    def approve_renewal_draft(
        self, command: ApproveRenewalDraft
    ) -> CommandReceipt[ApproveRenewalDraftResult]:
        return self._dispatcher.execute(
            command_type="renewal.approve_draft",
            schema_version=1,
            command=command,
        )

    def request_renewal_revision(
        self, command: RequestRenewalRevision
    ) -> CommandReceipt[RequestRenewalRevisionResult]:
        return self._dispatcher.execute(
            command_type="renewal.request_revision",
            schema_version=1,
            command=command,
        )

    def revoke_renewal_authority(
        self, command: RevokeRenewalAuthority
    ) -> CommandReceipt[RevokeRenewalAuthorityResult]:
        return self._dispatcher.execute(
            command_type="renewal.revoke_approval_authority",
            schema_version=1,
            command=command,
        )

    def cancel_renewal_outreach(
        self, command: CancelRenewalOutreach
    ) -> CommandReceipt[CancelRenewalOutreachResult]:
        return self._dispatcher.execute(
            command_type="renewal.cancel_outreach",
            schema_version=1,
            command=command,
        )

    def authorize_email_dispatch(
        self, *, attempt: ClaimedAttempt, worker_id: str
    ) -> CommandReceipt[ExternalEffectPermit]:
        return self._workers.authorize_dispatch(attempt=attempt, worker_id=worker_id)

    def accept_renewal_effect_observation(
        self,
        command: AcceptRenewalEffectObservation,
    ) -> CommandReceipt[WorkflowAttemptResult]:
        return self._workers.accept_effect_observation(command)

    def run_workflow_worker_once(
        self, *, worker_id: str, worker_shutdown: Event | None = None
    ) -> WorkflowAttemptResult | None:
        return self._workers.run_once(worker_id=worker_id, worker_shutdown=worker_shutdown)

    def recover_expired_workflow_attempt(self) -> bool:
        return self._workers.recover_expired()

    def claim_workflow_attempt(
        self, *, worker_id: str, claim_request_id: UUID
    ) -> ClaimedAttempt | None:
        return self._workers.claim(worker_id=worker_id, claim_request_id=claim_request_id)

    def complete_workflow_attempt(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        worker_shutdown: Event | None = None,
    ) -> WorkflowAttemptResult:
        return self._workers.complete(
            attempt=attempt,
            worker_id=worker_id,
            worker_shutdown=worker_shutdown,
        )

    def submit_workflow_observation(
        self,
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        return self._workers.submit_observation(
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )

    def run_delivery_worker_once(self, *, worker_id: str) -> DeliveryAcknowledgement | None:
        claim = self.claim_delivery_attempt(worker_id=worker_id, claim_request_id=uuid4())
        if claim is None:
            return None
        return self.complete_delivery_attempt(claim=claim, worker_id=worker_id)

    def claim_delivery_attempt(
        self, *, worker_id: str, claim_request_id: UUID
    ) -> ClaimedDelivery | None:
        return claim_delivery_once(
            database_url=self._database_url,
            request=ClaimDelivery(claim_request_id=claim_request_id, worker_id=worker_id),
        )

    def complete_delivery_attempt(
        self, *, claim: ClaimedDelivery, worker_id: str
    ) -> DeliveryAcknowledgement:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            return DeliveryWork(connection).acknowledge(
                claim,
                worker_id=worker_id,
                proposed_thread_id=claim.thread_id,
            )

    def replay_delivery_acknowledgement(
        self, *, delivery_attempt_id: UUID
    ) -> DeliveryAcknowledgement:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            return DeliveryWork(connection).replay_acknowledgement(delivery_attempt_id)

    def renewal_evidence_json(self, workflow_id: UUID) -> str:
        return RenewalEvidenceProjector(database_url=self._database_url).to_json(workflow_id)

    def _handle_start(
        self,
        command: StartRenewalOutreach,
        connection: Connection[tuple[Any, ...]],
    ) -> StartRenewalOutreachResult:
        try:
            ThreadAccess(connection).require(command.input.thread_id)
        except KeyError:
            raise StateConflict("The exact Thread does not exist") from None
        if workflow_exists(connection, command.input.workflow_id):
            raise StateConflict("The renewal Workflow already exists")
        start = KernelControl(connection).start(
            StartInstance(
                command_id=command.command_id,
                definition_key=RENEWAL_DEFINITION.identity.key,
                definition_version=RENEWAL_DEFINITION.identity.version,
                instance_input={
                    "workflow_id": str(command.input.workflow_id),
                    "thread_id": str(command.input.thread_id),
                    "policy_id": str(command.input.policy_id),
                },
                route_input={
                    "policy_id": str(command.input.policy_id),
                    "policy_number": command.input.policy_number,
                    "policyholder_name": command.input.policyholder_name,
                    "policyholder_email": command.input.policyholder_email,
                    "renewal_date": command.input.renewal_date,
                    "expiring_premium_cents": command.input.expiring_premium_cents,
                },
            )
        )
        record_workflow(
            connection,
            command_id=command.command_id,
            instance_id=start.instance_id,
            actor=command.actor,
            value=command.input,
        )
        return StartRenewalOutreachResult(
            workflow_id=command.input.workflow_id,
            instance_id=start.instance_id,
            thread_id=command.input.thread_id,
        )

    def _handle_approval(
        self,
        command: ApproveRenewalDraft,
        connection: Connection[tuple[Any, ...]],
    ) -> ApproveRenewalDraftResult:
        return self._review_control.approve(command, connection)

    def _handle_revision(
        self,
        command: RequestRenewalRevision,
        connection: Connection[tuple[Any, ...]],
    ) -> RequestRenewalRevisionResult:
        return self._review_control.request_revision(command, connection)

    def _handle_revocation(
        self,
        command: RevokeRenewalAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> RevokeRenewalAuthorityResult:
        return self._lifecycle_control.revoke(command, connection)

    def _handle_cancellation(
        self,
        command: CancelRenewalOutreach,
        connection: Connection[tuple[Any, ...]],
    ) -> CancelRenewalOutreachResult:
        return self._lifecycle_control.cancel(command, connection)

    def _handle_dispatch_authorization(
        self,
        command: AuthorizeRenewalEmailDispatch,
        connection: Connection[tuple[Any, ...]],
    ) -> ExternalEffectPermit:
        return self._effect_control.authorize_dispatch(
            connection,
            attempt=command.input.attempt,
            worker_id=command.input.worker_id,
            lineage=CommandEventLineage(command.actor, command.command_id),
        )

    def _handle_effect_observation(
        self,
        command: AcceptRenewalEffectObservation,
        connection: Connection[tuple[Any, ...]],
    ) -> WorkflowAttemptResult:
        return self._attempt_control.accept_effect_observation(
            connection,
            attempt=command.input.attempt,
            worker_id=command.input.worker_id,
            observation={
                "classification": command.input.observation.classification,
                "provider_request_id": command.input.observation.provider_request_id,
            },
            lineage=CommandEventLineage(command.actor, command.command_id),
        )

    def _verification(self) -> VerificationControl:
        if self._verification_control is None:
            raise CommandUnavailable("Verification requires an explicit code secret")
        return self._verification_control

    def _handle_verification_provision(
        self,
        command: ProvisionVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> ProvisionVerificationAuthorityResult:
        return self._verification().provision(command, connection)

    def _handle_protected_request(
        self,
        command: RequestProtectedRenewalDetails,
        connection: Connection[tuple[Any, ...]],
    ) -> RequestProtectedRenewalDetailsResult:
        return self._verification().request(command, connection)

    def _handle_verification_revocation(
        self,
        command: RevokeVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> RevokeVerificationAuthorityResult:
        return self._verification().revoke(command, connection)

    def _handle_verification_submission(
        self,
        command: SubmitVerificationCode,
        connection: Connection[tuple[Any, ...]],
    ) -> SubmitVerificationCodeResult:
        return self._verification().submit(command, connection)


__all__ = [
    "AcceptRenewalEffectObservation",
    "AcceptRenewalEffectObservationInput",
    "ApproveRenewalDraft",
    "ApproveRenewalDraftInput",
    "ApproveRenewalDraftResult",
    "AuthorizeRenewalEmailDispatch",
    "AuthorizeRenewalEmailDispatchInput",
    "CancelRenewalOutreach",
    "CancelRenewalOutreachInput",
    "CancelRenewalOutreachResult",
    "ExampleInsurance",
    "ProvisionVerificationAuthority",
    "ProvisionVerificationAuthorityInput",
    "ProvisionVerificationAuthorityResult",
    "RenewalApprovalPresentation",
    "RenewalEffectObservation",
    "RenewalEmailEffect",
    "RenewalFacts",
    "RequestProtectedRenewalDetails",
    "RequestProtectedRenewalDetailsInput",
    "RequestProtectedRenewalDetailsResult",
    "RequestRenewalRevision",
    "RequestRenewalRevisionInput",
    "RequestRenewalRevisionResult",
    "RevokeRenewalAuthority",
    "RevokeRenewalAuthorityInput",
    "RevokeRenewalAuthorityResult",
    "RevokeVerificationAuthority",
    "RevokeVerificationAuthorityInput",
    "RevokeVerificationAuthorityResult",
    "StartRenewalOutreach",
    "StartRenewalOutreachInput",
    "StartRenewalOutreachResult",
    "SubmitVerificationCode",
    "SubmitVerificationCodeInput",
    "SubmitVerificationCodeResult",
    "VerificationAuthorityTarget",
    "WorkflowAttemptResult",
]
