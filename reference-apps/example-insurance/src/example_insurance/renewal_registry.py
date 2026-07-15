"""Renewal Command registrations and receipt decoders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import CommandRegistryBuilder
from psycopg import Connection

from example_insurance.renewal_commands import (
    AcceptRenewalEffectObservation,
    ApproveRenewalDraft,
    ApproveRenewalDraftResult,
    AuthorizeRenewalEmailDispatch,
    CancelRenewalOutreach,
    CancelRenewalOutreachResult,
    RequestRenewalRevision,
    RequestRenewalRevisionResult,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityResult,
    StartRenewalOutreach,
    StartRenewalOutreachResult,
    WorkflowAttemptResult,
    validate_approval,
    validate_cancellation,
    validate_dispatch,
    validate_effect_observation,
    validate_revision,
    validate_revocation,
    validate_start,
)
from example_insurance.renewal_effect_types import ExternalEffectPermit, permit_from_record


@dataclass(frozen=True)
class RenewalCommandHandlers:
    start: Callable[[StartRenewalOutreach, Connection[tuple[Any, ...]]], StartRenewalOutreachResult]
    approve: Callable[[ApproveRenewalDraft, Connection[tuple[Any, ...]]], ApproveRenewalDraftResult]
    revise: Callable[
        [RequestRenewalRevision, Connection[tuple[Any, ...]]], RequestRenewalRevisionResult
    ]
    revoke: Callable[
        [RevokeRenewalAuthority, Connection[tuple[Any, ...]]], RevokeRenewalAuthorityResult
    ]
    cancel: Callable[
        [CancelRenewalOutreach, Connection[tuple[Any, ...]]], CancelRenewalOutreachResult
    ]
    authorize_dispatch: Callable[
        [AuthorizeRenewalEmailDispatch, Connection[tuple[Any, ...]]], ExternalEffectPermit
    ]
    accept_effect_observation: Callable[
        [AcceptRenewalEffectObservation, Connection[tuple[Any, ...]]], WorkflowAttemptResult
    ]


def _decode_start(payload: dict[str, Any]) -> StartRenewalOutreachResult:
    return StartRenewalOutreachResult(
        workflow_id=UUID(payload["workflow_id"]),
        instance_id=UUID(payload["instance_id"]),
        thread_id=UUID(payload["thread_id"]),
    )


def _decode_approval(payload: dict[str, Any]) -> ApproveRenewalDraftResult:
    grant_id = payload["approval_grant_id"]
    effect_step_id = payload["effect_step_id"]
    return ApproveRenewalDraftResult(
        outcome=payload["outcome"],
        workflow_id=UUID(payload["workflow_id"]),
        wait_id=UUID(payload["wait_id"]),
        approval_grant_id=UUID(grant_id) if grant_id is not None else None,
        effect_step_id=UUID(effect_step_id) if effect_step_id is not None else None,
    )


def _decode_revision(payload: dict[str, Any]) -> RequestRenewalRevisionResult:
    revision_step_id = payload["revision_step_id"]
    return RequestRenewalRevisionResult(
        outcome=payload["outcome"],
        workflow_id=UUID(payload["workflow_id"]),
        wait_id=UUID(payload["wait_id"]),
        revision_step_id=UUID(revision_step_id) if revision_step_id is not None else None,
    )


def _decode_revocation(payload: dict[str, Any]) -> RevokeRenewalAuthorityResult:
    return RevokeRenewalAuthorityResult(
        outcome=payload["outcome"],
        workflow_id=UUID(payload["workflow_id"]),
    )


def _decode_cancellation(payload: dict[str, Any]) -> CancelRenewalOutreachResult:
    return CancelRenewalOutreachResult(
        outcome=payload["outcome"],
        workflow_id=UUID(payload["workflow_id"]),
        instance_id=UUID(payload["instance_id"]),
    )


def _decode_attempt_result(payload: dict[str, Any]) -> WorkflowAttemptResult:
    agent_run_id = payload["agent_run_id"]
    return WorkflowAttemptResult(
        attempt_id=UUID(payload["attempt_id"]),
        template_key=str(payload["template_key"]),
        executor_key=str(payload["executor_key"]),
        agent_run_id=UUID(agent_run_id) if agent_run_id is not None else None,
        agent_runtime_generation=payload["agent_runtime_generation"],
        steps={key: UUID(value) for key, value in payload["steps"].items()},
        waits={key: UUID(value) for key, value in payload["waits"].items()},
    )


def register_renewal_commands(
    builder: CommandRegistryBuilder, handlers: RenewalCommandHandlers
) -> CommandRegistryBuilder:
    return (
        builder.register(
            command_type="renewal.start_outreach",
            schema_version=1,
            command_class=StartRenewalOutreach,
            result_class=StartRenewalOutreachResult,
            handler=handlers.start,
            result_decoder=_decode_start,
            validator=validate_start,
        )
        .register(
            command_type="renewal.approve_draft",
            schema_version=1,
            command_class=ApproveRenewalDraft,
            result_class=ApproveRenewalDraftResult,
            handler=handlers.approve,
            result_decoder=_decode_approval,
            validator=validate_approval,
        )
        .register(
            command_type="renewal.request_revision",
            schema_version=1,
            command_class=RequestRenewalRevision,
            result_class=RequestRenewalRevisionResult,
            handler=handlers.revise,
            result_decoder=_decode_revision,
            validator=validate_revision,
        )
        .register(
            command_type="renewal.revoke_approval_authority",
            schema_version=1,
            command_class=RevokeRenewalAuthority,
            result_class=RevokeRenewalAuthorityResult,
            handler=handlers.revoke,
            result_decoder=_decode_revocation,
            validator=validate_revocation,
        )
        .register(
            command_type="renewal.cancel_outreach",
            schema_version=1,
            command_class=CancelRenewalOutreach,
            result_class=CancelRenewalOutreachResult,
            handler=handlers.cancel,
            result_decoder=_decode_cancellation,
            validator=validate_cancellation,
        )
        .register(
            command_type="renewal.authorize_email_dispatch",
            schema_version=1,
            command_class=AuthorizeRenewalEmailDispatch,
            result_class=ExternalEffectPermit,
            handler=handlers.authorize_dispatch,
            result_decoder=permit_from_record,
            validator=validate_dispatch,
        )
        .register(
            command_type="renewal.accept_effect_observation",
            schema_version=1,
            command_class=AcceptRenewalEffectObservation,
            result_class=WorkflowAttemptResult,
            handler=handlers.accept_effect_observation,
            result_decoder=_decode_attempt_result,
            validator=validate_effect_observation,
        )
    )


__all__ = [
    "RenewalCommandHandlers",
    "register_renewal_commands",
]
