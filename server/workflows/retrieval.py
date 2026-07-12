"""Authorization-first Workflow search and bounded packet retrieval."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

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
)
from .models import (
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .retrieval_contracts import (
    WorkflowFacet,
    WorkflowFacetEntry,
    WorkflowInspectionContext,
    WorkflowPacket,
    WorkflowPacketApproval,
    WorkflowPacketDispatch,
    WorkflowPacketEvent,
    WorkflowPacketEventWindow,
    WorkflowPacketJob,
    WorkflowPacketRun,
    WorkflowPacketWorkflow,
    WorkflowParticipantSummary,
    WorkflowSearchFacets,
    WorkflowSearchPage,
    WorkflowSearchRequest,
    WorkflowSearchResult,
)

RENEWAL_OUTREACH_KIND = "renewal_outreach.v1"
CURSOR_VERSION = 1
FACET_LIMIT = 10
EVENT_LIMIT = 20
HIDDEN_EVENT_TYPES = frozenset({"lease_extended", "run_claimed", "run_started"})


@dataclass(frozen=True)
class _Candidate:
    workflow: WorkflowRow
    organization: str
    participants: tuple[WorkflowParticipantSummary, ...]
    score: int
    match_reasons: tuple[str, ...]


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
            rows = (await session.execute(self._authorized_workflows(context.actor_party_id))).all()
            workflow_ids = [workflow.id for workflow, _organization in rows]
            participants = await self._participants(session, workflow_ids)

        candidates = [
            self._candidate(workflow, organization, participants[workflow.id], request)
            for workflow, organization in rows
        ]
        matches = [candidate for candidate in candidates if candidate is not None]
        matches.sort(
            key=lambda candidate: (
                -candidate.score,
                -candidate.workflow.created_at.timestamp(),
                str(candidate.workflow.id),
            )
        )

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
                        WorkflowParticipantRoleRow.workflow_id,
                        PartyRow.id,
                        PartyRow.display_name,
                        WorkflowParticipantRoleRow.role,
                    )
                    .join(PartyRow, PartyRow.id == WorkflowParticipantRoleRow.party_id)
                    .where(
                        WorkflowParticipantRoleRow.workflow_id.in_(workflow_ids),
                        WorkflowParticipantRoleRow.revoked_at.is_(None),
                    )
                    .order_by(PartyRow.display_name, PartyRow.id, WorkflowParticipantRoleRow.role)
                )
            ).all()
            for workflow_id, party_id, name, role in rows:
                current = grouped[workflow_id].setdefault(party_id, (name, []))
                current[1].append(role)
        return {
            workflow_id: tuple(
                WorkflowParticipantSummary(
                    party_id=party_id,
                    name=name,
                    roles=tuple(roles),
                )
                for party_id, (name, roles) in parties.items()
            )
            for workflow_id, parties in grouped.items()
        } | {workflow_id: () for workflow_id in workflow_ids if workflow_id not in grouped}

    def _candidate(
        self,
        workflow: WorkflowRow,
        organization: str,
        participants: tuple[WorkflowParticipantSummary, ...],
        request: WorkflowSearchRequest,
    ) -> _Candidate | None:
        period = workflow.input.get("renewal_period")
        if request.workflow_kind and workflow.kind != request.workflow_kind:
            return None
        if request.status and workflow.status != request.status:
            return None
        if request.renewal_period and period != request.renewal_period:
            return None
        if request.organization and request.organization.casefold() not in organization.casefold():
            return None
        participant_names = [participant.name for participant in participants]
        if request.participant and not any(
            request.participant.casefold() in name.casefold() for name in participant_names
        ):
            return None

        searchable = " ".join(
            [
                str(workflow.id),
                workflow.objective,
                workflow.kind.replace("_", " ").replace(".v1", ""),
                workflow.status,
                organization,
                str(period or ""),
                *participant_names,
            ]
        ).casefold()
        query = " ".join(request.query.casefold().split())
        query_terms = query.split()
        if any(term not in searchable for term in query_terms):
            return None

        score = sum(5 for term in query_terms if term in searchable)
        reasons: list[str] = []
        if query == str(workflow.id).casefold():
            score += 1000
            reasons.append(f"exact workflow identifier: {workflow.id}")
        for name in participant_names:
            if name.casefold() in query or (
                request.participant and request.participant.casefold() == name.casefold()
            ):
                score += 200
                reasons.append(f"exact participant match: {name}")
                break
        if organization.casefold() in query or (
            request.organization and request.organization.casefold() == organization.casefold()
        ):
            score += 180
            reasons.append(f"organization matched: {organization}")
        kind_words = workflow.kind.replace("_", " ").split(".", 1)[0].casefold()
        if kind_words in query or request.workflow_kind == workflow.kind:
            score += 100
            reasons.append(f"workflow kind matched: {workflow.kind}")
        if query and query in workflow.objective.casefold():
            score += 80
            reasons.append("objective matched query")
        if request.status == workflow.status:
            score += 40
            reasons.append(f"status matched: {workflow.status}")
        if request.renewal_period == period:
            score += 40
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
            status=workflow.status,
            organization=candidate.organization,
            participants=candidate.participants,
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
        waiting_reasons: list[str] = []
        if job.status == "waiting":
            waiting_reasons.extend(f"dependency:{dependency_id}" for dependency_id in unresolved)
            if not unresolved and job.kind == "gmail.send_email.v1" and approval is None:
                waiting_reasons.append("exact_approval")
            if (
                latest_run is not None
                and latest_run.result
                and latest_run.result.get("outcome") == "uncertain"
            ):
                waiting_reasons.append("uncertain_external_effect")

        return WorkflowPacketJob(
            job_id=job.id,
            kind=job.kind,
            status=job.status,
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
                WorkflowPacketDispatch(started_at=dispatch.occurred_at, run_id=dispatch.run_id)
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
        error = result.get("error")
        if isinstance(error, dict):
            error_summary = str(error.get("message") or error.get("code") or "Run failed")
        elif error is None:
            error_summary = None
        else:
            error_summary = str(error)
        return WorkflowPacketRun(
            run_id=run.id,
            status=run.status,
            outcome=result.get("outcome"),
            error_summary=error_summary[:300] if error_summary else None,
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
            "query": " ".join(request.query.casefold().split()),
            **WorkflowRetrieval._applied_filters(request),
        }
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

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
