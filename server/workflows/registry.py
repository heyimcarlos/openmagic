"""Closed, versioned Workflow and Job Kind contracts owned by OpenMagic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any
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


class ProposedGmailSendEmailInput(KindSchema):
    sender_mailbox: EmailStr
    to: tuple[EmailStr, ...]
    cc: tuple[EmailStr, ...] = ()
    bcc: tuple[EmailStr, ...] = ()
    subject: ProposedJobOutputReference
    body: ProposedJobOutputReference

    @field_validator("to")
    @classmethod
    def require_recipient(cls, value: tuple[EmailStr, ...]) -> tuple[EmailStr, ...]:
        if not value:
            raise ValueError("at least one recipient is required")
        return value


class GmailSendEmailInput(KindSchema):
    sender_mailbox: EmailStr
    to: tuple[EmailStr, ...]
    cc: tuple[EmailStr, ...] = ()
    bcc: tuple[EmailStr, ...] = ()
    subject: JobOutputReference
    body: JobOutputReference

    @field_validator("to")
    @classmethod
    def require_recipient(cls, value: tuple[EmailStr, ...]) -> tuple[EmailStr, ...]:
        if not value:
            raise ValueError("at least one recipient is required")
        return value


class GmailSendEmailOutput(KindSchema):
    provider: str
    accepted: bool


class ExecutionStrategy(StrEnum):
    FRESH_EXECUTION_AGENT = "fresh_execution_agent"
    DETERMINISTIC_ADAPTER = "deterministic_adapter"


@dataclass(frozen=True)
class JobKindContract:
    kind: str
    input_schema: type[KindSchema]
    output_schema: type[KindSchema]
    run_data_schema: type[KindSchema]
    execution_strategy: ExecutionStrategy
    max_attempts: int
    requires_approval: bool = False


@dataclass(frozen=True)
class WorkflowKindContract:
    kind: str
    input_schema: type[KindSchema]
    allowed_job_kinds: frozenset[str]


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
        self._workflow_kinds = MappingProxyType({kind.kind: kind for kind in workflow_kinds})
        self._job_kinds = MappingProxyType({kind.kind: kind for kind in job_kinds})

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
                schema = (
                    ProposedGmailSendEmailInput
                    if job.kind == GMAIL_SEND_EMAIL_KIND
                    else contract.input_schema
                )
                proposed_input = schema.model_validate(job.input)
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
            self._validate_renewal_graph(tuple(validated_jobs))

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
        if job.kind != GMAIL_SEND_EMAIL_KIND:
            return job.proposed_input.model_dump(mode="json")

        proposed = ProposedGmailSendEmailInput.model_validate(job.proposed_input)
        materialized = GmailSendEmailInput(
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
        return materialized.model_dump(mode="json")

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
    def _validate_renewal_graph(jobs: tuple[ValidatedJobProposal, ...]) -> None:
        drafts = [job for job in jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND]
        sends = [job for job in jobs if job.kind == GMAIL_SEND_EMAIL_KIND]
        if len(jobs) != 2 or len(drafts) != 1 or len(sends) != 1:
            raise InvalidWorkflowProposalError(
                "renewal_outreach.v1 requires one Draft Job and one Send Job"
            )
        draft, send = drafts[0], sends[0]
        if draft.depends_on or send.depends_on != (draft.key,):
            raise InvalidWorkflowProposalError("the Send Job must depend only on the Draft Job")
        send_input = ProposedGmailSendEmailInput.model_validate(send.proposed_input)
        if send_input.subject.job_output != draft.key or send_input.subject.field != "subject":
            raise InvalidWorkflowProposalError("Send subject must reference the Draft subject")
        if send_input.body.job_output != draft.key or send_input.body.field != "body":
            raise InvalidWorkflowProposalError("Send body must reference the Draft body")


def default_workflow_registry() -> WorkflowKindRegistry:
    """Build the V0 registry without exposing mutable global state."""

    draft = JobKindContract(
        kind=DRAFT_RENEWAL_EMAIL_KIND,
        input_schema=DraftRenewalEmailInput,
        output_schema=DraftRenewalEmailOutput,
        run_data_schema=DraftRenewalEmailOutput,
        execution_strategy=ExecutionStrategy.FRESH_EXECUTION_AGENT,
        max_attempts=2,
    )
    send = JobKindContract(
        kind=GMAIL_SEND_EMAIL_KIND,
        input_schema=GmailSendEmailInput,
        output_schema=GmailSendEmailOutput,
        run_data_schema=GmailSendEmailOutput,
        execution_strategy=ExecutionStrategy.DETERMINISTIC_ADAPTER,
        max_attempts=1,
        requires_approval=True,
    )
    renewal = WorkflowKindContract(
        kind=RENEWAL_OUTREACH_KIND,
        input_schema=RenewalOutreachInput,
        allowed_job_kinds=frozenset({draft.kind, send.kind}),
    )
    return WorkflowKindRegistry((renewal,), (draft, send))
