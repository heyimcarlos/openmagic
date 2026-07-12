"""Closed, versioned Workflow and Job Kind contracts owned by OpenMagic."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator

from .contracts import WorkflowJobProposal, WorkflowProposal
from .errors import (
    InvalidWorkflowProposalError,
    UnknownWorkflowJobKindError,
    UnknownWorkflowKindError,
)

RENEWAL_OUTREACH_KIND = "renewal_outreach.v1"
DRAFT_RENEWAL_EMAIL_KIND = "renewal_email.draft.v1"
GMAIL_SEND_EMAIL_KIND = "gmail.send_email.v1"


ContractT = TypeVar("ContractT")


class KindSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RenewalOutreachInput(KindSchema):
    renewal_period: str = Field(pattern=r"^[0-9]{4}$")


class DraftRenewalEmailInput(KindSchema):
    recipient_name: str = Field(min_length=1, max_length=200)
    renewal_period: str = Field(pattern=r"^[0-9]{4}$")


class DraftRenewalEmailOutput(KindSchema):
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


class ProposedJobOutputReference(KindSchema):
    job_output: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    field: str = Field(min_length=1, max_length=64)


class JobOutputReference(KindSchema):
    job_output: UUID
    field: str = Field(min_length=1, max_length=64)


class GmailMessageEnvelope(KindSchema):
    sender_mailbox: EmailStr
    to: tuple[EmailStr, ...]
    cc: tuple[EmailStr, ...] = ()
    bcc: tuple[EmailStr, ...] = ()

    @field_validator("to")
    @classmethod
    def require_recipient(cls, value: tuple[EmailStr, ...]) -> tuple[EmailStr, ...]:
        if not value:
            raise ValueError("at least one recipient is required")
        return value


class ProposedGmailSendEmailInput(GmailMessageEnvelope):
    subject: ProposedJobOutputReference
    body: ProposedJobOutputReference


class GmailSendEmailInput(GmailMessageEnvelope):
    subject: JobOutputReference
    body: JobOutputReference


class GmailSendEmailOutput(KindSchema):
    provider: str
    acknowledged: bool
    tool_version: str
    message_id: str | None = None
    thread_id: str | None = None


class ExecutionStrategy(StrEnum):
    FRESH_EXECUTION_AGENT = "fresh_execution_agent"
    DETERMINISTIC_ADAPTER = "deterministic_adapter"


@dataclass(frozen=True)
class JobKindContract:
    kind: str
    input_schema: type[KindSchema]
    proposal_input_schema: type[KindSchema]
    output_schema: type[KindSchema]
    run_data_schema: type[KindSchema]
    materialize_input: Callable[[KindSchema, Mapping[str, UUID]], KindSchema]
    execution_strategy: ExecutionStrategy
    executor_key: str
    max_attempts: int
    success_event_type: str
    success_notification_kind: str | None
    retryable_error_codes: frozenset[str]
    retry_backoff: timedelta
    requires_approval: bool = False
    adapter_version: str | None = None
    provider_tool_version: str | None = None


def _never_complete(_view: object) -> bool:
    return False


@dataclass(frozen=True)
class WorkflowKindContract:
    kind: str
    input_schema: type[KindSchema]
    allowed_job_kinds: frozenset[str]
    completion_predicate: Callable[[WorkflowCompletionView], bool] = _never_complete


@dataclass(frozen=True)
class WorkflowCompletionJob:
    id: UUID
    kind: str
    status: str
    revises_job_id: UUID | None


@dataclass(frozen=True)
class WorkflowCompletionView:
    jobs: tuple[WorkflowCompletionJob, ...]
    uncertain_job_ids: frozenset[UUID]
    approved_dispatch_job_ids: frozenset[UUID]


@dataclass(frozen=True)
class ValidatedJobProposal:
    key: str
    kind: str
    proposed_input: KindSchema
    depends_on: tuple[str, ...]
    contract: JobKindContract


@dataclass(frozen=True)
class ValidatedWorkflowProposal:
    kind: str
    objective: str
    input: dict[str, Any]
    jobs: tuple[ValidatedJobProposal, ...]


class WorkflowKindRegistry:
    """Validate proposals and hide trusted execution configuration."""

    def __init__(
        self,
        workflow_kinds: tuple[WorkflowKindContract, ...],
        job_kinds: tuple[JobKindContract, ...],
    ) -> None:
        self._workflow_kinds = MappingProxyType(
            self._index_unique_kinds(workflow_kinds, "Workflow", lambda contract: contract.kind)
        )
        self._job_kinds = MappingProxyType(
            self._index_unique_kinds(job_kinds, "Workflow Job", lambda contract: contract.kind)
        )

    @staticmethod
    def _index_unique_kinds(
        contracts: tuple[ContractT, ...],
        label: str,
        kind_of: Callable[[ContractT], str],
    ) -> dict[str, ContractT]:
        indexed: dict[str, ContractT] = {}
        for contract in contracts:
            kind = kind_of(contract)
            if kind in indexed:
                raise ValueError(f"duplicate {label} Kind {kind!r}")
            indexed[kind] = contract
        return indexed

    def validate(self, proposal: WorkflowProposal) -> ValidatedWorkflowProposal:
        workflow_contract = self._workflow_kinds.get(proposal.kind)
        if workflow_contract is None:
            raise UnknownWorkflowKindError(proposal.kind)

        try:
            workflow_input = workflow_contract.input_schema.model_validate(proposal.input)
        except ValidationError as exc:
            raise InvalidWorkflowProposalError("invalid Workflow input") from exc

        keys = [job.key for job in proposal.jobs]
        if len(keys) != len(set(keys)):
            raise InvalidWorkflowProposalError("Job proposal keys must be unique")
        known_keys = set(keys)

        validated_jobs: list[ValidatedJobProposal] = []
        for job in proposal.jobs:
            contract = self._job_kinds.get(job.kind)
            if contract is None:
                raise UnknownWorkflowJobKindError(job.kind)
            if job.kind not in workflow_contract.allowed_job_kinds:
                raise InvalidWorkflowProposalError(
                    f"Job Kind {job.kind!r} is not allowed by {proposal.kind!r}"
                )
            self._validate_dependencies(job, known_keys)
            try:
                proposed_input = contract.proposal_input_schema.model_validate(job.input)
            except ValidationError as exc:
                raise InvalidWorkflowProposalError(f"invalid input for Job {job.key!r}") from exc
            validated_jobs.append(
                ValidatedJobProposal(
                    key=job.key,
                    kind=job.kind,
                    proposed_input=proposed_input,
                    depends_on=job.depends_on,
                    contract=contract,
                )
            )

        self._reject_cycles(tuple(validated_jobs))
        if proposal.kind == RENEWAL_OUTREACH_KIND:
            self._validate_renewal_graph(tuple(validated_jobs), workflow_input)

        return ValidatedWorkflowProposal(
            kind=proposal.kind,
            objective=proposal.objective,
            input=workflow_input.model_dump(mode="json"),
            jobs=tuple(validated_jobs),
        )

    def materialize_job_input(
        self,
        job: ValidatedJobProposal,
        job_ids: dict[str, UUID],
    ) -> dict[str, Any]:
        materialized = job.contract.materialize_input(job.proposed_input, job_ids)
        return materialized.model_dump(mode="json")

    def requires_approval(self, job_kind: str) -> bool:
        contract = self._job_kinds.get(job_kind)
        if contract is None:
            raise UnknownWorkflowJobKindError(job_kind)
        return contract.requires_approval

    def job_contract(self, job_kind: str) -> JobKindContract:
        """Resolve trusted execution behavior for a persisted Job Kind."""

        contract = self._job_kinds.get(job_kind)
        if contract is None:
            raise UnknownWorkflowJobKindError(job_kind)
        return contract

    def completion_satisfied(
        self,
        workflow_kind: str,
        view: WorkflowCompletionView,
    ) -> bool:
        contract = self._workflow_kinds.get(workflow_kind)
        if contract is None:
            raise UnknownWorkflowKindError(workflow_kind)
        return contract.completion_predicate(view)

    def validate_job_input(self, job_kind: str, value: dict[str, Any]) -> dict[str, Any]:
        """Revalidate persisted input before it crosses the execution boundary."""

        contract = self.job_contract(job_kind)
        try:
            validated = contract.input_schema.model_validate(value)
        except ValidationError as exc:
            raise InvalidWorkflowProposalError("invalid persisted Workflow Job input") from exc
        return validated.model_dump(mode="json")

    def validate_success_data(self, job_kind: str, value: object) -> dict[str, Any]:
        """Validate successful Run data before publishing canonical Job output."""

        contract = self.job_contract(job_kind)
        try:
            run_data = contract.run_data_schema.model_validate(value)
            output = contract.output_schema.model_validate(run_data)
        except ValidationError as exc:
            raise InvalidWorkflowProposalError("invalid successful Run data") from exc
        return output.model_dump(mode="json")

    @staticmethod
    def _validate_dependencies(job: WorkflowJobProposal, known_keys: set[str]) -> None:
        if len(job.depends_on) != len(set(job.depends_on)):
            raise InvalidWorkflowProposalError(f"Job {job.key!r} repeats a dependency")
        for dependency in job.depends_on:
            if dependency not in known_keys:
                raise InvalidWorkflowProposalError(
                    f"Job {job.key!r} depends on unknown Job {dependency!r}"
                )
            if dependency == job.key:
                raise InvalidWorkflowProposalError(f"Job {job.key!r} depends on itself")

    @staticmethod
    def _reject_cycles(jobs: tuple[ValidatedJobProposal, ...]) -> None:
        dependencies = {job.key: job.depends_on for job in jobs}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise InvalidWorkflowProposalError("Workflow Job graph contains a cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in dependencies[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in dependencies:
            visit(key)

    @staticmethod
    def _validate_renewal_graph(
        jobs: tuple[ValidatedJobProposal, ...],
        workflow_input: KindSchema,
    ) -> None:
        drafts = [job for job in jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND]
        sends = [job for job in jobs if job.kind == GMAIL_SEND_EMAIL_KIND]
        if len(jobs) != 2 or len(drafts) != 1 or len(sends) != 1:
            raise InvalidWorkflowProposalError(
                "renewal_outreach.v1 requires one Draft Job and one Send Job"
            )
        draft, send = drafts[0], sends[0]
        renewal = RenewalOutreachInput.model_validate(workflow_input)
        draft_input = DraftRenewalEmailInput.model_validate(draft.proposed_input)
        if draft_input.renewal_period != renewal.renewal_period:
            raise InvalidWorkflowProposalError(
                "the Draft Job renewal period must match the Workflow renewal period"
            )
        if draft.depends_on or send.depends_on != (draft.key,):
            raise InvalidWorkflowProposalError("the Send Job must depend only on the Draft Job")
        send_input = ProposedGmailSendEmailInput.model_validate(send.proposed_input)
        if send_input.subject.job_output != draft.key or send_input.subject.field != "subject":
            raise InvalidWorkflowProposalError("Send subject must reference the Draft subject")
        if send_input.body.job_output != draft.key or send_input.body.field != "body":
            raise InvalidWorkflowProposalError("Send body must reference the Draft body")


def _keep_validated_input(
    input_value: KindSchema,
    _job_ids: Mapping[str, UUID],
) -> KindSchema:
    return input_value


def _materialize_gmail_input(
    input_value: KindSchema,
    job_ids: Mapping[str, UUID],
) -> KindSchema:
    proposed = ProposedGmailSendEmailInput.model_validate(input_value)
    return GmailSendEmailInput(
        sender_mailbox=proposed.sender_mailbox,
        to=proposed.to,
        cc=proposed.cc,
        bcc=proposed.bcc,
        subject=JobOutputReference(
            job_output=job_ids[proposed.subject.job_output],
            field=proposed.subject.field,
        ),
        body=JobOutputReference(
            job_output=job_ids[proposed.body.job_output],
            field=proposed.body.field,
        ),
    )


def _renewal_completion_satisfied(view: WorkflowCompletionView) -> bool:
    if view.uncertain_job_ids:
        return False
    revised_ids = {job.revises_job_id for job in view.jobs if job.revises_job_id is not None}
    effective_sends = [
        job for job in view.jobs if job.kind == GMAIL_SEND_EMAIL_KIND and job.id not in revised_ids
    ]
    if len(effective_sends) != 1:
        return False
    effective_send = effective_sends[0]
    if (
        effective_send.status != "succeeded"
        or effective_send.id not in view.approved_dispatch_job_ids
    ):
        return False
    return all(job.status in {"succeeded", "cancelled"} for job in view.jobs)


def default_workflow_registry() -> WorkflowKindRegistry:
    """Build the V0 registry without exposing mutable global state."""

    draft = JobKindContract(
        kind=DRAFT_RENEWAL_EMAIL_KIND,
        input_schema=DraftRenewalEmailInput,
        proposal_input_schema=DraftRenewalEmailInput,
        output_schema=DraftRenewalEmailOutput,
        run_data_schema=DraftRenewalEmailOutput,
        materialize_input=_keep_validated_input,
        execution_strategy=ExecutionStrategy.FRESH_EXECUTION_AGENT,
        executor_key="renewal_email_drafter",
        max_attempts=2,
        success_event_type="draft_ready",
        success_notification_kind="approval_required",
        retryable_error_codes=frozenset({"executor_unavailable", "invalid_draft_output"}),
        retry_backoff=timedelta(seconds=2),
    )
    send = JobKindContract(
        kind=GMAIL_SEND_EMAIL_KIND,
        input_schema=GmailSendEmailInput,
        proposal_input_schema=ProposedGmailSendEmailInput,
        output_schema=GmailSendEmailOutput,
        run_data_schema=GmailSendEmailOutput,
        materialize_input=_materialize_gmail_input,
        execution_strategy=ExecutionStrategy.DETERMINISTIC_ADAPTER,
        executor_key="composio_gmail_send",
        max_attempts=3,
        success_event_type="email_send_succeeded",
        success_notification_kind="send_confirmed",
        retryable_error_codes=frozenset(),
        retry_backoff=timedelta(seconds=2),
        requires_approval=True,
        adapter_version="openmagic.composio_gmail.v1",
        provider_tool_version="GMAIL_SEND_EMAIL@20260702_01",
    )
    renewal = WorkflowKindContract(
        kind=RENEWAL_OUTREACH_KIND,
        input_schema=RenewalOutreachInput,
        allowed_job_kinds=frozenset({draft.kind, send.kind}),
        completion_predicate=_renewal_completion_satisfied,
    )
    return WorkflowKindRegistry((renewal,), (draft, send))
