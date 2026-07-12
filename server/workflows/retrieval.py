"""Authorization-first Workflow search and bounded packet retrieval."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import aliased

from .database import WorkflowDatabase
from .errors import (
    InvalidWorkflowSearchError,
    StaleWorkflowCursorError,
    WorkflowNotFoundError,
)
from .identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
    WorkflowParticipantRoleRow,
    WorkflowParticipantRow,
)
from .models import (
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .retrieval_contracts import (
    ParticipantRole,
    RunOutcome,
    WorkflowFacet,
    WorkflowFacetEntry,
    WorkflowInspectionContext,
    WorkflowJobStatus,
    WorkflowPacket,
    WorkflowPacketApproval,
    WorkflowPacketDispatch,
    WorkflowPacketEvent,
    WorkflowPacketEventWindow,
    WorkflowPacketJob,
    WorkflowPacketRun,
    WorkflowPacketWaitingReason,
    WorkflowPacketWorkflow,
    WorkflowParticipantSummary,
    WorkflowRunStatus,
    WorkflowSearchFacets,
    WorkflowSearchPage,
    WorkflowSearchParticipantSummary,
    WorkflowSearchRequest,
    WorkflowSearchResult,
    WorkflowStatus,
)

RENEWAL_OUTREACH_KIND = "renewal_outreach.v1"
CURSOR_VERSION = 1
FACET_LIMIT = 10
EVENT_LIMIT = 20
HIDDEN_EVENT_TYPES = frozenset({"lease_extended", "run_claimed", "run_started"})
QUERY_NOISE_TERMS = frozenset(
    {
        "a",
        "an",
        "at",
        "can",
        "could",
        "create",
        "draft",
        "email",
        "find",
        "for",
        "help",
        "i",
        "is",
        "it",
        "like",
        "me",
        "my",
        "need",
        "of",
        "our",
        "please",
        "prepare",
        "send",
        "that",
        "the",
        "this",
        "to",
        "want",
        "would",
        "workflow",
        "you",
    }
)


@dataclass(frozen=True)
class _Candidate:
    workflow: WorkflowRow
    organization: str
    participants: tuple[WorkflowParticipantSummary, ...]
    score: int
    match_reasons: tuple[str, ...]


@dataclass(frozen=True)
class _WorkflowIdentifiers:
    participants: tuple[str, ...] = ()
    organization: tuple[str, ...] = ()


class WorkflowRetrieval:
    """Hide authorization, ranking, facets, cursors, and packet projection."""

    def __init__(self, *, database: WorkflowDatabase, cursor_secret: bytes) -> None:
        if not cursor_secret:
            raise ValueError("cursor_secret must not be empty")
        self._database = database
        self._cursor_secret = cursor_secret

    async def search_workflows(
        self,
        context: WorkflowInspectionContext,
        request: WorkflowSearchRequest,
    ) -> WorkflowSearchPage:
        self._validate_filters(request)
        async with self._database.read_transaction() as session:
            rows = (
                await session.execute(self._search_workflows_query(context.actor_party_id, request))
            ).all()
            workflow_ids = [workflow.id for workflow, _organization, _score in rows]
            participants = await self._participants(session, workflow_ids)
            identifiers = await self._identifiers(session, workflow_ids)

        matches = [
            self._candidate(
                workflow,
                organization,
                participants[workflow.id],
                identifiers[workflow.id],
                request,
                score,
            )
            for workflow, organization, score in rows
        ]

        request_digest = self._request_digest(request)
        start = self._cursor_start(matches, request.cursor, request_digest)
        window = matches[start : start + request.limit + 1]
        has_more = len(window) > request.limit
        page_candidates = window[: request.limit]
        next_cursor = None
        if has_more and page_candidates:
            next_cursor = self._encode_cursor(page_candidates[-1], request_digest)

        return WorkflowSearchPage(
            results=tuple(self._result(candidate) for candidate in page_candidates),
            total_matches=len(matches),
            has_more=has_more,
            next_cursor=next_cursor,
            applied_filters=self._applied_filters(request),
            facets=self._facets(matches),
            generated_at=datetime.now(UTC),
        )

    async def read_workflow_packet(
        self,
        context: WorkflowInspectionContext,
        workflow_id: UUID,
    ) -> WorkflowPacket:
        async with self._database.read_transaction() as session:
            authorized = self._authorized_workflows(context.actor_party_id).where(
                WorkflowRow.id == workflow_id
            )
            row = (await session.execute(authorized)).one_or_none()
            if row is None:
                raise WorkflowNotFoundError(str(workflow_id))
            workflow, organization = row
            participants = (await self._participants(session, [workflow_id]))[workflow_id]
            jobs = (
                await session.scalars(
                    sa.select(WorkflowJobRow)
                    .where(WorkflowJobRow.workflow_id == workflow_id)
                    .order_by(WorkflowJobRow.created_at, WorkflowJobRow.id)
                )
            ).all()
            dependencies = (
                await session.scalars(
                    sa.select(WorkflowJobDependencyRow).where(
                        WorkflowJobDependencyRow.workflow_id == workflow_id
                    )
                )
            ).all()
            runs = (
                await session.scalars(
                    sa.select(WorkflowJobRunRow)
                    .where(WorkflowJobRunRow.workflow_id == workflow_id)
                    .order_by(WorkflowJobRunRow.created_at, WorkflowJobRunRow.id)
                )
            ).all()
            events = (
                await session.scalars(
                    sa.select(WorkflowEventRow)
                    .where(
                        WorkflowEventRow.workflow_id == workflow_id,
                        WorkflowEventRow.event_type.not_in(HIDDEN_EVENT_TYPES),
                    )
                    .order_by(WorkflowEventRow.occurred_at, WorkflowEventRow.id)
                )
            ).all()

        dependencies_by_job: dict[UUID, list[UUID]] = defaultdict(list)
        for dependency in dependencies:
            dependencies_by_job[dependency.job_id].append(dependency.depends_on_job_id)
        runs_by_job: dict[UUID, list[WorkflowJobRunRow]] = defaultdict(list)
        for run in runs:
            runs_by_job[run.job_id].append(run)
        jobs_by_id = {job.id: job for job in jobs}
        events_by_job: dict[UUID, list[WorkflowEventRow]] = defaultdict(list)
        for event in events:
            if event.job_id is not None:
                events_by_job[event.job_id].append(event)

        event_window = events[-EVENT_LIMIT:]
        return WorkflowPacket(
            generated_at=datetime.now(UTC),
            workflow=WorkflowPacketWorkflow(
                workflow_id=workflow.id,
                workflow_kind=workflow.kind,
                objective=workflow.objective,
                status=workflow.status,
                input=workflow.input,
                organization=organization,
                corrects_workflow_id=workflow.corrects_workflow_id,
                created_at=workflow.created_at,
            ),
            participants=participants,
            jobs=tuple(
                self._packet_job(
                    job,
                    dependencies_by_job[job.id],
                    runs_by_job[job.id],
                    events_by_job[job.id],
                    jobs_by_id,
                )
                for job in jobs
            ),
            recent_events=tuple(self._packet_event(event) for event in event_window),
            event_window=WorkflowPacketEventWindow(
                returned=len(event_window),
                total=len(events),
                has_earlier=len(events) > EVENT_LIMIT,
            ),
        )

    @staticmethod
    def _authorized_workflows(actor_party_id: UUID) -> sa.Select[Any]:
        verified_identifier = sa.exists(
            sa.select(PartyIdentifierRow.id).where(
                PartyIdentifierRow.party_id == actor_party_id,
                PartyIdentifierRow.verified_at.is_not(None),
                PartyIdentifierRow.revoked_at.is_(None),
            )
        )
        return (
            sa.select(WorkflowRow, PartyRow.display_name)
            .join(PartyRow, PartyRow.id == WorkflowRow.organization_party_id)
            .join(
                OrganizationMembershipRow,
                sa.and_(
                    OrganizationMembershipRow.person_party_id == actor_party_id,
                    OrganizationMembershipRow.organization_party_id
                    == WorkflowRow.organization_party_id,
                    OrganizationMembershipRow.revoked_at.is_(None),
                ),
            )
            .join(
                WorkflowParticipantRoleRow,
                sa.and_(
                    WorkflowParticipantRoleRow.workflow_id == WorkflowRow.id,
                    WorkflowParticipantRoleRow.party_id == actor_party_id,
                    WorkflowParticipantRoleRow.role == "Broker",
                    WorkflowParticipantRoleRow.revoked_at.is_(None),
                ),
            )
            .where(verified_identifier)
        )

    @classmethod
    def _search_workflows_query(
        cls,
        actor_party_id: UUID,
        request: WorkflowSearchRequest,
    ) -> sa.Select[Any]:
        participant = aliased(WorkflowParticipantRow)
        participant_party = aliased(PartyRow)
        participant_identifier = aliased(PartyIdentifierRow)
        organization_identifier = aliased(PartyIdentifierRow)

        def participant_matches(value: str) -> sa.ColumnElement[bool]:
            normalized = value.casefold()
            return sa.exists(
                sa.select(participant.workflow_id)
                .select_from(participant)
                .join(participant_party, participant_party.id == participant.party_id)
                .outerjoin(
                    participant_identifier,
                    sa.and_(
                        participant_identifier.party_id == participant.party_id,
                        participant_identifier.verified_at.is_not(None),
                        participant_identifier.revoked_at.is_(None),
                    ),
                )
                .where(
                    participant.workflow_id == WorkflowRow.id,
                    sa.or_(
                        sa.func.lower(participant_party.display_name).contains(
                            normalized, autoescape=True
                        ),
                        sa.func.lower(participant_identifier.value).contains(
                            normalized, autoescape=True
                        ),
                    ),
                )
            )

        def participant_equals(value: str) -> sa.ColumnElement[bool]:
            normalized = value.casefold()
            return sa.exists(
                sa.select(participant.workflow_id)
                .select_from(participant)
                .join(participant_party, participant_party.id == participant.party_id)
                .outerjoin(
                    participant_identifier,
                    sa.and_(
                        participant_identifier.party_id == participant.party_id,
                        participant_identifier.verified_at.is_not(None),
                        participant_identifier.revoked_at.is_(None),
                    ),
                )
                .where(
                    participant.workflow_id == WorkflowRow.id,
                    sa.or_(
                        sa.func.lower(participant_party.display_name) == normalized,
                        sa.func.lower(participant_identifier.value) == normalized,
                    ),
                )
            )

        def organization_identifier_matches(value: str) -> sa.ColumnElement[bool]:
            return sa.exists(
                sa.select(organization_identifier.id).where(
                    organization_identifier.party_id == WorkflowRow.organization_party_id,
                    organization_identifier.verified_at.is_not(None),
                    organization_identifier.revoked_at.is_(None),
                    sa.func.lower(organization_identifier.value).contains(
                        value.casefold(), autoescape=True
                    ),
                )
            )

        def organization_identifier_equals(value: str) -> sa.ColumnElement[bool]:
            return sa.exists(
                sa.select(organization_identifier.id).where(
                    organization_identifier.party_id == WorkflowRow.organization_party_id,
                    organization_identifier.verified_at.is_not(None),
                    organization_identifier.revoked_at.is_(None),
                    sa.func.lower(organization_identifier.value) == value.casefold(),
                )
            )

        query = cls._authorized_workflows(actor_party_id)
        if request.workflow_kind:
            query = query.where(WorkflowRow.kind == request.workflow_kind)
        if request.status:
            query = query.where(WorkflowRow.status == request.status)
        if request.renewal_period:
            query = query.where(
                WorkflowRow.kind == RENEWAL_OUTREACH_KIND,
                WorkflowRow.input["renewal_period"].as_string() == request.renewal_period,
            )
        if request.participant:
            query = query.where(participant_matches(request.participant))
        if request.organization:
            query = query.where(
                sa.or_(
                    sa.func.lower(PartyRow.display_name).contains(
                        request.organization.casefold(), autoescape=True
                    ),
                    organization_identifier_matches(request.organization),
                )
            )

        normalized_query = cls._normalize_text(request.query)
        query_terms = cls._query_terms(request.query)
        normalized_kind = sa.func.replace(
            sa.func.replace(sa.func.lower(WorkflowRow.kind), "_", " "),
            ".v1",
            "",
        )
        period = WorkflowRow.input["renewal_period"].as_string()
        for term in query_terms:
            query = query.where(
                sa.or_(
                    sa.cast(WorkflowRow.id, sa.Text).contains(term, autoescape=True),
                    sa.func.lower(WorkflowRow.objective).contains(term, autoescape=True),
                    normalized_kind.contains(term, autoescape=True),
                    sa.func.lower(WorkflowRow.status).contains(term, autoescape=True),
                    sa.func.lower(PartyRow.display_name).contains(term, autoescape=True),
                    period.contains(term, autoescape=True),
                    participant_matches(term),
                    organization_identifier_matches(term),
                )
            )

        participant_exact_value = request.participant or normalized_query
        organization_exact_value = request.organization or normalized_query
        participant_exact = (
            participant_equals(participant_exact_value) if participant_exact_value else sa.false()
        )
        organization_exact = (
            sa.or_(
                sa.func.lower(PartyRow.display_name) == organization_exact_value,
                organization_identifier_equals(organization_exact_value),
            )
            if organization_exact_value
            else sa.false()
        )
        text_score: sa.ColumnElement[int] = sa.literal(0)
        for term in query_terms:
            text_score = (
                text_score
                + sa.case(
                    (
                        sa.func.lower(WorkflowRow.objective).contains(term, autoescape=True),
                        8,
                    ),
                    else_=0,
                )
                + sa.case((participant_matches(term), 6), else_=0)
                + sa.case(
                    (
                        sa.or_(
                            sa.func.lower(PartyRow.display_name).contains(term, autoescape=True),
                            organization_identifier_matches(term),
                        ),
                        5,
                    ),
                    else_=0,
                )
                + sa.case((normalized_kind.contains(term, autoescape=True), 4), else_=0)
                + sa.case((period.contains(term, autoescape=True), 3), else_=0)
            )
        score = (
            text_score
            + sa.case((sa.cast(WorkflowRow.id, sa.Text) == normalized_query, 1000), else_=0)
            + sa.case((participant_exact, 200), else_=0)
            + sa.case((organization_exact, 180), else_=0)
            + sa.case(
                (
                    WorkflowRow.kind == request.workflow_kind
                    if request.workflow_kind
                    else normalized_kind == normalized_query,
                    100,
                ),
                else_=0,
            )
            + sa.case(
                (
                    sa.and_(
                        sa.literal(bool(normalized_query)),
                        sa.func.lower(WorkflowRow.objective).like(f"%{normalized_query}%"),
                    ),
                    80,
                ),
                else_=0,
            )
            + sa.literal(40 if request.status else 0)
            + sa.literal(40 if request.renewal_period else 0)
        ).label("search_score")
        return query.add_columns(score).order_by(
            sa.desc(score),
            sa.desc(WorkflowRow.created_at),
            WorkflowRow.id,
        )

    @staticmethod
    async def _participants(
        session: Any,
        workflow_ids: list[UUID],
    ) -> dict[UUID, tuple[WorkflowParticipantSummary, ...]]:
        grouped: dict[UUID, dict[UUID, tuple[str, list[str]]]] = defaultdict(dict)
        if workflow_ids:
            rows = (
                await session.execute(
                    sa.select(
                        WorkflowParticipantRow.workflow_id,
                        PartyRow.id,
                        PartyRow.display_name,
                        WorkflowParticipantRoleRow.role,
                    )
                    .join(PartyRow, PartyRow.id == WorkflowParticipantRow.party_id)
                    .outerjoin(
                        WorkflowParticipantRoleRow,
                        sa.and_(
                            WorkflowParticipantRoleRow.workflow_id
                            == WorkflowParticipantRow.workflow_id,
                            WorkflowParticipantRoleRow.party_id == WorkflowParticipantRow.party_id,
                            WorkflowParticipantRoleRow.revoked_at.is_(None),
                        ),
                    )
                    .where(
                        WorkflowParticipantRow.workflow_id.in_(workflow_ids),
                    )
                    .order_by(PartyRow.display_name, PartyRow.id, WorkflowParticipantRoleRow.role)
                )
            ).all()
            for workflow_id, party_id, name, role in rows:
                current = grouped[workflow_id].setdefault(party_id, (name, []))
                if role is not None:
                    current[1].append(role)
        return {
            workflow_id: tuple(
                WorkflowParticipantSummary(
                    party_id=party_id,
                    name=name,
                    roles=cast(tuple[ParticipantRole, ...], tuple(roles)),
                )
                for party_id, (name, roles) in parties.items()
            )
            for workflow_id, parties in grouped.items()
        } | {workflow_id: () for workflow_id in workflow_ids if workflow_id not in grouped}

    @staticmethod
    async def _identifiers(
        session: Any,
        workflow_ids: list[UUID],
    ) -> dict[UUID, _WorkflowIdentifiers]:
        participant_values: dict[UUID, list[str]] = defaultdict(list)
        organization_values: dict[UUID, list[str]] = defaultdict(list)
        if workflow_ids:
            participant_rows = (
                await session.execute(
                    sa.select(WorkflowParticipantRow.workflow_id, PartyIdentifierRow.value)
                    .join(
                        PartyIdentifierRow,
                        PartyIdentifierRow.party_id == WorkflowParticipantRow.party_id,
                    )
                    .where(
                        WorkflowParticipantRow.workflow_id.in_(workflow_ids),
                        PartyIdentifierRow.verified_at.is_not(None),
                        PartyIdentifierRow.revoked_at.is_(None),
                    )
                )
            ).all()
            organization_rows = (
                await session.execute(
                    sa.select(WorkflowRow.id, PartyIdentifierRow.value)
                    .join(
                        PartyIdentifierRow,
                        PartyIdentifierRow.party_id == WorkflowRow.organization_party_id,
                    )
                    .where(
                        WorkflowRow.id.in_(workflow_ids),
                        PartyIdentifierRow.verified_at.is_not(None),
                        PartyIdentifierRow.revoked_at.is_(None),
                    )
                )
            ).all()
            for workflow_id, value in participant_rows:
                participant_values[workflow_id].append(value)
            for workflow_id, value in organization_rows:
                organization_values[workflow_id].append(value)
        return {
            workflow_id: _WorkflowIdentifiers(
                participants=tuple(participant_values[workflow_id]),
                organization=tuple(organization_values[workflow_id]),
            )
            for workflow_id in workflow_ids
        }

    def _candidate(
        self,
        workflow: WorkflowRow,
        organization: str,
        participants: tuple[WorkflowParticipantSummary, ...],
        identifiers: _WorkflowIdentifiers,
        request: WorkflowSearchRequest,
        persisted_score: int,
    ) -> _Candidate:
        period = workflow.input.get("renewal_period")
        participant_names = [participant.name for participant in participants]
        query = self._normalize_text(request.query)
        query_terms = self._query_terms(request.query)

        score = int(persisted_score)
        reasons: list[str] = []
        if query == str(workflow.id).casefold():
            reasons.append(f"exact workflow identifier: {workflow.id}")
        for name in participant_names:
            if name.casefold() in query or (
                request.participant and request.participant.casefold() == name.casefold()
            ):
                reasons.append(f"exact participant match: {name}")
                break
        if request.participant and any(
            request.participant.casefold() == value.casefold() for value in identifiers.participants
        ):
            reasons.append("exact participant identifier match")
        elif request.participant and not any(
            reason.startswith("exact participant") for reason in reasons
        ):
            reasons.append("participant filter matched")
        if organization.casefold() in query or (
            request.organization and request.organization.casefold() == organization.casefold()
        ):
            reasons.append(f"organization matched: {organization}")
        elif request.organization and any(
            request.organization.casefold() == value.casefold()
            for value in identifiers.organization
        ):
            reasons.append("exact organization identifier match")
        elif request.organization:
            reasons.append(f"organization filter matched: {organization}")
        kind_words = workflow.kind.replace("_", " ").split(".", 1)[0].casefold()
        if kind_words in query or request.workflow_kind == workflow.kind:
            reasons.append(f"workflow kind matched: {workflow.kind}")
        if query and query in workflow.objective.casefold():
            reasons.append("objective matched query")
        if request.status == workflow.status:
            reasons.append(f"status matched: {workflow.status}")
        if request.renewal_period == period:
            reasons.append(f"renewal period matched: {period}")
        if not reasons and query_terms:
            reasons.append("text terms matched authorized Workflow fields")
        return _Candidate(workflow, organization, participants, score, tuple(reasons))

    @staticmethod
    def _result(candidate: _Candidate) -> WorkflowSearchResult:
        workflow = candidate.workflow
        return WorkflowSearchResult(
            workflow_id=workflow.id,
            objective=workflow.objective,
            workflow_kind=workflow.kind,
            status=cast(WorkflowStatus, workflow.status),
            organization=candidate.organization,
            participants=tuple(
                WorkflowSearchParticipantSummary(name=participant.name, roles=participant.roles)
                for participant in candidate.participants
            ),
            renewal_period=workflow.input.get("renewal_period"),
            created_at=workflow.created_at,
            match_reasons=candidate.match_reasons,
        )

    def _packet_job(
        self,
        job: WorkflowJobRow,
        dependency_ids: list[UUID],
        runs: list[WorkflowJobRunRow],
        events: list[WorkflowEventRow],
        jobs_by_id: dict[UUID, WorkflowJobRow],
    ) -> WorkflowPacketJob:
        latest_run = runs[-1] if runs else None
        approval = next(
            (event for event in reversed(events) if event.event_type == "approval_granted"),
            None,
        )
        dispatch = next(
            (event for event in events if event.event_type == "external_effect_dispatch_started"),
            None,
        )
        invalidated = approval is not None and any(
            event.event_type == "approval_invalidated" and event.approval_grant_id == approval.id
            for event in events
        )
        unresolved = [
            dependency_id
            for dependency_id in dependency_ids
            if jobs_by_id[dependency_id].status != "succeeded"
        ]
        waiting_reasons: list[WorkflowPacketWaitingReason] = []
        if job.status == "waiting":
            waiting_reasons.extend(
                WorkflowPacketWaitingReason(
                    kind="dependency",
                    dependency_job_id=dependency_id,
                )
                for dependency_id in unresolved
            )
            if not unresolved and job.kind == "gmail.send_email.v1":
                if approval is None:
                    waiting_reasons.append(WorkflowPacketWaitingReason(kind="exact_approval"))
                elif invalidated:
                    waiting_reasons.append(WorkflowPacketWaitingReason(kind="approval_invalidated"))
            if (
                latest_run is not None
                and latest_run.result
                and latest_run.result.get("outcome") == "uncertain"
            ):
                waiting_reasons.append(
                    WorkflowPacketWaitingReason(kind="uncertain_external_effect")
                )

        return WorkflowPacketJob(
            job_id=job.id,
            kind=job.kind,
            status=cast(WorkflowJobStatus, job.status),
            input=job.input,
            resolved_input=self._resolved_input(job, jobs_by_id),
            output=job.output,
            revises_job_id=job.revises_job_id,
            depends_on_job_ids=tuple(dependency_ids),
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            available_at=job.available_at,
            waiting_reasons=tuple(waiting_reasons),
            latest_run=self._packet_run(latest_run),
            approval=self._packet_approval(approval, invalidated, dispatch is not None),
            dispatch=(
                WorkflowPacketDispatch(
                    started_at=dispatch.occurred_at,
                    run_id=dispatch.run_id,
                    evidence=(
                        "outcome_uncertain"
                        if latest_run is not None
                        and latest_run.result
                        and latest_run.result.get("outcome") == "uncertain"
                        else "provider_confirmed"
                        if job.status == "succeeded"
                        else "dispatch_started"
                    ),
                )
                if dispatch is not None
                else None
            ),
        )

    @staticmethod
    def _resolved_input(
        job: WorkflowJobRow,
        jobs_by_id: dict[UUID, WorkflowJobRow],
    ) -> dict[str, Any] | None:
        resolved = dict(job.input)
        found_reference = False
        for field, value in job.input.items():
            if not isinstance(value, dict) or set(value) != {"job_output", "field"}:
                continue
            found_reference = True
            try:
                source_id = UUID(str(value["job_output"]))
                source = jobs_by_id[source_id]
                if source.output is None or value["field"] not in source.output:
                    return None
                resolved[field] = source.output[value["field"]]
            except (KeyError, TypeError, ValueError):
                return None
        return resolved if found_reference else dict(job.input)

    @staticmethod
    def _packet_run(run: WorkflowJobRunRow | None) -> WorkflowPacketRun | None:
        if run is None:
            return None
        result = run.result or {}
        outcome = result.get("outcome")
        error_summary = None
        if outcome == "uncertain":
            error_summary = "External effect outcome is uncertain"
        elif result.get("error") is not None:
            error_summary = "Run failed"
        return WorkflowPacketRun(
            run_id=run.id,
            status=cast(WorkflowRunStatus, run.status),
            outcome=cast(RunOutcome | None, outcome),
            error_summary=error_summary,
            started_at=run.created_at,
            finished_at=run.finished_at,
        )

    @staticmethod
    def _packet_approval(
        approval: WorkflowEventRow | None,
        invalidated: bool,
        consumed: bool,
    ) -> WorkflowPacketApproval | None:
        if approval is None:
            return None
        try:
            approving_party_id = UUID(approval.actor_id)
            draft_value = approval.data.get("draft_revision_id")
            draft_job_id = UUID(str(draft_value)) if draft_value else None
        except ValueError:
            return None
        outcome = "consumed" if consumed else "invalidated" if invalidated else "usable"
        return WorkflowPacketApproval(
            approval_grant_id=approval.id,
            approving_party_id=approving_party_id,
            draft_job_id=draft_job_id,
            cause_type=approval.cause_type,
            cause_id=approval.cause_id,
            granted_at=approval.occurred_at,
            outcome=outcome,
        )

    @staticmethod
    def _packet_event(event: WorkflowEventRow) -> WorkflowPacketEvent:
        summaries = {
            "workflow_created": "Workflow created",
            "workflow_jobs_proposed": "Workflow work proposed",
            "draft_ready": "Draft is ready for review",
            "approval_granted": "Exact effect approved",
            "external_effect_dispatch_started": "External effect dispatch started",
            "email_send_succeeded": "Email send succeeded",
            "workflow_completed": "Workflow objective completed",
        }
        return WorkflowPacketEvent(
            event_id=event.id,
            job_id=event.job_id,
            run_id=event.run_id,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            cause_type=event.cause_type,
            cause_id=event.cause_id,
            occurred_at=event.occurred_at,
            summary=summaries.get(event.event_type, event.event_type.replace("_", " "))[:200],
        )

    @staticmethod
    def _validate_filters(request: WorkflowSearchRequest) -> None:
        if request.workflow_kind not in {None, RENEWAL_OUTREACH_KIND}:
            raise InvalidWorkflowSearchError("unknown Workflow Kind")
        if request.renewal_period and request.workflow_kind not in {
            None,
            RENEWAL_OUTREACH_KIND,
        }:
            raise InvalidWorkflowSearchError("renewal_period is unsupported for this Kind")

    @staticmethod
    def _applied_filters(request: WorkflowSearchRequest) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("workflow_kind", request.workflow_kind),
                ("status", request.status),
                ("participant", request.participant),
                ("organization", request.organization),
                ("renewal_period", request.renewal_period),
            )
            if value is not None
        }

    @staticmethod
    def _facet(values: list[str]) -> WorkflowFacet:
        counts = sorted(Counter(values).items(), key=lambda item: (-item[1], item[0]))
        return WorkflowFacet(
            entries=tuple(
                WorkflowFacetEntry(value=value, count=count)
                for value, count in counts[:FACET_LIMIT]
            ),
            has_more=len(counts) > FACET_LIMIT,
        )

    def _facets(self, matches: list[_Candidate]) -> WorkflowSearchFacets:
        return WorkflowSearchFacets(
            status=self._facet([candidate.workflow.status for candidate in matches]),
            workflow_kind=self._facet([candidate.workflow.kind for candidate in matches]),
            organization=self._facet([candidate.organization for candidate in matches]),
            renewal_period=self._facet(
                [
                    str(candidate.workflow.input["renewal_period"])
                    for candidate in matches
                    if "renewal_period" in candidate.workflow.input
                ]
            ),
        )

    @staticmethod
    def _request_digest(request: WorkflowSearchRequest) -> str:
        normalized = {
            "query": WorkflowRetrieval._normalize_text(request.query),
            **WorkflowRetrieval._applied_filters(request),
        }
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

    @classmethod
    def _query_terms(cls, value: str) -> list[str]:
        return [
            term
            for term in cls._normalize_text(value).split()
            if term not in QUERY_NOISE_TERMS and len(term) > 1
        ]

    def _encode_cursor(self, candidate: _Candidate, request_digest: str) -> str:
        payload = {
            "v": CURSOR_VERSION,
            "request": request_digest,
            "score": candidate.score,
            "created_at": candidate.workflow.created_at.isoformat(),
            "workflow_id": str(candidate.workflow.id),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")

    def _cursor_start(
        self,
        matches: list[_Candidate],
        cursor: str | None,
        request_digest: str,
    ) -> int:
        if cursor is None:
            return 0
        payload = self._decode_cursor(cursor)
        if payload.get("v") != CURSOR_VERSION or payload.get("request") != request_digest:
            raise StaleWorkflowCursorError("cursor does not match this search")
        for index, candidate in enumerate(matches):
            if str(candidate.workflow.id) != payload.get("workflow_id"):
                continue
            if candidate.score != payload.get("score") or (
                candidate.workflow.created_at.isoformat() != payload.get("created_at")
            ):
                raise StaleWorkflowCursorError("cursor anchor changed")
            return index + 1
        raise StaleWorkflowCursorError("cursor anchor is unavailable")

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode())
            raw, signature = decoded[:-32], decoded[-32:]
            expected = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise InvalidWorkflowSearchError("invalid cursor signature")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise InvalidWorkflowSearchError("invalid cursor payload")
            return payload
        except (ValueError, json.JSONDecodeError) as exc:
            raise InvalidWorkflowSearchError("invalid cursor") from exc
