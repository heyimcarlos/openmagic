"""Registry-backed creation of new Workflows from authorized source context."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from .authority import CurrentBrokerAuthority
from .contracts import ProposeWorkflowCommand, WorkflowProposal
from .database import WorkflowDatabase
from .errors import WorkflowAuthorizationError, WorkflowLifecycleError
from .identity_models import (
    PartyIdentifierRow,
    PartyRow,
    WorkflowParticipantRoleRow,
    WorkflowParticipantRow,
)
from .interaction_cause_protocol import WorkflowInteractionCauseProtocol
from .models import WorkflowRow
from .proposal_protocol import WorkflowProposalProtocol
from .registry import WorkflowKindRegistry


class WorkflowCreationProtocol:
    """Create one new aggregate without exposing its Job graph to the model."""

    def __init__(
        self,
        database: WorkflowDatabase,
        registry: WorkflowKindRegistry,
        proposals: WorkflowProposalProtocol,
        causes: WorkflowInteractionCauseProtocol,
        has_current_broker_authority: CurrentBrokerAuthority,
    ) -> None:
        self._database = database
        self._registry = registry
        self._proposals = proposals
        self._causes = causes
        self._has_current_broker_authority = has_current_broker_authority

    async def propose(self, command: ProposeWorkflowCommand):
        command_digest = self._command_digest(command)
        async with self._database.transaction() as session:
            source = await session.scalar(
                sa.select(WorkflowRow)
                .where(WorkflowRow.id == command.source_workflow_id)
                .with_for_update()
            )
            if source is None:
                raise WorkflowLifecycleError("Source Workflow does not exist")
            context = command.context
            if context.organization_party_id != source.organization_party_id:
                raise WorkflowAuthorizationError(
                    "Source Workflow is outside the authenticated Organization"
                )
            if command.workflow_kind != source.kind:
                raise WorkflowAuthorizationError(
                    "Source Workflow does not authorize the requested Workflow Kind"
                )
            if not await self._has_current_broker_authority(session, context, source):
                raise WorkflowAuthorizationError("Party cannot create work from this Workflow")
            if command.corrects_workflow_id is not None:
                if command.corrects_workflow_id != source.id:
                    raise WorkflowAuthorizationError(
                        "Correction target must be the selected source Workflow"
                    )
                if source.status != "completed":
                    raise WorkflowLifecycleError("Correction target must be a completed Workflow")
            await self._causes.require(session, context)

            existing = await self._proposals.event_for_cause(session, context)
            if existing is not None:
                if (
                    existing.actor_id != str(context.actor_party_id)
                    or existing.data.get("source_workflow_id") != str(command.source_workflow_id)
                    or existing.data.get("command_digest") != command_digest
                ):
                    raise WorkflowLifecycleError("Workflow proposal Cause was already used")
                workflow = await session.get(WorkflowRow, existing.workflow_id)
                if workflow is None:
                    raise WorkflowLifecycleError("Workflow proposal receipt is incomplete")
                return await self._proposals.read_receipt(session, workflow, existing)

            roles = (
                await session.execute(
                    sa.select(
                        WorkflowParticipantRoleRow.party_id,
                        WorkflowParticipantRoleRow.role,
                    ).where(
                        WorkflowParticipantRoleRow.workflow_id == source.id,
                        WorkflowParticipantRoleRow.party_id != context.actor_party_id,
                        WorkflowParticipantRoleRow.role != "Broker",
                        WorkflowParticipantRoleRow.revoked_at.is_(None),
                    )
                )
            ).all()
            workflow_input = command.input.model_dump(mode="json")
            facts = await self._renewal_facts(
                session,
                context.actor_party_id,
                roles,
                workflow_input,
            )
            jobs = self._registry.compile_work(
                command.workflow_kind,
                command.operation,
                facts,
            )
            validated = self._registry.validate(
                WorkflowProposal(
                    kind=command.workflow_kind,
                    objective=command.objective,
                    input=workflow_input,
                    jobs=jobs,
                )
            )
            proposal_digest = self._proposals.workflow_digest(validated)
            workflow = WorkflowRow(
                id=uuid4(),
                kind=validated.kind,
                objective=validated.objective,
                status="active",
                input=validated.input,
                organization_party_id=source.organization_party_id,
                corrects_workflow_id=command.corrects_workflow_id,
            )
            session.add(workflow)
            await session.flush()
            await session.execute(
                sa.select(WorkflowRow.id).where(WorkflowRow.id == workflow.id).with_for_update()
            )

            participant_ids = {context.actor_party_id}
            participant_ids.update(party_id for party_id, _role in roles)
            session.add_all(
                WorkflowParticipantRow(workflow_id=workflow.id, party_id=party_id)
                for party_id in participant_ids
            )
            await session.flush()
            now = datetime.now(UTC)
            session.add(
                WorkflowParticipantRoleRow(
                    workflow_id=workflow.id,
                    party_id=context.actor_party_id,
                    role="Broker",
                    granted_at=now,
                )
            )
            session.add_all(
                WorkflowParticipantRoleRow(
                    workflow_id=workflow.id,
                    party_id=party_id,
                    role=role,
                    granted_at=now,
                )
                for party_id, role in roles
            )
            event = await self._proposals.append_graph(
                session,
                workflow,
                validated,
                context,
                proposal_digest=proposal_digest,
            )
            event.data = {
                **event.data,
                "source_workflow_id": str(source.id),
                "command_digest": command_digest,
            }
            await session.flush()
            return await self._proposals.read_receipt(session, workflow, event)

    @staticmethod
    async def _renewal_facts(session, actor_party_id: UUID, roles, workflow_input):
        policyholder_ids = [party_id for party_id, role in roles if role == "Policyholder"]
        if len(policyholder_ids) != 1:
            raise WorkflowLifecycleError("Source Workflow must have one current Policyholder")
        policyholder_id = policyholder_ids[0]
        policyholder_name = await session.scalar(
            sa.select(PartyRow.display_name).where(PartyRow.id == policyholder_id)
        )
        identifiers = (
            await session.execute(
                sa.select(PartyIdentifierRow.party_id, PartyIdentifierRow.value).where(
                    PartyIdentifierRow.party_id.in_((actor_party_id, policyholder_id)),
                    PartyIdentifierRow.kind == "email",
                    PartyIdentifierRow.verified_at.is_not(None),
                    PartyIdentifierRow.revoked_at.is_(None),
                )
            )
        ).all()
        emails: dict[UUID, list[str]] = defaultdict(list)
        for party_id, value in identifiers:
            emails[party_id].append(value)
        if (
            policyholder_name is None
            or len(emails[actor_party_id]) != 1
            or len(emails[policyholder_id]) != 1
        ):
            raise WorkflowLifecycleError(
                "Workflow creation requires one verified Broker and Policyholder mailbox"
            )
        renewal_period = workflow_input.get("renewal_period")
        return {
            "recipient_name": policyholder_name,
            "renewal_period": renewal_period,
            "sender_mailbox": emails[actor_party_id][0],
            "recipient_email": emails[policyholder_id][0],
        }

    @staticmethod
    def _command_digest(command: ProposeWorkflowCommand) -> str:
        value = {
            "source_workflow_id": str(command.source_workflow_id),
            "corrects_workflow_id": (
                str(command.corrects_workflow_id)
                if command.corrects_workflow_id is not None
                else None
            ),
            "workflow_kind": command.workflow_kind,
            "objective": command.objective,
            "input": command.input.model_dump(mode="json"),
            "operation": command.operation.model_dump(mode="json"),
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["WorkflowCreationProtocol"]
