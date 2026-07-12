"""SQLAlchemy mappings for the V0 PostgreSQL Workflow protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


class WorkflowRow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')",
            name="status",
        ),
        sa.CheckConstraint(
            "corrects_workflow_id IS NULL OR corrects_workflow_id <> id",
            name="not_self_correction",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    objective: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    organization_party_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("parties.id", name="fk_workflows_organization_party"),
        nullable=False,
    )
    corrects_workflow_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflows.id", name="fk_workflows_correction"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class WorkflowJobRow(Base):
    __tablename__ = "workflow_jobs"
    __table_args__ = (
        sa.UniqueConstraint("workflow_id", "id", name="uq_workflow_jobs_workflow_id_id"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "revises_job_id"],
            ["workflow_jobs.workflow_id", "workflow_jobs.id"],
            name="fk_workflow_jobs_revision",
        ),
        sa.CheckConstraint(
            "status IN ('waiting', 'queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        sa.CheckConstraint("attempts <= max_attempts", name="attempt_budget"),
        sa.CheckConstraint(
            "(status = 'succeeded' AND output IS NOT NULL) "
            "OR (status <> 'succeeded' AND output IS NULL)",
            name="output_matches_status",
        ),
        sa.CheckConstraint(
            "revises_job_id IS NULL OR revises_job_id <> id",
            name="not_self_revision",
        ),
        sa.Index(
            "uq_workflow_jobs_revises_job_id",
            "revises_job_id",
            unique=True,
            postgresql_where=sa.text("revises_job_id IS NOT NULL"),
        ),
        sa.Index(
            "ix_workflow_jobs_claim",
            "available_at",
            "created_at",
            "id",
            postgresql_where=sa.text("status = 'queued'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflows.id", name="fk_workflow_jobs_workflow"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    revises_job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class WorkflowJobDependencyRow(Base):
    __tablename__ = "workflow_job_dependencies"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["workflow_id", "job_id"],
            ["workflow_jobs.workflow_id", "workflow_jobs.id"],
            name="fk_workflow_job_dependencies_job",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "depends_on_job_id"],
            ["workflow_jobs.workflow_id", "workflow_jobs.id"],
            name="fk_workflow_job_dependencies_prerequisite",
        ),
        sa.CheckConstraint("job_id <> depends_on_job_id", name="not_self_dependency"),
        sa.Index(
            "ix_workflow_job_dependencies_reverse",
            "workflow_id",
            "depends_on_job_id",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    depends_on_job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)


class WorkflowJobRunRow(Base):
    __tablename__ = "workflow_job_runs"
    __table_args__ = (
        sa.UniqueConstraint(
            "workflow_id",
            "job_id",
            "id",
            name="uq_workflow_job_runs_workflow_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "job_id"],
            ["workflow_jobs.workflow_id", "workflow_jobs.id"],
            name="fk_workflow_job_runs_job",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'abandoned')",
            name="status",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND result IS NULL) "
            "OR (status IN ('succeeded', 'failed') AND finished_at IS NOT NULL "
            "AND result IS NOT NULL) "
            "OR (status IN ('cancelled', 'abandoned') AND finished_at IS NOT NULL "
            "AND result IS NULL)",
            name="terminal_shape",
        ),
        sa.Index(
            "uq_workflow_job_runs_running_job",
            "job_id",
            unique=True,
            postgresql_where=sa.text("status = 'running'"),
        ),
        sa.Index(
            "ix_workflow_job_runs_lease",
            "lease_expires_at",
            postgresql_where=sa.text("status = 'running'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    worker_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    runtime_instance_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    application_build: Mapped[str] = mapped_column(sa.Text, nullable=False)
    adapter_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    provider_tool_version: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class InteractionCauseRow(Base):
    """Authoritative durable human interaction referenced by domain evidence."""

    __tablename__ = "interaction_causes"
    __table_args__ = (
        sa.CheckConstraint("cause_type IN ('message', 'ui_action')", name="cause_type"),
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    cause_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_party_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("parties.id", name="fk_interaction_causes_actor_party"),
        nullable=False,
    )
    content_digest: Mapped[str] = mapped_column(sa.Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class WorkflowEventRow(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        sa.UniqueConstraint("workflow_id", "id", name="uq_workflow_events_workflow_id_id"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "job_id"],
            ["workflow_jobs.workflow_id", "workflow_jobs.id"],
            name="fk_workflow_events_job",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "job_id", "run_id"],
            [
                "workflow_job_runs.workflow_id",
                "workflow_job_runs.job_id",
                "workflow_job_runs.id",
            ],
            name="fk_workflow_events_run",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "approval_grant_id"],
            ["workflow_events.workflow_id", "workflow_events.id"],
            name="fk_workflow_events_approval_grant",
        ),
        sa.CheckConstraint("run_id IS NULL OR job_id IS NOT NULL", name="run_requires_job"),
        sa.CheckConstraint(
            "event_type NOT IN ('approval_granted', 'external_effect_dispatch_started') "
            "OR job_id IS NOT NULL",
            name="approval_and_dispatch_require_job",
        ),
        sa.CheckConstraint(
            "event_type <> 'approval_invalidated' OR approval_grant_id IS NOT NULL",
            name="invalidation_requires_grant",
        ),
        sa.Index("ix_workflow_events_timeline", "workflow_id", "occurred_at", "id"),
        sa.Index("ix_workflow_events_job", "workflow_id", "job_id", "occurred_at"),
        sa.Index("ix_workflow_events_run", "workflow_id", "run_id", "occurred_at"),
        sa.Index(
            "uq_workflow_events_workflow_proposed",
            "workflow_id",
            unique=True,
            postgresql_where=sa.text("event_type = 'workflow_jobs_proposed'"),
        ),
        sa.Index(
            "uq_workflow_events_dispatch_job",
            "job_id",
            unique=True,
            postgresql_where=sa.text("event_type = 'external_effect_dispatch_started'"),
        ),
        sa.Index(
            "uq_workflow_events_approval_invalidation",
            "approval_grant_id",
            unique=True,
            postgresql_where=sa.text("event_type = 'approval_invalidated'"),
        ),
        sa.Index(
            "uq_workflow_events_approval_cause",
            "cause_type",
            "cause_id",
            unique=True,
            postgresql_where=sa.text("event_type = 'approval_granted'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflows.id", name="fk_workflow_events_workflow"),
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    cause_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    cause_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    approval_grant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["workflow_id", "workflow_event_id"],
            ["workflow_events.workflow_id", "workflow_events.id"],
            name="fk_notifications_workflow_event",
        ),
        sa.UniqueConstraint(
            "workflow_event_id",
            "kind",
            "destination_type",
            "destination_id",
            name="uq_notifications_delivery",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'delivering', 'delivered', 'failed')",
            name="status",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        sa.CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        sa.CheckConstraint("attempts <= max_attempts", name="attempt_budget"),
        sa.CheckConstraint(
            "(status = 'queued' AND claimed_by IS NULL AND lease_expires_at IS NULL "
            "AND delivered_at IS NULL AND delivered_by IS NULL) "
            "OR (status = 'delivering' AND claimed_by IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND delivered_at IS NULL "
            "AND delivered_by IS NULL) "
            "OR (status = 'delivered' AND claimed_by IS NULL AND lease_expires_at IS NULL "
            "AND delivered_at IS NOT NULL AND delivered_by IS NOT NULL) "
            "OR (status = 'failed' AND claimed_by IS NULL AND lease_expires_at IS NULL "
            "AND delivered_at IS NULL AND delivered_by IS NULL)",
            name="delivery_shape",
        ),
        sa.Index(
            "ix_notifications_claim",
            "available_at",
            "created_at",
            "id",
            postgresql_where=sa.text("status = 'queued'"),
        ),
        sa.Index(
            "ix_notifications_lease",
            "lease_expires_at",
            postgresql_where=sa.text("status = 'delivering'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflows.id", name="fk_notifications_workflow"),
        nullable=False,
    )
    workflow_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    destination_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    destination_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    claimed_by: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    delivered_by: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
